import os
import uuid
import traceback
from dotenv import load_dotenv
load_dotenv()
from flask import Blueprint, request, jsonify, g, send_file
from werkzeug.utils import secure_filename
from db import get_conn, release_conn
from middleware import token_required
import io

profile_bp = Blueprint("profile", __name__)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads/bulletins")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_BULLETINS = 5
MAX_SIZE = 10 * 1024 * 1024  # 10 MB


def _ensure_bulletins_table():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bulletins (
                    id          UUID        PRIMARY KEY,
                    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    original_name TEXT      NOT NULL,
                    stored_name TEXT        NOT NULL,
                    uploaded_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_conn(conn)


_ensure_bulletins_table()


def _auth_headers():
    token = (request.headers.get("Authorization", "") or "").replace("Bearer ", "").strip()
    return token


# ── GET /api/profile/me ───────────────────────────────────────────────────────

@profile_bp.route("/me", methods=["GET"])
@token_required
def get_me():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id, u.email, u.role, u.created_at,
                       p.prenom, p.nom, p.telephone, p.date_naissance,
                       p.ville, p.niveau, p.filiere_actuelle,
                       p.etablissement, p.annee_scolaire, p.moyenne_generale,
                       p.avatar_url, p.type_bac, p.note_bac,
                       COALESCE(p.show_in_leaderboard, FALSE) AS show_in_leaderboard
                FROM users u
                LEFT JOIN student_profiles p ON p.user_id = u.id
                WHERE u.id = %s
                """,
                (g.current_user["id"],),
            )
            row = cur.fetchone()

        if not row:
            return jsonify({"error": "Utilisateur introuvable"}), 404

        return jsonify({
            "id":               str(row[0]),
            "email":            row[1],
            "role":             row[2],
            "created_at":       row[3].isoformat() if row[3] else None,
            "prenom":           row[4] or "",
            "nom":              row[5] or "",
            "telephone":        row[6] or "",
            "date_naissance":   str(row[7]) if row[7] else None,
            "ville":            row[8] or "",
            "niveau":           row[9] or "",
            "filiere_actuelle": row[10] or "",
            "filiere":          row[10] or "",
            "etablissement":    row[11] or "",
            "annee_scolaire":   row[12] or "",
            "moyenne_generale": float(row[13]) if row[13] is not None else None,
            "avatar_url":           row[14] or None,
            "type_bac":             row[15] or "",
            "note_bac":             float(row[16]) if row[16] is not None else None,
            "show_in_leaderboard":  bool(row[17]),
        }), 200

    except Exception as e:
        print("PROFILE ME ERROR:", traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)


# ── PUT /api/profile/me ───────────────────────────────────────────────────────

@profile_bp.route("/me", methods=["PUT"])
@token_required
def update_me():
    data = request.get_json(silent=True) or {}

    allowed = [
        "prenom", "nom", "telephone", "date_naissance",
        "ville", "niveau", "filiere_actuelle",
        "etablissement", "annee_scolaire", "moyenne_generale",
        "type_bac", "note_bac", "show_in_leaderboard",
    ]
    fields = {k: data[k] for k in allowed if k in data}

    if not fields:
        return jsonify({"error": "Aucun champ à mettre à jour"}), 400

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [g.current_user["id"]]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE student_profiles SET {set_clause} WHERE user_id = %s",
                values,
            )
            conn.commit()
        return jsonify({"message": "Profil mis à jour"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)


# ── POST /api/profile/upload-bulletin ────────────────────────────────────────

@profile_bp.route("/upload-bulletin", methods=["POST"])
@token_required
def upload_bulletin():
    file = request.files.get("bulletin")
    if not file or not file.filename:
        return jsonify({"error": "Aucun fichier fourni"}), 400

    if file.mimetype not in ("application/pdf", "application/x-pdf"):
        ext = (file.filename or "").rsplit(".", 1)[-1].lower()
        if ext != "pdf":
            return jsonify({"error": "Seuls les fichiers PDF sont acceptés"}), 400

    content = file.read()
    if len(content) > MAX_SIZE:
        return jsonify({"error": "Fichier trop grand (max 10 MB)"}), 400

    user_id = str(g.current_user["id"])

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM bulletins WHERE user_id = %s",
                (user_id,),
            )
            count = cur.fetchone()[0]

        if count >= MAX_BULLETINS:
            release_conn(conn)
            return jsonify({"error": f"Maximum {MAX_BULLETINS} bulletins autorisés"}), 400

        original_name = secure_filename(file.filename or "bulletin.pdf")
        bulletin_id = str(uuid.uuid4())
        stored_name = f"{user_id}_{bulletin_id[:8]}_{original_name}"
        filepath = os.path.join(UPLOAD_DIR, stored_name)

        with open(filepath, "wb") as f:
            f.write(content)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bulletins (id, user_id, original_name, stored_name)
                VALUES (%s, %s, %s, %s)
                """,
                (bulletin_id, user_id, original_name, stored_name),
            )
            conn.commit()

        return jsonify({
            "id":            bulletin_id,
            "original_name": original_name,
            "uploaded_at":   None,
        }), 201

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)


# ── GET /api/profile/bulletins ────────────────────────────────────────────────

@profile_bp.route("/bulletins", methods=["GET"])
@token_required
def list_bulletins():
    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, original_name, stored_name, uploaded_at
                FROM bulletins
                WHERE user_id = %s
                ORDER BY uploaded_at DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()

        result = [
            {
                "id":            str(r[0]),
                "original_name": r[1],
                "stored_name":   r[2],
                "uploaded_at":   r[3].isoformat() if r[3] else None,
            }
            for r in rows
        ]
        return jsonify({"bulletins": result}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)


# ── GET /api/profile/bulletin/<id>/download ───────────────────────────────────

@profile_bp.route("/bulletin/<bulletin_id>/download", methods=["GET"])
@token_required
def download_bulletin(bulletin_id):
    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT original_name, stored_name FROM bulletins WHERE id = %s AND user_id = %s",
                (bulletin_id, user_id),
            )
            row = cur.fetchone()

        if not row:
            return jsonify({"error": "Bulletin introuvable"}), 404

        original_name, stored_name = row
        filepath = os.path.join(UPLOAD_DIR, stored_name)

        if not os.path.exists(filepath):
            return jsonify({"error": "Fichier introuvable sur le serveur"}), 404

        with open(filepath, "rb") as f:
            data = f.read()

        return send_file(
            io.BytesIO(data),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=original_name,
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)


# ── DELETE /api/profile/bulletin/<id> ────────────────────────────────────────

@profile_bp.route("/bulletin/<bulletin_id>", methods=["DELETE"])
@token_required
def delete_bulletin(bulletin_id):
    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stored_name FROM bulletins WHERE id = %s AND user_id = %s",
                (bulletin_id, user_id),
            )
            row = cur.fetchone()

        if not row:
            return jsonify({"error": "Bulletin introuvable"}), 404

        stored_name = row[0]
        filepath = os.path.join(UPLOAD_DIR, stored_name)

        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM bulletins WHERE id = %s AND user_id = %s",
                (bulletin_id, user_id),
            )
            conn.commit()

        if os.path.exists(filepath):
            os.remove(filepath)

        return jsonify({"message": "Bulletin supprimé"}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)
