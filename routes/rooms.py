from flask import Blueprint, request, jsonify
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
import random, string, jwt
from config import Config

rooms_bp = Blueprint("rooms", __name__)

def get_json():
    return request.get_json(silent=True) or {}

def get_user_from_token():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        print("NO BEARER TOKEN")
        return None
    token = auth.split(" ", 1)[1]
    try:
        payload = jwt.decode(
            token,
            Config.JWT_SECRET_KEY,
            algorithms=["HS256"],
            options={"verify_exp": False}
        )
        print("TOKEN PAYLOAD:", payload)
        return payload
    except Exception as e:
        print("TOKEN ERROR:", str(e))
        return None

def generate_code(length=6):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


@rooms_bp.route("/create", methods=["POST"])
def create_room():
    user = get_user_from_token()
    if not user:
        return jsonify({"error": "Non autorisé"}), 401

    data             = get_json()
    name             = (data.get("name") or "").strip()
    total_minutes    = int(data.get("total_minutes") or 25)
    max_participants = int(data.get("max_participants") or 4)

    if not name:
        return jsonify({"error": "Nom requis"}), 400

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Generate unique code
        code = generate_code()
        for _ in range(10):
            cur.execute("SELECT id FROM private_rooms WHERE code = %s AND is_active = TRUE", (code,))
            if not cur.fetchone():
                break
            code = generate_code()

        user_id = user.get("user_id") or user.get("sub") or user.get("id")

        cur.execute("""
            INSERT INTO private_rooms (host_id, name, code, total_minutes, max_participants, is_active)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            RETURNING id, name, code, total_minutes, max_participants, host_id
        """, (user_id, name, code, total_minutes, max_participants))
        room = cur.fetchone()

        # Add host as member
        cur.execute("""
            INSERT INTO private_room_members (room_id, user_id, is_host)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (room_id, user_id) DO NOTHING
        """, (room["id"], user_id))

        conn.commit()
        return jsonify({
            "room_id":         str(room["id"]),
            "name":            room["name"],
            "code":            room["code"],
            "total_minutes":   room["total_minutes"],
            "max_participants":room["max_participants"],
        }), 201

    except Exception as e:
        conn.rollback()
        print("CREATE ROOM ERROR:", str(e))
        return jsonify({"error": "Erreur serveur", "details": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


@rooms_bp.route("/join", methods=["POST"])
def join_room():
    user = get_user_from_token()
    if not user:
        return jsonify({"error": "Non autorisé"}), 401

    data = get_json()
    code = (data.get("code") or "").strip().upper()
    if not code:
        return jsonify({"error": "Code requis"}), 400

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT id, name, host_id, max_participants, is_active
            FROM private_rooms WHERE code = %s AND is_active = TRUE
        """, (code,))
        room = cur.fetchone()

        if not room:
            return jsonify({"error": "Code invalide ou salle introuvable"}), 404

        # Check capacity
        cur.execute(
            "SELECT COUNT(*) as cnt FROM private_room_members WHERE room_id = %s",
            (room["id"],)
        )
        count = cur.fetchone()["cnt"]
        if count >= room["max_participants"]:
            return jsonify({"error": "Salle complète"}), 400

        user_id = user.get("user_id") or user.get("sub") or user.get("id")

        cur.execute("""
            INSERT INTO private_room_members (room_id, user_id, is_host)
            VALUES (%s, %s, FALSE)
            ON CONFLICT (room_id, user_id) DO NOTHING
        """, (room["id"], user_id))
        conn.commit()

        return jsonify({
            "room_id": str(room["id"]),
            "name":    room["name"],
        }), 200

    except Exception as e:
        conn.rollback()
        print("JOIN ROOM ERROR:", str(e))
        return jsonify({"error": "Erreur serveur", "details": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


@rooms_bp.route("/<room_id>", methods=["GET"])
def get_room(room_id):
    user = get_user_from_token()
    if not user:
        print(f"GET ROOM 401: unauthenticated request for room_id={room_id}")
        return jsonify({"error": "Non autorisé"}), 401

    user_id = user.get("sub") or user.get("user_id") or user.get("id")
    print(f"GET ROOM: room_id={room_id} user_id={user_id}")

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT id, name, code, host_id, total_minutes, max_participants, is_active
            FROM private_rooms WHERE id = %s
        """, (room_id,))
        room = cur.fetchone()

        if not room:
            print(f"GET ROOM 404: room_id={room_id} not found")
            return jsonify({"error": "Salle introuvable"}), 404

        cur.execute("""
            SELECT u.id, sp.prenom, sp.nom, m.is_host
            FROM private_room_members m
            JOIN users u ON u.id = m.user_id
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE m.room_id = %s
        """, (room["id"],))
        members = cur.fetchall()
        print(f"GET ROOM 200: room_id={room_id} name={room['name']} members={len(members)}")

        return jsonify({
            "room": {
                "id":               str(room["id"]),
                "name":             room["name"],
                "code":             room["code"],
                "host_id":          str(room["host_id"]),
                "total_minutes":    room["total_minutes"],
                "max_participants": room["max_participants"],
                "is_active":        room["is_active"],
            },
            "members": [
                {
                    "id":      str(m["id"]),
                    "name":    f"{m['prenom'] or ''} {m['nom'] or ''}".strip() or "Anonyme",
                    "is_host": m["is_host"],
                }
                for m in members
            ]
        }), 200

    except Exception as e:
        print(f"GET ROOM 500: room_id={room_id} error={str(e)}")
        return jsonify({"error": "Erreur serveur", "details": str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


@rooms_bp.route("/<room_id>/leave", methods=["POST"])
def leave_room(room_id):
    user = get_user_from_token()
    if not user:
        return jsonify({"error": "Non autorisé"}), 401

    user_id = user.get("user_id") or user.get("sub") or user.get("id")

    conn = get_conn()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "DELETE FROM private_room_members WHERE room_id = %s AND user_id = %s",
            (room_id, user_id)
        )
        conn.commit()
        return jsonify({"message": "Quitté la salle"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)