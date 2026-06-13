import json
import traceback
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
from middleware import token_required

admin_bp = Blueprint("admin", __name__)


def admin_required(fn):
    @wraps(fn)
    @token_required
    def wrapper(*args, **kwargs):
        if g.current_user.get("role") != "admin":
            return jsonify({"error": "Accès refusé — admin uniquement"}), 403
        return fn(*args, **kwargs)
    return wrapper


_SCHOOLS_TABLE_OK  = False
_SETTINGS_TABLE_OK = False


def _ensure_schools_table(conn, cur):
    global _SCHOOLS_TABLE_OK
    if _SCHOOLS_TABLE_OK:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS etablissements (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            nom           VARCHAR(300) NOT NULL,
            sigle         VARCHAR(50),
            categorie     VARCHAR(150),
            secteur       VARCHAR(100),
            ville         VARCHAR(150),
            site_web      TEXT,
            telephone     VARCHAR(50),
            adresse       TEXT,
            frais_annuels NUMERIC(10,2),
            note_bac_min  NUMERIC(4,2),
            filieres      TEXT[],
            debouches     TEXT[],
            concours      TEXT[],
            duree_etudes  VARCHAR(50),
            groupe        VARCHAR(100),
            created_at    TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    _SCHOOLS_TABLE_OK = True


def _ensure_settings_table(conn, cur):
    global _SETTINGS_TABLE_OK
    if _SETTINGS_TABLE_OK:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_settings (
            key        VARCHAR(100) PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        INSERT INTO admin_settings (key, value) VALUES
            ('maintenance_mode',  'false'),
            ('registration_open', 'true'),
            ('contact_email',     'contact@najahi.ma'),
            ('support_phone',     '')
        ON CONFLICT (key) DO NOTHING
    """)
    conn.commit()
    _SETTINGS_TABLE_OK = True


def _ensure_forum_moderation_cols(conn, cur):
    cur.execute("ALTER TABLE forum_posts ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE")
    cur.execute("ALTER TABLE forum_posts ADD COLUMN IF NOT EXISTS is_locked BOOLEAN DEFAULT FALSE")
    conn.commit()


def _school_row(r):
    def arr(v):
        if v is None:
            return []
        if isinstance(v, list):
            return v
        return list(v)
    return {
        "id":            str(r["id"]),
        "nom":           r["nom"] or "",
        "sigle":         r["sigle"] or "",
        "categorie":     r["categorie"] or "",
        "secteur":       r["secteur"] or "",
        "ville":         r["ville"] or "",
        "site_web":      r["site_web"] or "",
        "telephone":     r["telephone"] or "",
        "adresse":       r["adresse"] or "",
        "frais_annuels": float(r["frais_annuels"]) if r["frais_annuels"] is not None else None,
        "note_bac_min":  float(r["note_bac_min"])  if r["note_bac_min"]  is not None else None,
        "filieres":      arr(r["filieres"]),
        "debouches":     arr(r["debouches"]),
        "concours":      arr(r["concours"]),
        "duree_etudes":  r["duree_etudes"] or "",
        "groupe":        r["groupe"] or "",
        "created_at":    r["created_at"].isoformat() if r["created_at"] else None,
    }


# ── GET /api/admin/stats ─────────────────────────────────────────────────────
@admin_bp.route("/stats", methods=["GET"])
@admin_required
def get_stats():
    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM users WHERE last_login_at >= NOW() - INTERVAL '24 hours'")
        active_today = cur.fetchone()[0]

        try:
            cur.execute("SELECT COUNT(*) FROM orientation_results")
            total_orientations = cur.fetchone()[0]
        except Exception:
            conn.rollback()
            total_orientations = 0

        try:
            cur.execute("SELECT COUNT(*) FROM schools_chat_history")
            total_chats = cur.fetchone()[0]
        except Exception:
            conn.rollback()
            total_chats = 0

        try:
            cur.execute("SELECT COUNT(*) FROM solo_study_sessions WHERE ended_at IS NOT NULL")
            total_sessions = cur.fetchone()[0]
        except Exception:
            conn.rollback()
            total_sessions = 0

        cur.execute("SELECT COUNT(*) FROM users WHERE created_at >= NOW() - INTERVAL '7 days'")
        new_this_week = cur.fetchone()[0]

        try:
            cur.execute("SELECT COUNT(*) FROM temoignages WHERE is_approved = FALSE")
            pending_temoignages = cur.fetchone()[0]
        except Exception:
            conn.rollback()
            pending_temoignages = 0

        try:
            cur.execute("SELECT COUNT(*) FROM shared_documents WHERE is_approved = FALSE")
            pending_documents = cur.fetchone()[0]
        except Exception:
            conn.rollback()
            pending_documents = 0

        return jsonify({
            "total_users":          int(total_users),
            "active_today":         int(active_today),
            "total_orientations":   int(total_orientations),
            "total_chats":          int(total_chats),
            "total_sessions":       int(total_sessions),
            "new_this_week":        int(new_this_week),
            "pending_temoignages":  int(pending_temoignages),
            "pending_documents":    int(pending_documents),
        }), 200
    except Exception:
        print("ADMIN STATS ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/admin/registrations ─────────────────────────────────────────────
@admin_bp.route("/registrations", methods=["GET"])
@admin_required
def get_registrations():
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT DATE(created_at AT TIME ZONE 'UTC') AS day, COUNT(*) AS count
            FROM users
            WHERE created_at >= NOW() - INTERVAL '30 days'
            GROUP BY day
            ORDER BY day
        """)
        rows = cur.fetchall()
        data = [{"day": str(r["day"]), "count": int(r["count"])} for r in rows]
        return jsonify({"registrations": data}), 200
    except Exception:
        print("ADMIN REGISTRATIONS ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/admin/users ─────────────────────────────────────────────────────
@admin_bp.route("/users", methods=["GET"])
@admin_required
def get_users():
    page    = max(1, int(request.args.get("page", 1)))
    limit   = min(100, max(1, int(request.args.get("limit", 20))))
    search  = (request.args.get("search") or "").strip()
    offset  = (page - 1) * limit

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        where  = ""
        params = []
        if search:
            where = "WHERE (u.email ILIKE %s OR sp.prenom ILIKE %s OR sp.nom ILIKE %s)"
            s = f"%{search}%"
            params = [s, s, s]

        cur.execute(f"""
            SELECT COUNT(*) FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            {where}
        """, params)
        total = cur.fetchone()["count"]

        cur.execute(f"""
            SELECT u.id, u.email, u.role, u.is_email_verified, u.auth_provider,
                   u.created_at, u.last_login_at, u.status,
                   sp.prenom, sp.nom, sp.avatar_url
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            {where}
            ORDER BY u.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])

        rows = cur.fetchall()
        users = []
        for r in rows:
            users.append({
                "id":               str(r["id"]),
                "email":            r["email"],
                "role":             r["role"],
                "is_email_verified": r["is_email_verified"],
                "auth_provider":    r["auth_provider"],
                "created_at":       r["created_at"].isoformat() if r["created_at"] else None,
                "last_login_at":    r["last_login_at"].isoformat() if r["last_login_at"] else None,
                "status":           r["status"],
                "prenom":           r["prenom"] or "",
                "nom":              r["nom"] or "",
                "avatar_url":       r["avatar_url"] or "",
            })

        return jsonify({
            "users": users,
            "total": int(total),
            "page":  page,
            "pages": max(1, -(-int(total) // limit)),
        }), 200
    except Exception:
        print("ADMIN USERS ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── PUT /api/admin/users/<id>/role ───────────────────────────────────────────
@admin_bp.route("/users/<user_id>/role", methods=["PUT"])
@admin_required
def update_role(user_id):
    role = (request.get_json(silent=True) or {}).get("role", "")
    if role not in ("student", "admin"):
        return jsonify({"error": "Rôle invalide (student ou admin)"}), 400
    if str(user_id) == str(g.current_user["id"]):
        return jsonify({"error": "Impossible de modifier son propre rôle"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET role = %s WHERE id = %s", (role, user_id))
        if cur.rowcount == 0:
            return jsonify({"error": "Utilisateur introuvable"}), 404
        conn.commit()
        return jsonify({"ok": True, "role": role}), 200
    except Exception:
        conn.rollback()
        print("ADMIN ROLE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── DELETE /api/admin/users/<id> ─────────────────────────────────────────────
@admin_bp.route("/users/<user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    if str(user_id) == str(g.current_user["id"]):
        return jsonify({"error": "Impossible de supprimer son propre compte"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        if cur.rowcount == 0:
            return jsonify({"error": "Utilisateur introuvable"}), 404
        conn.commit()
        return jsonify({"ok": True}), 200
    except Exception:
        conn.rollback()
        print("ADMIN DELETE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/admin/orientations ──────────────────────────────────────────────
@admin_bp.route("/orientations", methods=["GET"])
@admin_required
def get_orientations():
    page   = max(1, int(request.args.get("page", 1)))
    limit  = min(100, max(1, int(request.args.get("limit", 20))))
    offset = (page - 1) * limit

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT COUNT(*) FROM orientation_results")
        total = cur.fetchone()["count"]

        cur.execute("""
            SELECT o.id, o.created_at, o.top_schools,
                   u.email, sp.prenom, sp.nom
            FROM orientation_results o
            JOIN users u ON u.id = o.user_id
            LEFT JOIN student_profiles sp ON sp.user_id = o.user_id
            ORDER BY o.created_at DESC
            LIMIT %s OFFSET %s
        """, (limit, offset))

        rows = cur.fetchall()
        results = []
        for r in rows:
            results.append({
                "id":         str(r["id"]),
                "email":      r["email"],
                "prenom":     r["prenom"] or "",
                "nom":        r["nom"] or "",
                "top_schools": r["top_schools"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            })

        return jsonify({"results": results, "total": int(total), "page": page}), 200
    except Exception:
        print("ADMIN ORIENT ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/admin/activity ──────────────────────────────────────────────────
@admin_bp.route("/activity", methods=["GET"])
@admin_required
def get_activity():
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        events = []

        cur.execute("""
            SELECT u.email, sp.prenom, u.last_login_at AS ts, 'login' AS type
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.last_login_at IS NOT NULL
            ORDER BY u.last_login_at DESC LIMIT 20
        """)
        events += [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT u.email, sp.prenom, u.created_at AS ts, 'register' AS type
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            ORDER BY u.created_at DESC LIMIT 10
        """)
        events += [dict(r) for r in cur.fetchall()]

        try:
            cur.execute("""
                SELECT u.email, sp.prenom, o.created_at AS ts, 'orientation' AS type
                FROM orientation_results o
                JOIN users u ON u.id = o.user_id
                LEFT JOIN student_profiles sp ON sp.user_id = o.user_id
                ORDER BY o.created_at DESC LIMIT 10
            """)
            events += [dict(r) for r in cur.fetchall()]
        except Exception:
            print("ADMIN ACTIVITY (orientations) ERROR:", traceback.format_exc())
            conn.rollback()

        def safe_ts(e):
            ts = e.get("ts")
            if ts is None:
                return ""
            if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                return ts.astimezone(timezone.utc).replace(tzinfo=None)
            return ts

        events.sort(key=safe_ts, reverse=True)
        for e in events:
            if e["ts"]:
                e["ts"] = e["ts"].isoformat()

        return jsonify({"activity": events[:40]}), 200
    except Exception:
        print("ADMIN ACTIVITY ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/admin/schools ───────────────────────────────────────────────────
@admin_bp.route("/schools", methods=["GET"])
@admin_required
def list_schools():
    page   = max(1, int(request.args.get("page", 1)))
    limit  = min(100, max(1, int(request.args.get("limit", 20))))
    search = (request.args.get("search") or "").strip()
    offset = (page - 1) * limit

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schools_table(conn, cur)

        where, params = "", []
        if search:
            where = "WHERE (nom ILIKE %s OR ville ILIKE %s OR categorie ILIKE %s OR sigle ILIKE %s)"
            s = f"%{search}%"
            params = [s, s, s, s]

        cur.execute(f"SELECT COUNT(*) FROM etablissements {where}", params)
        total = cur.fetchone()["count"]

        cur.execute(f"""
            SELECT * FROM etablissements {where}
            ORDER BY nom ASC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])

        schools = [_school_row(r) for r in cur.fetchall()]
        return jsonify({
            "schools": schools,
            "total":   int(total),
            "page":    page,
            "pages":   max(1, -(-int(total) // limit)),
        }), 200
    except Exception:
        print("ADMIN SCHOOLS LIST ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/admin/schools ──────────────────────────────────────────────────
@admin_bp.route("/schools", methods=["POST"])
@admin_required
def create_school():
    data = request.get_json(silent=True) or {}
    nom  = (data.get("nom") or "").strip()
    if not nom:
        return jsonify({"error": "Le nom est obligatoire"}), 400

    def to_arr(v):
        if not v:
            return None
        if isinstance(v, list):
            return [x for x in v if x]
        return [x.strip() for x in str(v).split(",") if x.strip()]

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schools_table(conn, cur)

        cur.execute("""
            INSERT INTO etablissements
              (nom, sigle, categorie, secteur, ville, site_web, telephone, adresse,
               frais_annuels, note_bac_min, filieres, debouches, concours, duree_etudes, groupe)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
        """, (
            nom,
            data.get("sigle")         or None,
            data.get("categorie")     or None,
            data.get("secteur")       or None,
            data.get("ville")         or None,
            data.get("site_web")      or None,
            data.get("telephone")     or None,
            data.get("adresse")       or None,
            data.get("frais_annuels") or None,
            data.get("note_bac_min")  or None,
            to_arr(data.get("filieres")),
            to_arr(data.get("debouches")),
            to_arr(data.get("concours")),
            data.get("duree_etudes")  or None,
            data.get("groupe")        or None,
        ))
        row = cur.fetchone()
        conn.commit()
        return jsonify(_school_row(row)), 201
    except Exception:
        conn.rollback()
        print("ADMIN SCHOOLS CREATE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── PUT /api/admin/schools/<id> ──────────────────────────────────────────────
@admin_bp.route("/schools/<school_id>", methods=["PUT"])
@admin_required
def update_school(school_id):
    data = request.get_json(silent=True) or {}
    nom  = (data.get("nom") or "").strip()
    if not nom:
        return jsonify({"error": "Le nom est obligatoire"}), 400

    def to_arr(v):
        if not v:
            return None
        if isinstance(v, list):
            return [x for x in v if x]
        return [x.strip() for x in str(v).split(",") if x.strip()]

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schools_table(conn, cur)

        cur.execute("""
            UPDATE etablissements SET
              nom=%s, sigle=%s, categorie=%s, secteur=%s, ville=%s,
              site_web=%s, telephone=%s, adresse=%s, frais_annuels=%s,
              note_bac_min=%s, filieres=%s, debouches=%s, concours=%s,
              duree_etudes=%s, groupe=%s
            WHERE id=%s
            RETURNING *
        """, (
            nom,
            data.get("sigle")         or None,
            data.get("categorie")     or None,
            data.get("secteur")       or None,
            data.get("ville")         or None,
            data.get("site_web")      or None,
            data.get("telephone")     or None,
            data.get("adresse")       or None,
            data.get("frais_annuels") or None,
            data.get("note_bac_min")  or None,
            to_arr(data.get("filieres")),
            to_arr(data.get("debouches")),
            to_arr(data.get("concours")),
            data.get("duree_etudes")  or None,
            data.get("groupe")        or None,
            school_id,
        ))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "École introuvable"}), 404
        conn.commit()
        return jsonify(_school_row(row)), 200
    except Exception:
        conn.rollback()
        print("ADMIN SCHOOLS UPDATE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── DELETE /api/admin/schools/<id> ───────────────────────────────────────────
@admin_bp.route("/schools/<school_id>", methods=["DELETE"])
@admin_required
def delete_school(school_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        _ensure_schools_table(conn, cur)
        cur.execute("DELETE FROM etablissements WHERE id = %s", (school_id,))
        if cur.rowcount == 0:
            return jsonify({"error": "École introuvable"}), 404
        conn.commit()
        return jsonify({"ok": True}), 200
    except Exception:
        conn.rollback()
        print("ADMIN SCHOOLS DELETE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/admin/settings ──────────────────────────────────────────────────
@admin_bp.route("/settings", methods=["GET"])
@admin_required
def get_settings():
    import os as _os
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_settings_table(conn, cur)
        cur.execute("SELECT key, value FROM admin_settings")
        rows = {r["key"]: r["value"] for r in cur.fetchall()}
        return jsonify({
            "maintenance_mode":  rows.get("maintenance_mode",  "false") == "true",
            "registration_open": rows.get("registration_open", "true")  == "true",
            "contact_email":     rows.get("contact_email",     ""),
            "support_phone":     rows.get("support_phone",     ""),
            "app_version":       "1.0.0",
            "environment":       _os.getenv("FLASK_ENV", "production"),
        }), 200
    except Exception:
        print("ADMIN SETTINGS GET ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── PUT /api/admin/settings ──────────────────────────────────────────────────
@admin_bp.route("/settings", methods=["PUT"])
@admin_required
def update_settings():
    data = request.get_json(silent=True) or {}
    allowed = {"maintenance_mode", "registration_open", "contact_email", "support_phone"}

    conn = get_conn()
    try:
        cur = conn.cursor()
        _ensure_settings_table(conn, cur)

        for key, raw in data.items():
            if key not in allowed:
                continue
            if isinstance(raw, bool):
                value = "true" if raw else "false"
            else:
                value = str(raw)
            cur.execute("""
                INSERT INTO admin_settings (key, value, updated_at) VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, (key, value))

        conn.commit()
        return jsonify({"ok": True}), 200
    except Exception:
        conn.rollback()
        print("ADMIN SETTINGS PUT ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/admin/forum/posts ───────────────────────────────────────────────
@admin_bp.route("/forum/posts", methods=["GET"])
@admin_required
def list_forum_posts():
    page   = max(1, int(request.args.get("page", 1)))
    limit  = min(100, max(1, int(request.args.get("limit", 30))))
    search = (request.args.get("search") or "").strip()
    offset = (page - 1) * limit

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            _ensure_forum_moderation_cols(conn, cur)
        except Exception:
            conn.rollback()

        where, params = "", []
        if search:
            where = "WHERE (fp.title ILIKE %s OR fp.content ILIKE %s OR u.email ILIKE %s)"
            s = f"%{search}%"
            params = [s, s, s]

        cur.execute(f"""
            SELECT COUNT(*) FROM forum_posts fp
            JOIN users u ON u.id = fp.user_id
            {where}
        """, params)
        total = cur.fetchone()["count"]

        cur.execute(f"""
            SELECT fp.id, fp.title, fp.content, fp.category, fp.school,
                   fp.likes, fp.views, fp.created_at,
                   COALESCE(fp.is_pinned, FALSE) AS is_pinned,
                   COALESCE(fp.is_locked, FALSE) AS is_locked,
                   u.email,
                   COALESCE(sp.prenom, '') AS prenom,
                   COALESCE(sp.nom, '') AS nom,
                   COALESCE(sp.avatar_url, '') AS avatar_url,
                   (SELECT COUNT(*) FROM forum_replies fr WHERE fr.post_id = fp.id) AS reply_count
            FROM forum_posts fp
            JOIN users u ON u.id = fp.user_id
            LEFT JOIN student_profiles sp ON sp.user_id = fp.user_id
            {where}
            ORDER BY fp.is_pinned DESC NULLS LAST, fp.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])

        rows = cur.fetchall()
        posts = []
        for r in rows:
            posts.append({
                "id":          str(r["id"]),
                "title":       r["title"],
                "content":     r["content"][:200] + ("…" if len(r["content"]) > 200 else ""),
                "category":    r["category"],
                "school":      r["school"] or "",
                "likes":       int(r["likes"] or 0),
                "views":       int(r["views"] or 0),
                "reply_count": int(r["reply_count"] or 0),
                "is_pinned":   bool(r["is_pinned"]),
                "is_locked":   bool(r["is_locked"]),
                "created_at":  r["created_at"].isoformat() if r["created_at"] else None,
                "author": {
                    "email":      r["email"],
                    "prenom":     r["prenom"],
                    "nom":        r["nom"],
                    "avatar_url": r["avatar_url"],
                },
            })

        return jsonify({"posts": posts, "total": int(total), "page": page,
                        "pages": max(1, -(-int(total) // limit))}), 200
    except Exception:
        print("ADMIN FORUM LIST ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── PUT /api/admin/forum/posts/<id> (pin / lock) ─────────────────────────────
@admin_bp.route("/forum/posts/<post_id>", methods=["PUT"])
@admin_required
def update_forum_post(post_id):
    data = request.get_json(silent=True) or {}
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            _ensure_forum_moderation_cols(conn, cur)
        except Exception:
            conn.rollback()

        sets, params = [], []
        if "is_pinned" in data:
            sets.append("is_pinned = %s")
            params.append(bool(data["is_pinned"]))
        if "is_locked" in data:
            sets.append("is_locked = %s")
            params.append(bool(data["is_locked"]))

        if not sets:
            return jsonify({"error": "Aucun champ à modifier"}), 400

        params.append(post_id)
        cur.execute(f"""
            UPDATE forum_posts SET {', '.join(sets)}
            WHERE id = %s RETURNING id, is_pinned, is_locked
        """, params)
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Post introuvable"}), 404
        conn.commit()
        return jsonify({
            "ok":        True,
            "id":        str(row["id"]),
            "is_pinned": bool(row["is_pinned"]),
            "is_locked": bool(row["is_locked"]),
        }), 200
    except Exception:
        conn.rollback()
        print("ADMIN FORUM UPDATE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── DELETE /api/admin/forum/posts/<id> ───────────────────────────────────────
@admin_bp.route("/forum/posts/<post_id>", methods=["DELETE"])
@admin_required
def delete_forum_post(post_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM forum_posts WHERE id = %s", (post_id,))
        if cur.rowcount == 0:
            return jsonify({"error": "Post introuvable"}), 404
        conn.commit()
        return jsonify({"ok": True}), 200
    except Exception:
        conn.rollback()
        print("ADMIN FORUM DELETE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)
