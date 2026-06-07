from functools import wraps
from flask import request, jsonify, g
from psycopg2.extras import RealDictCursor
import jwt

from config import Config
from db import get_conn, release_conn


def token_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Token manquant"}), 401

        token = auth_header.split(" ", 1)[1].strip()

        try:
            payload = jwt.decode(
                token,
                Config.JWT_SECRET_KEY,
                algorithms=[Config.JWT_ALGORITHM]
            )

            if payload.get("type") != "access":
                return jsonify({"error": "Token invalide"}), 401

            user_id = payload.get("sub")
            if not user_id:
                return jsonify({"error": "Token invalide"}), 401

            conn = get_conn()
            cur = conn.cursor(cursor_factory=RealDictCursor)

            try:
                cur.execute(
                    """
                    SELECT id, email, phone_number, role,
                           is_email_verified, is_phone_verified,
                           auth_provider, status
                    FROM users
                    WHERE id = %s
                    """,
                    (user_id,)
                )
                user = cur.fetchone()

                if not user:
                    return jsonify({"error": "Utilisateur introuvable"}), 401

                if user["status"] != "active":
                    return jsonify({"error": "Compte inactif"}), 403

                g.current_user = user

            finally:
                cur.close()
                release_conn(conn)

        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expiré"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token invalide"}), 401

        return fn(*args, **kwargs)

    return wrapper