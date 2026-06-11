import os
import re
import traceback
import requests as http_requests
from flask import Blueprint, request, jsonify
from extensions import limiter
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, timezone
from db import get_conn, release_conn
from auth_utils import (
    hash_password, check_password, hash_token,
    generate_numeric_code, create_access_token,
    create_refresh_token, create_password_reset_token,
    build_auth_response,
)
from services.email_service import send_verification_email, send_reset_password_email
from config import Config
from routes.notifications import send_notification

auth_bp = Blueprint("auth", __name__)

RECAPTCHA_SECRET = os.environ.get("RECAPTCHA_SECRET_KEY", "6LcNABgtAAAAADD_n98GlHDQN_o9jy2DEWAQWVOW")

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

def verify_recaptcha(token):
    if not token:
        return True  # Don't block if no token
    try:
        res = http_requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": RECAPTCHA_SECRET, "response": token},
            timeout=5,
        )
        result = res.json()
        print("RECAPTCHA RESULT:", result)
        return result.get("success", False) and result.get("score", 0) >= 0.3
    except Exception as e:
        print("RECAPTCHA ERROR:", str(e))
        return True  # Don't block users if reCAPTCHA is down

def _validate(email=None, password=None, nom=None, prenom=None):
    if email is not None:
        if len(email) > 254:
            return "L'adresse email est trop longue (max 254 caractères)"
        if email and not _EMAIL_RE.match(email):
            return "Format d'email invalide"
    if password is not None and len(password) > 128:
        return "Le mot de passe est trop long (max 128 caractères)"
    if nom is not None and len(nom) > 100:
        return "Le nom est trop long (max 100 caractères)"
    if prenom is not None and len(prenom) > 100:
        return "Le prénom est trop long (max 100 caractères)"
    return None

def get_json():
    return request.get_json(silent=True) or {}

def utcnow():
    return datetime.utcnow()


@auth_bp.route("/register", methods=["POST"])
@limiter.limit("3 per minute")
def register():
    data       = get_json()
    if not verify_recaptcha(data.get("recaptcha_token")):
        return jsonify({"error": "Vérification anti-bot échouée. Réessaie."}), 400
    email      = (data.get("email") or "").strip().lower()
    password   = data.get("password") or ""
    nom        = (data.get("nom") or "").strip()
    prenom     = (data.get("prenom") or "").strip()
    niveau     = (data.get("niveau") or "").strip()
    filiere    = (data.get("filiere") or "").strip()
    ville      = (data.get("ville") or "").strip()
    type_ecole = (data.get("type_ecole") or "").strip()
    nom_ecole  = (data.get("nom_ecole") or "").strip()
    telephone  = (data.get("telephone") or "").strip()

    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis"}), 400
    if len(password) < 8:
        return jsonify({"error": "Le mot de passe doit contenir au moins 8 caractères"}), 400
    err = _validate(email=email, password=password, nom=nom, prenom=prenom)
    if err:
        return jsonify({"error": err}), 400

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            return jsonify({"error": "Email déjà utilisé"}), 409

        password_hash = hash_password(password)
        cur.execute("""
            INSERT INTO users (email, password_hash, role, is_email_verified,
                is_phone_verified, auth_provider, status, phone_number)
            VALUES (%s, %s, %s, FALSE, FALSE, 'email', 'active', %s)
            RETURNING id, email, role, is_email_verified, is_phone_verified, auth_provider
        """, (email, password_hash, "student", telephone or None))
        user = cur.fetchone()

        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'student_profiles'
        """)
        existing_cols = {row["column_name"] for row in cur.fetchall()}

        profile_data = {
            "user_id": user["id"],
            "nom":     nom     or None,
            "prenom":  prenom  or None,
            "niveau":  niveau  or None,
            "filiere": filiere or None,
        }
        if "ville"      in existing_cols: profile_data["ville"]      = ville      or None
        if "type_ecole" in existing_cols: profile_data["type_ecole"] = type_ecole or None
        if "nom_ecole"  in existing_cols: profile_data["nom_ecole"]  = nom_ecole  or None

        cols         = ", ".join(profile_data.keys())
        placeholders = ", ".join(["%s"] * len(profile_data))
        cur.execute(
            f"INSERT INTO student_profiles ({cols}) VALUES ({placeholders})",
            list(profile_data.values())
        )

        verification_code      = generate_numeric_code(6)
        verification_code_hash = hash_token(verification_code)
        expires_at = utcnow() + timedelta(minutes=Config.EMAIL_VERIFICATION_EXPIRES_MINUTES)

        cur.execute("""
            INSERT INTO email_verification_tokens (user_id, email, code, token_hash, is_used, expires_at)
            VALUES (%s, %s, %s, %s, FALSE, %s)
        """, (user["id"], email, verification_code, verification_code_hash, expires_at))

        conn.commit()

        try:
            send_verification_email(email, verification_code)
            print("Verification email sent to", email)
        except Exception as email_err:
            print("EMAIL ERROR (non-fatal):", str(email_err))
            # Continue anyway - token is saved

        try:
            send_notification(
                user["id"],
                "Bienvenue sur Najahi ! 🎓",
                f"Bonjour {prenom or 'là'} ! Ton compte est créé. Commence par vérifier ton email puis explore tes recommandations d'orientation.",
                type="success",
                link="/app/orientation",
            )
        except Exception:
            pass

        return jsonify({
            "message": "Compte créé. Vérifiez votre email avec le code envoyé.",
            "user": {
                "id":                str(user["id"]),
                "email":             user["email"],
                "role":              user["role"],
                "is_email_verified": user["is_email_verified"],
                "auth_provider":     user["auth_provider"],
            }
        }), 201

    except Exception as e:
        conn.rollback()
        print("REGISTER ERROR:", str(e))
        s = str(e)
        if "users_phone_number_key" in s:
            return jsonify({"error": "Ce numéro de téléphone est déjà utilisé."}), 409
        if "users_email_key" in s:
            return jsonify({"error": "Cette adresse email est déjà utilisée."}), 409
        return jsonify({"error": "Erreur serveur. Réessaie plus tard."}), 500
    finally:
        cur.close(); release_conn(conn)


@auth_bp.route("/verify-email", methods=["POST"])
def verify_email():
    data  = get_json()
    email = (data.get("email") or "").strip().lower()
    code  = (data.get("code") or "").strip()

    if not email or not code:
        return jsonify({"error": "Email et code requis"}), 400

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT evt.id, evt.user_id, evt.code, evt.is_used, evt.expires_at, u.email
            FROM email_verification_tokens evt
            JOIN users u ON u.id = evt.user_id
            WHERE evt.email = %s
            ORDER BY evt.created_at DESC
            LIMIT 1
        """, (email,))
        row = cur.fetchone()

        if not row:
            return jsonify({"error": "Code invalide"}), 400
        if row["is_used"]:
            return jsonify({"error": "Code déjà utilisé"}), 400
        if row["code"] != code:
            return jsonify({"error": "Code invalide"}), 400

        # Compare sans timezone
        expires_at = row["expires_at"]
        if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=None)
        if expires_at < utcnow():
            return jsonify({"error": "Code expiré"}), 400

        cur.execute(
            "UPDATE users SET is_email_verified = TRUE, updated_at = NOW() WHERE id = %s",
            (row["user_id"],)
        )
        cur.execute(
            "UPDATE email_verification_tokens SET is_used = TRUE WHERE id = %s",
            (row["id"],)
        )
        conn.commit()

        return jsonify({"message": "Email vérifié avec succès"}), 200

    except Exception as e:
        conn.rollback()
        print("VERIFY EMAIL ERROR:", str(e))
        return jsonify({"error": "Erreur serveur", "details": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


@auth_bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    data  = get_json()
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Email requis"}), 400

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if not user:
            return jsonify({"message": "Si ce compte existe, un code a été envoyé."}), 200

        verification_code      = generate_numeric_code(6)
        verification_code_hash = hash_token(verification_code)
        expires_at = utcnow() + timedelta(minutes=Config.EMAIL_VERIFICATION_EXPIRES_MINUTES)

        cur.execute("""
            INSERT INTO email_verification_tokens (user_id, email, code, token_hash, is_used, expires_at)
            VALUES (%s, %s, %s, %s, FALSE, %s)
        """, (user["id"], email, verification_code, verification_code_hash, expires_at))
        conn.commit()

        try:
            send_verification_email(email, verification_code)
        except Exception as e:
            print("RESEND EMAIL ERROR:", e)

        return jsonify({"message": "Code renvoyé."}), 200

    except Exception as e:
        conn.rollback()
        print("RESEND ERROR:", str(e))
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


@auth_bp.route("/login", methods=["POST"])
@limiter.limit("5 per minute")
def login():
    data     = get_json()
    if not verify_recaptcha(data.get("recaptcha_token")):
        return jsonify({"error": "Vérification anti-bot échouée. Réessaie."}), 400
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis"}), 400
    err = _validate(email=email, password=password)
    if err:
        return jsonify({"error": err}), 400

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT id, email, password_hash, role, is_email_verified,
                   is_phone_verified, auth_provider, phone_number
            FROM users WHERE email = %s
        """, (email,))
        user = cur.fetchone()

        if not user or not user["password_hash"]:
            return jsonify({"error": "Identifiants invalides"}), 401
        if not check_password(password, user["password_hash"]):
            return jsonify({"error": "Identifiants invalides"}), 401

        access_token = create_access_token(user["id"], user["email"], user["role"])
        raw_refresh_token, refresh_token_hash, refresh_expires_at = create_refresh_token(user["id"])

        cur.execute("""
            INSERT INTO user_sessions (user_id, refresh_token_hash, ip_address, user_agent, expires_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            user["id"], refresh_token_hash,
            request.remote_addr, request.headers.get("User-Agent"),
            refresh_expires_at
        ))
        now_utc = datetime.now(timezone.utc)
        cur.execute(
            "UPDATE users SET last_login_at = %s, updated_at = %s WHERE id = %s",
            (now_utc, now_utc, user["id"])
        )
        conn.commit()

        return jsonify(build_auth_response(user, access_token, raw_refresh_token)), 200

    except Exception as e:
        conn.rollback()
        print("LOGIN ERROR:", str(e))
        return jsonify({"error": "Erreur serveur", "details": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


@auth_bp.route("/refresh", methods=["POST"])
def refresh():
    data          = get_json()
    # Accept token from JSON body or Authorization: Bearer header
    refresh_token = data.get("refresh_token") or ""
    if not refresh_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            refresh_token = auth_header.split(" ", 1)[1].strip()
    if not refresh_token:
        return jsonify({"error": "Refresh token requis"}), 400

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        refresh_token_hash = hash_token(refresh_token)
        cur.execute("""
            SELECT s.id AS session_id, s.user_id, s.expires_at, s.is_revoked,
                   u.id, u.email, u.role, u.is_email_verified,
                   u.is_phone_verified, u.auth_provider, u.phone_number
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.refresh_token_hash = %s
            ORDER BY s.created_at DESC LIMIT 1
        """, (refresh_token_hash,))
        session = cur.fetchone()

        if not session:
            return jsonify({"error": "Refresh token invalide"}), 401

        session_expires = session["expires_at"]
        if hasattr(session_expires, 'tzinfo') and session_expires.tzinfo is not None:
            session_expires = session_expires.replace(tzinfo=None)
        if session["is_revoked"] or session_expires < utcnow():
            return jsonify({"error": "Session expirée ou révoquée"}), 401

        new_access_token = create_access_token(
            session["user_id"], session["email"], session["role"]
        )
        new_raw_refresh_token, new_refresh_token_hash, new_refresh_expires_at = \
            create_refresh_token(session["user_id"])

        cur.execute(
            "UPDATE user_sessions SET is_revoked = TRUE WHERE id = %s",
            (session["session_id"],)
        )
        cur.execute("""
            INSERT INTO user_sessions (user_id, refresh_token_hash, ip_address, user_agent, expires_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            session["user_id"], new_refresh_token_hash,
            request.remote_addr, request.headers.get("User-Agent"),
            new_refresh_expires_at
        ))
        conn.commit()

        user = {
            "id":                session["user_id"],
            "email":             session["email"],
            "role":              session["role"],
            "is_email_verified": session["is_email_verified"],
            "is_phone_verified": session["is_phone_verified"],
            "auth_provider":     session["auth_provider"],
            "phone_number":      session["phone_number"],
        }
        return jsonify(build_auth_response(user, new_access_token, new_raw_refresh_token)), 200

    except Exception as e:
        conn.rollback()
        print("REFRESH ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur", "details": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    data          = get_json()
    refresh_token = data.get("refresh_token") or ""
    if not refresh_token:
        return jsonify({"message": "Déconnexion réussie"}), 200

    conn = get_conn()
    cur  = conn.cursor()
    try:
        refresh_token_hash = hash_token(refresh_token)
        cur.execute(
            "UPDATE user_sessions SET is_revoked = TRUE WHERE refresh_token_hash = %s",
            (refresh_token_hash,)
        )
        conn.commit()
        return jsonify({"message": "Déconnexion réussie"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": "Erreur serveur", "details": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


@auth_bp.route("/forgot-password", methods=["POST"])
@limiter.limit("3 per hour")
def forgot_password():
    data  = get_json()
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Email requis"}), 400
    err = _validate(email=email)
    if err:
        return jsonify({"error": err}), 400

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT id, email FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

        if user:
            raw_reset_token, reset_token_hash, expires_at = create_password_reset_token()
            cur.execute("""
                INSERT INTO password_reset_tokens (user_id, token_hash, is_used, expires_at)
                VALUES (%s, %s, FALSE, %s)
            """, (user["id"], reset_token_hash, expires_at))
            conn.commit()
            try:
                send_reset_password_email(user["email"], raw_reset_token)
                print("Reset email sent to", email)
            except Exception as email_err:
                print("EMAIL ERROR (non-fatal):", str(email_err))
                # Continue anyway - token is saved

        return jsonify({
            "message": "Si un compte existe avec cet email, un lien a été envoyé."
        }), 200

    except Exception as e:
        conn.rollback()
        print("FORGOT PASSWORD ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur", "details": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    data         = get_json()
    token        = (data.get("token") or "").strip()
    new_password = data.get("password") or ""

    if not token or not new_password:
        return jsonify({"error": "Token et mot de passe requis"}), 400
    if len(new_password) < 8:
        return jsonify({"error": "Le mot de passe doit contenir au moins 8 caractères"}), 400

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        token_hash = hash_token(token)
        cur.execute("""
            SELECT id, user_id, is_used, expires_at
            FROM password_reset_tokens
            WHERE token_hash = %s
            ORDER BY created_at DESC LIMIT 1
        """, (token_hash,))
        row = cur.fetchone()

        if not row:
            return jsonify({"error": "Lien invalide ou expiré"}), 400
        if row["is_used"]:
            return jsonify({"error": "Lien déjà utilisé"}), 400

        expires_at = row["expires_at"]
        if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=None)
        if expires_at < utcnow():
            return jsonify({"error": "Lien expiré"}), 400

        new_password_hash = hash_password(new_password)
        cur.execute(
            "UPDATE users SET password_hash = %s, updated_at = NOW() WHERE id = %s",
            (new_password_hash, row["user_id"])
        )
        cur.execute(
            "UPDATE password_reset_tokens SET is_used = TRUE WHERE id = %s",
            (row["id"],)
        )
        cur.execute(
            "UPDATE user_sessions SET is_revoked = TRUE WHERE user_id = %s",
            (row["user_id"],)
        )
        conn.commit()

        return jsonify({"message": "Mot de passe réinitialisé avec succès"}), 200

    except Exception as e:
        conn.rollback()
        print("RESET PASSWORD ERROR:", str(e))
        return jsonify({"error": "Erreur serveur", "details": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)