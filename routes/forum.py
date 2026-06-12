import traceback
from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
from middleware import token_required

forum_bp = Blueprint("forum", __name__)

CATEGORIES = [
    {"id": "ensa",          "label": "ENSA",              "group": "Ingénierie"},
    {"id": "ensias",        "label": "ENSIAS",            "group": "Ingénierie"},
    {"id": "emi",           "label": "EMI",               "group": "Ingénierie"},
    {"id": "inpt",          "label": "INPT",              "group": "Ingénierie"},
    {"id": "ehtp",          "label": "EHTP",              "group": "Ingénierie"},
    {"id": "medecine",      "label": "Médecine",          "group": "Santé"},
    {"id": "pharmacie",     "label": "Pharmacie",         "group": "Santé"},
    {"id": "encg",          "label": "ENCG",              "group": "Commerce"},
    {"id": "iscae",         "label": "ISCAE",             "group": "Commerce"},
    {"id": "cpge",          "label": "CPGE",              "group": "Prépa"},
    {"id": "orientation",   "label": "Orientation",       "group": "Général"},
    {"id": "vie_etudiante", "label": "Vie étudiante",     "group": "Général"},
    {"id": "bourses",       "label": "Bourses & Aides",   "group": "Général"},
    {"id": "temoignages",   "label": "Témoignages",       "group": "Général"},
]

_TABLES_CREATED = False

def _ensure_tables(cur):
    global _TABLES_CREATED
    if _TABLES_CREATED:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS forum_posts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(300) NOT NULL,
            content TEXT NOT NULL,
            category VARCHAR(100) NOT NULL,
            school VARCHAR(150),
            likes INTEGER DEFAULT 0,
            views INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS forum_replies (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            post_id UUID REFERENCES forum_posts(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            likes INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS forum_likes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            post_id UUID,
            reply_id UUID,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    _TABLES_CREATED = True


def _author(cur, user_id):
    cur.execute("""
        SELECT u.email, sp.prenom, sp.nom, sp.avatar_url
        FROM users u
        LEFT JOIN student_profiles sp ON sp.user_id = u.id
        WHERE u.id = %s
    """, (user_id,))
    row = cur.fetchone()
    if not row:
        return {"prenom": "Utilisateur", "nom": "", "email": "", "avatar_url": None}
    prenom = row["prenom"] or row["email"].split("@")[0]
    return {"prenom": prenom, "nom": row["nom"] or "", "email": row["email"], "avatar_url": row["avatar_url"]}


# ── GET /api/forum/categories ──────────────────────────────────────────────

@forum_bp.route("/categories", methods=["GET"])
def get_categories():
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)
        conn.commit()
        cur.execute("""
            SELECT category, COUNT(*) AS cnt
            FROM forum_posts
            GROUP BY category
            HAVING COUNT(*) >= 1
            ORDER BY cnt DESC
        """)
        rows = cur.fetchall()
        result = [{"id": r["category"], "label": r["category"], "count": int(r["cnt"])} for r in rows]
        return jsonify(result), 200
    except Exception:
        print("FORUM CATEGORIES ERROR:", traceback.format_exc())
        return jsonify([]), 200
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/forum/posts ───────────────────────────────────────────────────

@forum_bp.route("/posts", methods=["GET"])
def get_posts():
    category = request.args.get("category", "").strip()
    school   = request.args.get("school", "").strip()
    search   = request.args.get("search", "").strip()
    page     = max(1, int(request.args.get("page", 1)))
    per_page = 20
    offset   = (page - 1) * per_page

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)
        conn.commit()

        conditions = []
        params = []
        if category:
            conditions.append("p.category = %s")
            params.append(category)
        if school:
            conditions.append("p.school ILIKE %s")
            params.append(f"%{school}%")
        if search:
            conditions.append("(p.title ILIKE %s OR p.content ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        cur.execute(f"""
            SELECT p.id, p.title, p.content, p.category, p.school,
                   p.likes, p.views, p.created_at, p.user_id,
                   (SELECT COUNT(*) FROM forum_replies r WHERE r.post_id = p.id) AS reply_count
            FROM forum_posts p
            {where}
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])
        posts = cur.fetchall()

        cur.execute(f"SELECT COUNT(*) AS total FROM forum_posts p {where}", params)
        total = cur.fetchone()["total"]

        result = []
        for p in posts:
            author = _author(cur, p["user_id"])
            result.append({
                "id":          str(p["id"]),
                "title":       p["title"],
                "category":    p["category"],
                "school":      p["school"],
                "likes":       p["likes"],
                "views":       p["views"],
                "reply_count": int(p["reply_count"]),
                "created_at":  p["created_at"].isoformat() if p["created_at"] else None,
                "author":      author,
            })

        return jsonify({"posts": result, "total": int(total), "page": page, "per_page": per_page}), 200

    except Exception:
        print("FORUM GET POSTS ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/forum/posts ──────────────────────────────────────────────────

@forum_bp.route("/posts", methods=["POST"])
@token_required
def create_post():
    user_id = g.current_user["id"]
    data    = request.get_json(silent=True) or {}
    title    = (data.get("title") or "").strip()
    content  = (data.get("content") or "").strip()
    category = (data.get("category") or "").strip()
    school   = (data.get("school") or "").strip() or None

    if not title or not content or not category:
        return jsonify({"error": "Titre, contenu et catégorie requis"}), 400
    if len(title) > 300:
        return jsonify({"error": "Titre trop long (max 300 caractères)"}), 400
    if category not in [c["id"] for c in CATEGORIES]:
        return jsonify({"error": "Catégorie invalide"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)
        cur.execute("""
            INSERT INTO forum_posts (user_id, title, content, category, school)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, created_at
        """, (user_id, title, content, category, school))
        row = cur.fetchone()
        conn.commit()
        return jsonify({"id": str(row["id"]), "created_at": row["created_at"].isoformat()}), 201
    except Exception:
        conn.rollback()
        print("FORUM CREATE POST ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/forum/posts/<id> ──────────────────────────────────────────────

@forum_bp.route("/posts/<post_id>", methods=["GET"])
def get_post(post_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)

        # increment views
        cur.execute("UPDATE forum_posts SET views = views + 1 WHERE id = %s", (post_id,))

        cur.execute("""
            SELECT id, user_id, title, content, category, school,
                   likes, views, created_at, updated_at
            FROM forum_posts WHERE id = %s
        """, (post_id,))
        post = cur.fetchone()
        if not post:
            conn.rollback()
            return jsonify({"error": "Post introuvable"}), 404

        author = _author(cur, post["user_id"])

        cur.execute("""
            SELECT id, user_id, content, likes, created_at
            FROM forum_replies
            WHERE post_id = %s
            ORDER BY created_at ASC
        """, (post_id,))
        replies_raw = cur.fetchall()

        replies = []
        for r in replies_raw:
            replies.append({
                "id":         str(r["id"]),
                "content":    r["content"],
                "likes":      r["likes"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "author":     _author(cur, r["user_id"]),
            })

        conn.commit()
        return jsonify({
            "id":         str(post["id"]),
            "title":      post["title"],
            "content":    post["content"],
            "category":   post["category"],
            "school":     post["school"],
            "likes":      post["likes"],
            "views":      post["views"],
            "created_at": post["created_at"].isoformat() if post["created_at"] else None,
            "author":     author,
            "replies":    replies,
        }), 200

    except Exception:
        conn.rollback()
        print("FORUM GET POST ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/forum/posts/<id>/reply ──────────────────────────────────────

@forum_bp.route("/posts/<post_id>/reply", methods=["POST"])
@token_required
def add_reply(post_id):
    user_id = g.current_user["id"]
    data    = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()

    if not content:
        return jsonify({"error": "Contenu requis"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)

        cur.execute("SELECT id FROM forum_posts WHERE id = %s", (post_id,))
        if not cur.fetchone():
            return jsonify({"error": "Post introuvable"}), 404

        cur.execute("""
            INSERT INTO forum_replies (post_id, user_id, content)
            VALUES (%s, %s, %s)
            RETURNING id, created_at
        """, (post_id, user_id, content))
        row = cur.fetchone()
        conn.commit()

        author = _author(cur, user_id)
        return jsonify({
            "id":         str(row["id"]),
            "content":    content,
            "likes":      0,
            "created_at": row["created_at"].isoformat(),
            "author":     author,
        }), 201

    except Exception:
        conn.rollback()
        print("FORUM REPLY ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/forum/posts/<id>/like ───────────────────────────────────────

@forum_bp.route("/posts/<post_id>/like", methods=["POST"])
@token_required
def toggle_like(post_id):
    user_id = g.current_user["id"]
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)

        cur.execute("""
            SELECT id FROM forum_likes
            WHERE user_id = %s AND post_id = %s AND reply_id IS NULL
        """, (user_id, post_id))
        existing = cur.fetchone()

        if existing:
            cur.execute("DELETE FROM forum_likes WHERE id = %s", (existing["id"],))
            cur.execute("UPDATE forum_posts SET likes = GREATEST(0, likes - 1) WHERE id = %s RETURNING likes", (post_id,))
            liked = False
        else:
            cur.execute("INSERT INTO forum_likes (user_id, post_id) VALUES (%s, %s)", (user_id, post_id))
            cur.execute("UPDATE forum_posts SET likes = likes + 1 WHERE id = %s RETURNING likes", (post_id,))
            liked = True

        row = cur.fetchone()
        conn.commit()
        return jsonify({"liked": liked, "likes": row["likes"] if row else 0}), 200

    except Exception:
        conn.rollback()
        print("FORUM LIKE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)
