from flask import Blueprint, request, jsonify, redirect
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from psycopg2.extras import RealDictCursor
import requests as http_requests
import os

from db import get_conn, release_conn
from auth_utils import (
    create_access_token, create_refresh_token,
    build_auth_response, utcnow
)
from config import Config

google_auth_bp = Blueprint("google_auth", __name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

@google_auth_bp.route("/google", methods=["GET"])
def google_login():
    from urllib.parse import urlencode
    params = {
        "client_id": Config.GOOGLE_CLIENT_ID,
        "redirect_uri": Config.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return redirect(url)


@google_auth_bp.route("/google/callback", methods=["GET"])
def google_callback():
    code = request.args.get("code")
    if not code:
        return redirect(f"{Config.FRONTEND_URL}/login?error=no_code")

    # Exchange code for tokens
    token_res = http_requests.post(GOOGLE_TOKEN_URL, data={
        "code": code,
        "client_id": Config.GOOGLE_CLIENT_ID,
        "client_secret": Config.GOOGLE_CLIENT_SECRET,
        "redirect_uri": Config.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    })

    if not token_res.ok:
        return redirect(f"{Config.FRONTEND_URL}/login?error=token_failed")

    tokens = token_res.json()
    access_token = tokens.get("access_token")

    # Get user info
    userinfo_res = http_requests.get(GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"}
    )

    if not userinfo_res.ok:
        return redirect(f"{Config.FRONTEND_URL}/login?error=userinfo_failed")

    userinfo = userinfo_res.json()
    email    = userinfo.get("email", "").lower()
    prenom   = userinfo.get("given_name", "")
    nom      = userinfo.get("family_name", "")
    avatar   = userinfo.get("picture", "")
    google_id = userinfo.get("sub", "")

    if not email:
        return redirect(f"{Config.FRONTEND_URL}/login?error=no_email")

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Check if user exists
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

        if not user:
            # Create new user
            cur.execute("""
                INSERT INTO users (
                    email, role, is_email_verified, is_phone_verified,
                    auth_provider, status, google_id, avatar_url
                )
                VALUES (%s, %s, TRUE, FALSE, 'google', 'active', %s, %s)
                RETURNING id, email, role, is_email_verified, is_phone_verified, auth_provider
            """, (email, "student", google_id, avatar))
            user = cur.fetchone()

            # Create student profile
            cur.execute("""
                INSERT INTO student_profiles (user_id, nom, prenom)
                VALUES (%s, %s, %s)
            """, (user["id"], nom or None, prenom or None))

        else:
            # Update google_id and avatar if missing
            cur.execute("""
                UPDATE users SET google_id = COALESCE(google_id, %s),
                avatar_url = COALESCE(avatar_url, %s),
                is_email_verified = TRUE,
                updated_at = NOW()
                WHERE id = %s
            """, (google_id, avatar, user["id"]))

        # Create session
        jwt_token = create_access_token(user["id"], user["email"], user["role"])
        raw_refresh, refresh_hash, refresh_expires = create_refresh_token(user["id"])

        cur.execute("""
            INSERT INTO user_sessions (user_id, refresh_token_hash, ip_address, user_agent, expires_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (user["id"], refresh_hash, request.remote_addr,
              request.headers.get("User-Agent"), refresh_expires))

        cur.execute(
            "UPDATE users SET last_login_at = NOW(), updated_at = NOW() WHERE id = %s",
            (user["id"],)
        )

        conn.commit()

        # Redirect to frontend with tokens
        from urllib.parse import urlencode
        params = urlencode({
            "access_token": jwt_token,
            "refresh_token": raw_refresh,
            "email": email,
        })
        return redirect(f"{Config.FRONTEND_URL}/auth/callback?{params}")

    except Exception as e:
        conn.rollback()
        return redirect(f"{Config.FRONTEND_URL}/login?error={str(e)}")
    finally:
        cur.close()
        release_conn(conn)