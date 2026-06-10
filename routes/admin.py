import traceback
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

        return jsonify({
            "total_users":       int(total_users),
            "active_today":      int(active_today),
            "total_orientations": int(total_orientations),
            "total_chats":       int(total_chats),
            "total_sessions":    int(total_sessions),
            "new_this_week":     int(new_this_week),
        }), 200
    except Exception:
        print("ADMIN STATS ERROR:", traceback.format_exc())
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

        # Recent logins
        cur.execute("""
            SELECT u.email, sp.prenom, u.last_login_at AS ts, 'login' AS type
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.last_login_at IS NOT NULL
            ORDER BY u.last_login_at DESC LIMIT 20
        """)
        events += [dict(r) for r in cur.fetchall()]

        # Recent registrations
        cur.execute("""
            SELECT u.email, sp.prenom, u.created_at AS ts, 'register' AS type
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            ORDER BY u.created_at DESC LIMIT 10
        """)
        events += [dict(r) for r in cur.fetchall()]

        # Recent orientations
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

        # Sort combined list and take top 40
        events.sort(key=lambda e: e["ts"] or "", reverse=True)
        for e in events:
            if e["ts"]:
                e["ts"] = e["ts"].isoformat()

        return jsonify({"activity": events[:40]}), 200
    except Exception:
        print("ADMIN ACTIVITY ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)
