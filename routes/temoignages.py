import traceback
from functools import wraps
from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
from middleware import token_required

temoignages_bp = Blueprint("temoignages", __name__)

_READY = False


def _ensure_table():
    global _READY
    if _READY:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS temoignages (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                    school VARCHAR(150) NOT NULL,
                    filiere VARCHAR(150),
                    annee_entree VARCHAR(10),
                    content TEXT NOT NULL,
                    rating INTEGER DEFAULT 5 CHECK (rating BETWEEN 1 AND 5),
                    is_approved BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_tem_approved_school
                ON temoignages(is_approved, school)
            """)
        conn.commit()
        _READY = True
    except Exception:
        print("TEMOIGNAGES TABLE ERROR:", traceback.format_exc())
        conn.rollback()
    finally:
        release_conn(conn)


_ensure_table()


def _admin_required(fn):
    @wraps(fn)
    @token_required
    def wrapper(*args, **kwargs):
        if g.current_user.get("role") != "admin":
            return jsonify({"error": "Accès refusé — admin uniquement"}), 403
        return fn(*args, **kwargs)
    return wrapper


def _row(r):
    nom    = (r.get("user_nom")    or "").strip()
    prenom = (r.get("user_prenom") or "").strip()
    name   = f"{prenom} {nom}".strip() or "Étudiant anonyme"
    return {
        "id":           str(r["id"]),
        "user_id":      str(r["user_id"]) if r.get("user_id") else None,
        "user_name":    name,
        "user_avatar":  r.get("avatar_url"),
        "school":       r["school"],
        "filiere":      r.get("filiere"),
        "annee_entree": r.get("annee_entree"),
        "content":      r["content"],
        "rating":       int(r["rating"] or 5),
        "is_approved":  r["is_approved"],
        "created_at":   r["created_at"].isoformat() if r.get("created_at") else None,
    }


# ── GET /api/temoignages ──────────────────────────────────────────────────────

@temoignages_bp.route("", methods=["GET"])
def get_temoignages():
    _ensure_table()
    school = (request.args.get("school") or "").strip()
    page   = max(1, int(request.args.get("page", 1)))
    limit  = min(int(request.args.get("limit", 20)), 50)
    offset = (page - 1) * limit

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        where  = "WHERE t.is_approved = TRUE"
        params = []
        if school:
            where += " AND t.school ILIKE %s"
            params.append(f"%{school}%")

        cur.execute(f"""
            SELECT t.*,
                   COALESCE(sp.nom, '')    AS user_nom,
                   COALESCE(sp.prenom, '') AS user_prenom,
                   sp.avatar_url
            FROM temoignages t
            LEFT JOIN student_profiles sp ON sp.user_id = t.user_id
            {where}
            ORDER BY t.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = cur.fetchall()

        cur.execute(f"SELECT COUNT(*) AS cnt FROM temoignages t {where}", params)
        total = int(cur.fetchone()["cnt"])

        cur.execute("""
            SELECT DISTINCT school FROM temoignages
            WHERE is_approved = TRUE ORDER BY school
        """)
        schools = [r["school"] for r in cur.fetchall()]

        return jsonify({
            "temoignages": [_row(r) for r in rows],
            "total":   total,
            "page":    page,
            "pages":   max(1, -(-total // limit)),
            "schools": schools,
        }), 200
    except Exception:
        print("GET TEMOIGNAGES ERROR:", traceback.format_exc())
        return jsonify({"temoignages": [], "total": 0, "page": 1, "pages": 1, "schools": []}), 200
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/temoignages/pending  (admin) ────────────────────────────────────

@temoignages_bp.route("/pending", methods=["GET"])
@_admin_required
def get_pending():
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT t.*,
                   COALESCE(sp.nom, '')    AS user_nom,
                   COALESCE(sp.prenom, '') AS user_prenom,
                   sp.avatar_url
            FROM temoignages t
            LEFT JOIN student_profiles sp ON sp.user_id = t.user_id
            WHERE t.is_approved = FALSE
            ORDER BY t.created_at ASC
        """)
        rows = cur.fetchall()
        return jsonify({"temoignages": [_row(r) for r in rows]}), 200
    except Exception:
        print("GET PENDING ERROR:", traceback.format_exc())
        return jsonify({"temoignages": []}), 200
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/temoignages ─────────────────────────────────────────────────────

@temoignages_bp.route("", methods=["POST"])
@token_required
def submit():
    _ensure_table()
    user_id = str(g.current_user["id"])
    data    = request.get_json(silent=True) or {}
    school  = (data.get("school")       or "").strip()
    content = (data.get("content")      or "").strip()
    filiere = (data.get("filiere")      or "").strip() or None
    annee   = (data.get("annee_entree") or "").strip() or None
    rating  = max(1, min(5, int(data.get("rating") or 5)))

    if not school or not content:
        return jsonify({"error": "École et contenu du témoignage requis"}), 400
    if len(content) < 20:
        return jsonify({"error": "Le témoignage doit faire au moins 20 caractères"}), 400
    if len(content) > 1000:
        return jsonify({"error": "Le témoignage ne doit pas dépasser 1000 caractères"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT id FROM temoignages WHERE user_id = %s AND school = %s",
            (user_id, school)
        )
        if cur.fetchone():
            return jsonify({"error": "Tu as déjà soumis un témoignage pour cette école"}), 409

        cur.execute("""
            INSERT INTO temoignages (user_id, school, filiere, annee_entree, content, rating)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (user_id, school, filiere, annee, content, rating))
        row = dict(cur.fetchone())
        row["user_nom"] = ""
        row["user_prenom"] = ""
        row["avatar_url"] = None
        conn.commit()
        return jsonify({
            "temoignage": _row(row),
            "message": "Témoignage soumis ! Il sera visible après validation par un administrateur.",
        }), 201
    except Exception:
        conn.rollback()
        print("SUBMIT TEMOIGNAGE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── PUT /api/temoignages/<id>/approve  (admin) ───────────────────────────────

@temoignages_bp.route("/<tem_id>/approve", methods=["PUT"])
@_admin_required
def approve(tem_id):
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "UPDATE temoignages SET is_approved = TRUE WHERE id = %s RETURNING id",
            (tem_id,)
        )
        if not cur.fetchone():
            conn.rollback()
            return jsonify({"error": "Témoignage introuvable"}), 404
        conn.commit()
        return jsonify({"ok": True}), 200
    except Exception:
        conn.rollback()
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── DELETE /api/temoignages/<id>  (admin) ────────────────────────────────────

@temoignages_bp.route("/<tem_id>", methods=["DELETE"])
@_admin_required
def delete(tem_id):
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM temoignages WHERE id = %s", (tem_id,))
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Témoignage introuvable"}), 404
        conn.commit()
        return jsonify({"ok": True}), 200
    except Exception:
        conn.rollback()
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)
