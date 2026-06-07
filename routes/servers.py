import uuid
from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
from middleware import token_required

servers_bp = Blueprint("servers", __name__)

# ── GET /api/servers — liste des serveurs validés ──────────────────────────────
@servers_bp.route("/", methods=["GET"])
@token_required
def list_servers():
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT s.id, s.name, s.description, s.subject, s.icon,
                   s.banner_color, s.member_count, s.created_at,
                   u.email as owner_email,
                   EXISTS(
                       SELECT 1 FROM study_server_members m
                       WHERE m.server_id = s.id AND m.user_id = %s
                   ) as is_member
            FROM study_servers s
            JOIN users u ON u.id = s.owner_id
            WHERE s.is_validated = TRUE
            ORDER BY s.member_count DESC, s.created_at DESC
        """, (g.current_user["id"],))
        rows = cur.fetchall()
        for r in rows:
            r["id"] = str(r["id"])
            r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        return jsonify({"servers": rows}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/servers/<id> — détail serveur + canaux ───────────────────────────
@servers_bp.route("/<server_id>", methods=["GET"])
@token_required
def get_server(server_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT s.*, u.email as owner_email,
                   EXISTS(
                       SELECT 1 FROM study_server_members m
                       WHERE m.server_id = s.id AND m.user_id = %s
                   ) as is_member
            FROM study_servers s
            JOIN users u ON u.id = s.owner_id
            WHERE s.id = %s AND s.is_validated = TRUE
        """, (g.current_user["id"], server_id))
        server = cur.fetchone()
        if not server:
            return jsonify({"error": "Serveur introuvable"}), 404

        cur.execute("""
            SELECT c.id, c.name, c.description, c.subject, c.is_study_room,
                   COUNT(p.student_id) FILTER (WHERE p.is_present) as active_users
            FROM study_channels c
            LEFT JOIN study_rooms r ON r.id::text = c.id::text
            LEFT JOIN study_room_participants p ON p.room_id = r.id
            WHERE c.server_id = %s
            GROUP BY c.id
            ORDER BY c.name ASC
        """, (server_id,))
        channels = cur.fetchall()

        server["id"] = str(server["id"])
        server["created_at"] = server["created_at"].isoformat() if server["created_at"] else None
        for c in channels:
            c["id"] = str(c["id"])

        return jsonify({"server": dict(server), "channels": channels}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/servers/request — demande de création ───────────────────────────
@servers_bp.route("/request", methods=["POST"])
@token_required
def request_server():
    data = request.get_json(silent=True) or {}
    name    = (data.get("name") or "").strip()
    subject = (data.get("subject") or "").strip()
    desc    = (data.get("description") or "").strip()

    if not name:
        return jsonify({"error": "Nom requis"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO study_server_requests (id, user_id, name, description, subject, status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
            RETURNING id
        """, (str(uuid.uuid4()), g.current_user["id"], name, desc or None, subject or None))
        row = cur.fetchone()
        conn.commit()
        return jsonify({"message": "Demande envoyée, en attente de validation.", "id": str(row["id"])}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/servers/<id>/join ───────────────────────────────────────────────
@servers_bp.route("/<server_id>/join", methods=["POST"])
@token_required
def join_server(server_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id FROM study_servers WHERE id = %s AND is_validated = TRUE", (server_id,))
        if not cur.fetchone():
            return jsonify({"error": "Serveur introuvable"}), 404

        cur.execute("""
            INSERT INTO study_server_members (server_id, user_id, role)
            VALUES (%s, %s, 'member')
            ON CONFLICT (server_id, user_id) DO NOTHING
        """, (server_id, g.current_user["id"]))

        cur.execute("""
            UPDATE study_servers SET member_count = member_count + 1 WHERE id = %s
        """, (server_id,))

        conn.commit()
        return jsonify({"message": "Serveur rejoint"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/servers/<id>/leave ──────────────────────────────────────────────
@servers_bp.route("/<server_id>/leave", methods=["POST"])
@token_required
def leave_server(server_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM study_server_members
            WHERE server_id = %s AND user_id = %s
        """, (server_id, g.current_user["id"]))
        cur.execute("""
            UPDATE study_servers SET member_count = GREATEST(0, member_count - 1) WHERE id = %s
        """, (server_id,))
        conn.commit()
        return jsonify({"message": "Serveur quitté"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/servers/admin/validate — valider une demande (admin) ─────────────
@servers_bp.route("/admin/validate", methods=["POST"])
@token_required
def validate_server():
    if g.current_user.get("role") != "admin":
        return jsonify({"error": "Accès refusé"}), 403

    data       = request.get_json(silent=True) or {}
    request_id = data.get("request_id")
    action     = data.get("action")  # "approve" | "reject"

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM study_server_requests WHERE id = %s", (request_id,))
        req = cur.fetchone()
        if not req:
            return jsonify({"error": "Demande introuvable"}), 404

        if action == "approve":
            server_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO study_servers (id, name, description, subject, owner_id, is_validated)
                VALUES (%s, %s, %s, %s, %s, TRUE)
            """, (server_id, req["name"], req["description"], req["subject"], req["user_id"]))

            # Create default channels
            for ch_name in ["général", "questions", "ressources"]:
                cur.execute("""
                    INSERT INTO study_channels (id, server_id, name, is_study_room)
                    VALUES (%s, %s, %s, FALSE)
                """, (str(uuid.uuid4()), server_id, ch_name))

            # Add owner as member
            cur.execute("""
                INSERT INTO study_server_members (server_id, user_id, role)
                VALUES (%s, %s, 'owner')
            """, (server_id, req["user_id"]))

        cur.execute("""
            UPDATE study_server_requests SET status = %s WHERE id = %s
        """, ("approved" if action == "approve" else "rejected", request_id))

        conn.commit()
        return jsonify({"message": f"Serveur {'approuvé' if action == 'approve' else 'rejeté'}"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/servers/<id>/channels — créer un canal ─────────────────────────
@servers_bp.route("/<server_id>/channels", methods=["POST"])
@token_required
def create_channel(server_id):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Nom requis"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # Check if owner
        cur.execute("""
            SELECT role FROM study_server_members
            WHERE server_id = %s AND user_id = %s
        """, (server_id, g.current_user["id"]))
        member = cur.fetchone()
        if not member or member["role"] not in ("owner", "admin"):
            return jsonify({"error": "Accès refusé"}), 403

        channel_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO study_channels (id, server_id, name, description, subject, is_study_room)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, name
        """, (channel_id, server_id, name, data.get("description"), data.get("subject"), data.get("is_study_room", True)))

        channel = cur.fetchone()
        conn.commit()
        return jsonify({"channel": dict(channel)}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)