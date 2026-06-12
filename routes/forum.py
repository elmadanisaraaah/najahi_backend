import traceback
from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
from middleware import token_required
from routes.notifications import send_notification

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

REACTION_TYPES = ("like", "love", "celebrate", "support", "insightful")

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
        ALTER TABLE forum_replies
        ADD COLUMN IF NOT EXISTS parent_reply_id UUID
        REFERENCES forum_replies(id) ON DELETE SET NULL
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS post_reactions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            post_id UUID REFERENCES forum_posts(id) ON DELETE CASCADE,
            reply_id UUID REFERENCES forum_replies(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            reaction_type VARCHAR(20) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS post_reactions_user_post_idx
        ON post_reactions(user_id, post_id)
        WHERE reply_id IS NULL
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS post_reactions_user_reply_idx
        ON post_reactions(user_id, reply_id)
        WHERE post_id IS NULL
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


def _get_reactions(cur, post_id=None, reply_id=None):
    result = {rt: 0 for rt in REACTION_TYPES}
    if post_id:
        cur.execute("""
            SELECT reaction_type, COUNT(*) AS cnt
            FROM post_reactions
            WHERE post_id = %s AND reply_id IS NULL
            GROUP BY reaction_type
        """, (post_id,))
    elif reply_id:
        cur.execute("""
            SELECT reaction_type, COUNT(*) AS cnt
            FROM post_reactions
            WHERE reply_id = %s AND post_id IS NULL
            GROUP BY reaction_type
        """, (reply_id,))
    else:
        return result
    for row in cur.fetchall():
        if row["reaction_type"] in result:
            result[row["reaction_type"]] = int(row["cnt"])
    return result


def _get_user_reaction(cur, user_id, post_id=None, reply_id=None):
    if not user_id:
        return None
    if post_id:
        cur.execute("""
            SELECT reaction_type FROM post_reactions
            WHERE user_id = %s AND post_id = %s AND reply_id IS NULL
        """, (user_id, post_id))
    elif reply_id:
        cur.execute("""
            SELECT reaction_type FROM post_reactions
            WHERE user_id = %s AND reply_id = %s AND post_id IS NULL
        """, (user_id, reply_id))
    else:
        return None
    row = cur.fetchone()
    return row["reaction_type"] if row else None


def _sync_post_likes(cur, post_id):
    cur.execute("""
        UPDATE forum_posts SET likes = (
            SELECT COUNT(*) FROM post_reactions
            WHERE post_id = %s AND reply_id IS NULL
        ) WHERE id = %s RETURNING likes
    """, (post_id, post_id))
    row = cur.fetchone()
    return row["likes"] if row else 0


def _do_react(cur, user_id, reaction_type, post_id=None, reply_id=None):
    if reaction_type not in REACTION_TYPES:
        return None, False

    existing = _get_user_reaction(cur, user_id, post_id=post_id, reply_id=reply_id)

    if existing == reaction_type:
        # Toggle off
        if post_id:
            cur.execute("""
                DELETE FROM post_reactions
                WHERE user_id = %s AND post_id = %s AND reply_id IS NULL
            """, (user_id, post_id))
        else:
            cur.execute("""
                DELETE FROM post_reactions
                WHERE user_id = %s AND reply_id = %s AND post_id IS NULL
            """, (user_id, reply_id))
        return None, False
    elif existing:
        # Change reaction type
        if post_id:
            cur.execute("""
                UPDATE post_reactions SET reaction_type = %s
                WHERE user_id = %s AND post_id = %s AND reply_id IS NULL
            """, (reaction_type, user_id, post_id))
        else:
            cur.execute("""
                UPDATE post_reactions SET reaction_type = %s
                WHERE user_id = %s AND reply_id = %s AND post_id IS NULL
            """, (reaction_type, user_id, reply_id))
        return reaction_type, True
    else:
        # New reaction
        if post_id:
            cur.execute("""
                INSERT INTO post_reactions (user_id, post_id, reaction_type)
                VALUES (%s, %s, %s)
            """, (user_id, post_id, reaction_type))
        else:
            cur.execute("""
                INSERT INTO post_reactions (user_id, reply_id, reaction_type)
                VALUES (%s, %s, %s)
            """, (user_id, reply_id, reaction_type))
        return reaction_type, True


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
            reactions = _get_reactions(cur, post_id=str(p["id"]))
            total_reactions = sum(reactions.values())
            result.append({
                "id":              str(p["id"]),
                "title":           p["title"],
                "category":        p["category"],
                "school":          p["school"],
                "likes":           p["likes"],
                "total_reactions": total_reactions,
                "reactions":       reactions,
                "views":           p["views"],
                "reply_count":     int(p["reply_count"]),
                "created_at":      p["created_at"].isoformat() if p["created_at"] else None,
                "author":          author,
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
    auth_header = request.headers.get("Authorization", "")
    viewer_id = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            from middleware import decode_token
            payload = decode_token(token)
            viewer_id = payload.get("user_id") or payload.get("id")
        except Exception:
            pass

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)

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
        post_reactions = _get_reactions(cur, post_id=post_id)
        user_reaction = _get_user_reaction(cur, viewer_id, post_id=post_id) if viewer_id else None

        cur.execute("""
            SELECT id, user_id, content, likes, created_at, parent_reply_id
            FROM forum_replies
            WHERE post_id = %s
            ORDER BY created_at ASC
        """, (post_id,))
        replies_raw = cur.fetchall()

        replies = []
        for r in replies_raw:
            reply_id = str(r["id"])
            reply_reactions = _get_reactions(cur, reply_id=reply_id)
            reply_user_reaction = _get_user_reaction(cur, viewer_id, reply_id=reply_id) if viewer_id else None
            replies.append({
                "id":              reply_id,
                "content":         r["content"],
                "likes":           r["likes"],
                "reactions":       reply_reactions,
                "user_reaction":   reply_user_reaction,
                "parent_reply_id": str(r["parent_reply_id"]) if r["parent_reply_id"] else None,
                "created_at":      r["created_at"].isoformat() if r["created_at"] else None,
                "author":          _author(cur, r["user_id"]),
            })

        conn.commit()
        return jsonify({
            "id":            str(post["id"]),
            "title":         post["title"],
            "content":       post["content"],
            "category":      post["category"],
            "school":        post["school"],
            "likes":         post["likes"],
            "reactions":     post_reactions,
            "user_reaction": user_reaction,
            "views":         post["views"],
            "created_at":    post["created_at"].isoformat() if post["created_at"] else None,
            "author":        author,
            "replies":       replies,
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
    content         = (data.get("content") or "").strip()
    parent_reply_id = data.get("parent_reply_id") or None

    if not content:
        return jsonify({"error": "Contenu requis"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)

        cur.execute("SELECT id, user_id, title FROM forum_posts WHERE id = %s", (post_id,))
        post = cur.fetchone()
        if not post:
            return jsonify({"error": "Post introuvable"}), 404

        if parent_reply_id:
            cur.execute("SELECT id FROM forum_replies WHERE id = %s AND post_id = %s", (parent_reply_id, post_id))
            if not cur.fetchone():
                return jsonify({"error": "Réponse parente introuvable"}), 404

        cur.execute("""
            INSERT INTO forum_replies (post_id, user_id, content, parent_reply_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id, created_at
        """, (post_id, user_id, content, parent_reply_id))
        row = cur.fetchone()
        conn.commit()

        if str(post["user_id"]) != str(user_id):
            replier = _author(cur, user_id)
            rep_name = f"{replier.get('prenom','')} {replier.get('nom','')}".strip() or "Quelqu'un"
            try:
                send_notification(
                    user_id=post["user_id"],
                    title="Nouvelle réponse à ton post",
                    message=f"{rep_name} a répondu à « {(post['title'] or '')[:60]} »",
                    type="info",
                    link=f"/app/forum/{post_id}",
                )
            except Exception:
                pass

        author = _author(cur, user_id)
        return jsonify({
            "id":              str(row["id"]),
            "content":         content,
            "likes":           0,
            "reactions":       {rt: 0 for rt in REACTION_TYPES},
            "user_reaction":   None,
            "parent_reply_id": parent_reply_id,
            "created_at":      row["created_at"].isoformat(),
            "author":          author,
        }), 201

    except Exception:
        conn.rollback()
        print("FORUM REPLY ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/forum/posts/<id>/react ──────────────────────────────────────

@forum_bp.route("/posts/<post_id>/react", methods=["POST"])
@token_required
def react_post(post_id):
    user_id = g.current_user["id"]
    data = request.get_json(silent=True) or {}
    reaction_type = (data.get("reaction_type") or "like").strip()
    reply_id      = data.get("reply_id") or None

    if reaction_type not in REACTION_TYPES:
        return jsonify({"error": "Type de réaction invalide"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)

        if reply_id:
            cur.execute("SELECT id FROM forum_replies WHERE id = %s AND post_id = %s", (reply_id, post_id))
            if not cur.fetchone():
                return jsonify({"error": "Réponse introuvable"}), 404
            new_type, reacted = _do_react(cur, user_id, reaction_type, reply_id=reply_id)
            reactions = _get_reactions(cur, reply_id=reply_id)
        else:
            cur.execute("SELECT id FROM forum_posts WHERE id = %s", (post_id,))
            if not cur.fetchone():
                return jsonify({"error": "Post introuvable"}), 404
            new_type, reacted = _do_react(cur, user_id, reaction_type, post_id=post_id)
            likes = _sync_post_likes(cur, post_id)
            reactions = _get_reactions(cur, post_id=post_id)

        conn.commit()
        return jsonify({
            "reacted":       reacted,
            "reaction_type": new_type,
            "reactions":     reactions,
            "likes":         sum(reactions.values()),
        }), 200

    except Exception:
        conn.rollback()
        print("FORUM REACT ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/forum/posts/<id>/like (backward compat) ─────────────────────

@forum_bp.route("/posts/<post_id>/like", methods=["POST"])
@token_required
def toggle_like(post_id):
    user_id = g.current_user["id"]
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)

        cur.execute("SELECT id FROM forum_posts WHERE id = %s", (post_id,))
        if not cur.fetchone():
            return jsonify({"error": "Post introuvable"}), 404

        new_type, reacted = _do_react(cur, user_id, "like", post_id=post_id)
        likes = _sync_post_likes(cur, post_id)
        conn.commit()
        return jsonify({"liked": reacted, "likes": likes}), 200

    except Exception:
        conn.rollback()
        print("FORUM LIKE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)
