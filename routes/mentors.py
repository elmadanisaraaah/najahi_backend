import traceback
from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
from middleware import token_required
from routes.notifications import send_notification

mentors_bp = Blueprint("mentors", __name__)

_READY = False


def _ensure_tables():
    global _READY
    if _READY:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mentors (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id     UUID UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    school      VARCHAR(200) NOT NULL,
                    filiere     VARCHAR(200) NOT NULL,
                    bio         TEXT,
                    available   BOOLEAN DEFAULT TRUE,
                    created_at  TIMESTAMP DEFAULT NOW(),
                    updated_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mentor_requests (
                    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    requester_id UUID REFERENCES users(id) ON DELETE CASCADE,
                    mentor_id    UUID REFERENCES mentors(id) ON DELETE CASCADE,
                    message      TEXT,
                    created_at   TIMESTAMP DEFAULT NOW(),
                    UNIQUE(requester_id, mentor_id)
                )
            """)
        conn.commit()
        _READY = True
    except Exception:
        conn.rollback()
        print("MENTORS SETUP ERROR:", traceback.format_exc())
    finally:
        release_conn(conn)


_ensure_tables()


# ── GET /api/mentors ───────────────────────────────────────────

@mentors_bp.route("", methods=["GET"])
def list_mentors():
    _ensure_tables()
    school  = request.args.get("school", "").strip()
    filiere = request.args.get("filiere", "").strip()

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        conditions = ["m.available = TRUE"]
        params = []
        if school:
            conditions.append("m.school ILIKE %s")
            params.append(f"%{school}%")
        if filiere:
            conditions.append("m.filiere ILIKE %s")
            params.append(f"%{filiere}%")

        where = "WHERE " + " AND ".join(conditions)
        cur.execute(f"""
            SELECT m.id, m.school, m.filiere, m.bio, m.available, m.created_at,
                   sp.prenom, sp.nom, sp.avatar_url, sp.ville
            FROM mentors m
            JOIN student_profiles sp ON sp.user_id = m.user_id
            {where}
            ORDER BY m.created_at DESC
            LIMIT 100
        """, params)
        rows = cur.fetchall()
        result = []
        for r in rows:
            result.append({
                "id":        str(r["id"]),
                "school":    r["school"],
                "filiere":   r["filiere"],
                "bio":       r["bio"] or "",
                "available": r["available"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "prenom":    r["prenom"] or "",
                "nom":       r["nom"] or "",
                "avatar_url": r["avatar_url"],
                "ville":     r["ville"] or "",
            })
        return jsonify(result), 200
    except Exception:
        print("MENTORS LIST ERROR:", traceback.format_exc())
        return jsonify([]), 200
    finally:
        cur.close()
        release_conn(conn)


# ── GET /api/mentors/me ────────────────────────────────────────

@mentors_bp.route("/me", methods=["GET"])
@token_required
def get_my_mentor():
    _ensure_tables()
    user_id = g.current_user["id"]
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM mentors WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            return jsonify(None), 200
        return jsonify({
            "id":        str(row["id"]),
            "school":    row["school"],
            "filiere":   row["filiere"],
            "bio":       row["bio"] or "",
            "available": row["available"],
        }), 200
    except Exception:
        return jsonify(None), 200
    finally:
        cur.close()
        release_conn(conn)


# ── POST /api/mentors/register ─────────────────────────────────

@mentors_bp.route("/register", methods=["POST"])
@token_required
def register_mentor():
    _ensure_tables()
    user_id = g.current_user["id"]
    data    = request.get_json(silent=True) or {}
    school  = (data.get("school") or "").strip()
    filiere = (data.get("filiere") or "").strip()
    bio     = (data.get("bio") or "").strip()

    if not school or not filiere:
        return jsonify({"error": "École et filière requis"}), 400
    if len(bio) > 500:
        return jsonify({"error": "Bio trop longue (max 500 caractères)"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO mentors (user_id, school, filiere, bio)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
              SET school     = EXCLUDED.school,
                  filiere    = EXCLUDED.filiere,
                  bio        = EXCLUDED.bio,
                  updated_at = NOW()
            RETURNING id
        """, (user_id, school, filiere, bio))
        row = cur.fetchone()
        conn.commit()
        return jsonify({"id": str(row["id"]), "message": "Profil mentor enregistré"}), 200
    except Exception:
        conn.rollback()
        print("MENTOR REGISTER ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close()
        release_conn(conn)


# ── PUT /api/mentors/<id>/toggle ───────────────────────────────

@mentors_bp.route("/<mentor_id>/toggle", methods=["PUT"])
@token_required
def toggle_availability(mentor_id):
    user_id = g.current_user["id"]
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            UPDATE mentors SET available = NOT available, updated_at = NOW()
            WHERE id = %s AND user_id = %s
            RETURNING available
        """, (mentor_id, user_id))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Non autorisé"}), 403
        conn.commit()
        return jsonify({"available": row["available"]}), 200
    except Exception:
        conn.rollback()
        return jsonify({"error": "Erreur"}), 500
    finally:
        cur.close()
        release_conn(conn)


# ── POST /api/mentors/<id>/contact ─────────────────────────────

@mentors_bp.route("/<mentor_id>/contact", methods=["POST"])
@token_required
def contact_mentor(mentor_id):
    _ensure_tables()
    requester_id = g.current_user["id"]
    data    = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"error": "Message requis"}), 400
    if len(message) > 500:
        return jsonify({"error": "Message trop long (max 500 caractères)"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT m.user_id AS mentor_user_id, m.school,
                   sp.prenom AS req_prenom, sp.nom AS req_nom
            FROM mentors m
            JOIN student_profiles sp ON sp.user_id = %s
            WHERE m.id = %s
        """, (requester_id, mentor_id))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Mentor introuvable"}), 404

        if str(row["mentor_user_id"]) == str(requester_id):
            return jsonify({"error": "Vous ne pouvez pas vous contacter vous-même"}), 400

        cur.execute("""
            INSERT INTO mentor_requests (requester_id, mentor_id, message)
            VALUES (%s, %s, %s)
            ON CONFLICT (requester_id, mentor_id) DO UPDATE
              SET message = EXCLUDED.message, created_at = NOW()
        """, (requester_id, mentor_id, message))
        conn.commit()

        req_name = f"{row['req_prenom'] or ''} {row['req_nom'] or ''}".strip() or "Un étudiant"
        preview  = message[:120] + ("…" if len(message) > 120 else "")
        send_notification(
            user_id=row["mentor_user_id"],
            title="Nouvelle demande de mentorat",
            message=f"{req_name} t'a contacté pour du mentorat ({row['school']}) : « {preview} »",
            type="info",
            link="/app/mentors",
        )
        return jsonify({"message": "Demande envoyée"}), 200
    except Exception:
        conn.rollback()
        print("MENTOR CONTACT ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close()
        release_conn(conn)
