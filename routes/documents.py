import io
import os
import traceback
import cloudinary
import cloudinary.uploader
import jwt
from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
from middleware import token_required
from functools import wraps
from config import Config
from routes.notifications import send_notification

cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
)

documents_bp = Blueprint("documents", __name__)

DOC_TYPES   = {"sujet_concours", "fiche_revision", "autre"}
ALLOWED_MIME = {"application/pdf", "application/octet-stream"}
MAX_SIZE     = 20 * 1024 * 1024  # 20 MB

_READY = False


def _ensure_table():
    global _READY
    if _READY:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS shared_documents (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
                    title       VARCHAR(200) NOT NULL,
                    school      VARCHAR(150),
                    type        VARCHAR(50) NOT NULL DEFAULT 'autre',
                    file_url    TEXT NOT NULL,
                    is_approved BOOLEAN DEFAULT FALSE,
                    created_at  TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()
        _READY = True
    except Exception:
        conn.rollback()
        print("DOCUMENTS SETUP ERROR:", traceback.format_exc())
    finally:
        release_conn(conn)


_ensure_table()


def _optional_user_id():
    """Extract user_id from Authorization header without requiring it."""
    header = (request.headers.get("Authorization") or "").strip()
    if not header.startswith("Bearer "):
        return None
    token = header.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=[Config.JWT_ALGORITHM])
        if payload.get("type") == "access":
            return payload.get("sub")
    except Exception:
        pass
    return None


def _admin_required(fn):
    @wraps(fn)
    @token_required
    def wrapper(*args, **kwargs):
        if g.current_user.get("role") != "admin":
            return jsonify({"error": "Admin uniquement"}), 403
        return fn(*args, **kwargs)
    return wrapper


def _row_to_dict(r, include_user=False):
    d = {
        "id":          str(r["id"]),
        "title":       r["title"],
        "school":      r["school"] or "",
        "type":        r["type"],
        "file_url":    r["file_url"],
        "is_approved": r["is_approved"],
        "created_at":  r["created_at"].isoformat() if r["created_at"] else None,
    }
    if include_user:
        d["prenom"]     = r.get("prenom") or ""
        d["nom"]        = r.get("nom") or ""
        d["avatar_url"] = r.get("avatar_url")
    return d


# ── GET /api/documents ─────────────────────────────────────────

@documents_bp.route("", methods=["GET"])
def list_documents():
    _ensure_table()
    school = request.args.get("school", "").strip()
    dtype  = request.args.get("type", "").strip()
    uid    = _optional_user_id()

    conditions = []
    params     = []

    if uid:
        conditions.append("(d.is_approved = TRUE OR d.user_id = %s)")
        params.append(uid)
    else:
        conditions.append("d.is_approved = TRUE")

    if school:
        conditions.append("d.school ILIKE %s")
        params.append(f"%{school}%")
    if dtype and dtype in DOC_TYPES:
        conditions.append("d.type = %s")
        params.append(dtype)

    where = "WHERE " + " AND ".join(conditions)

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(f"""
            SELECT d.*, sp.prenom, sp.nom, sp.avatar_url
            FROM shared_documents d
            LEFT JOIN student_profiles sp ON sp.user_id = d.user_id
            {where}
            ORDER BY d.created_at DESC
            LIMIT 100
        """, params)
        rows = cur.fetchall()
        return jsonify([_row_to_dict(r, include_user=True) for r in rows]), 200
    except Exception:
        print("DOCUMENTS LIST ERROR:", traceback.format_exc())
        return jsonify([]), 200
    finally:
        cur.close()
        release_conn(conn)


# ── POST /api/documents ────────────────────────────────────────

@documents_bp.route("", methods=["POST"])
@token_required
def upload_document():
    _ensure_table()
    user_id = g.current_user["id"]

    file   = request.files.get("file")
    title  = (request.form.get("title") or "").strip()
    school = (request.form.get("school") or "").strip()
    dtype  = (request.form.get("type") or "autre").strip()

    if not file or not title:
        return jsonify({"error": "Fichier et titre requis"}), 400
    if dtype not in DOC_TYPES:
        return jsonify({"error": "Type invalide"}), 400

    content = file.read()
    if len(content) > MAX_SIZE:
        return jsonify({"error": "Fichier trop grand (max 20 Mo)"}), 400

    try:
        result = cloudinary.uploader.upload(
            io.BytesIO(content),
            resource_type="raw",
            folder="najahi/documents",
            use_filename=True,
            unique_filename=True,
            overwrite=False,
        )
        file_url = result["secure_url"]
    except Exception as e:
        return jsonify({"error": f"Erreur upload : {str(e)}"}), 500

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO shared_documents (user_id, title, school, type, file_url)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (user_id, title, school or None, dtype, file_url))
        row = cur.fetchone()
        conn.commit()
        return jsonify({
            "id":      str(row["id"]),
            "message": "Document soumis — en attente de validation par un administrateur",
        }), 201
    except Exception:
        conn.rollback()
        print("DOCUMENTS UPLOAD DB ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur base de données"}), 500
    finally:
        cur.close()
        release_conn(conn)


# ── GET /api/documents/admin ───────────────────────────────────

@documents_bp.route("/admin", methods=["GET"])
@_admin_required
def admin_list():
    _ensure_table()
    approved = request.args.get("approved", "")
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if approved == "false":
            where = "WHERE d.is_approved = FALSE"
        elif approved == "true":
            where = "WHERE d.is_approved = TRUE"
        else:
            where = ""
        cur.execute(f"""
            SELECT d.*, sp.prenom, sp.nom, sp.avatar_url
            FROM shared_documents d
            LEFT JOIN student_profiles sp ON sp.user_id = d.user_id
            {where}
            ORDER BY d.is_approved ASC, d.created_at ASC
        """)
        rows = cur.fetchall()
        return jsonify([_row_to_dict(r, include_user=True) for r in rows]), 200
    except Exception:
        print("DOCUMENTS ADMIN LIST ERROR:", traceback.format_exc())
        return jsonify([]), 200
    finally:
        cur.close()
        release_conn(conn)


# ── PUT /api/documents/admin/<id>/approve ─────────────────────

@documents_bp.route("/admin/<doc_id>/approve", methods=["PUT"])
@_admin_required
def approve_document(doc_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            UPDATE shared_documents SET is_approved = TRUE
            WHERE id = %s
            RETURNING user_id, title
        """, (doc_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Document introuvable"}), 404
        conn.commit()
        if row["user_id"]:
            send_notification(
                user_id=row["user_id"],
                title="Document approuvé",
                message=f"Ton document « {row['title']} » a été approuvé et est maintenant visible par tous.",
                type="success",
                link="/app/documents",
            )
        return jsonify({"message": "Approuvé"}), 200
    except Exception:
        conn.rollback()
        print("DOCUMENTS APPROVE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur"}), 500
    finally:
        cur.close()
        release_conn(conn)


# ── DELETE /api/documents/admin/<id> ──────────────────────────

@documents_bp.route("/admin/<doc_id>", methods=["DELETE"])
@_admin_required
def delete_document(doc_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("DELETE FROM shared_documents WHERE id = %s RETURNING id", (doc_id,))
        if not cur.fetchone():
            return jsonify({"error": "Document introuvable"}), 404
        conn.commit()
        return jsonify({"message": "Supprimé"}), 200
    except Exception:
        conn.rollback()
        return jsonify({"error": "Erreur"}), 500
    finally:
        cur.close()
        release_conn(conn)
