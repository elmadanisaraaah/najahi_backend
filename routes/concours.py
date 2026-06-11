import traceback
from datetime import date
from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
from middleware import token_required

concours_bp = Blueprint("concours", __name__)

_READY = False

def _ensure_tables():
    global _READY
    if _READY:
        return
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS concours_calendar (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(200) NOT NULL,
                school VARCHAR(150) NOT NULL,
                category VARCHAR(100) NOT NULL,
                registration_start DATE,
                registration_end DATE,
                exam_date DATE,
                results_date DATE,
                description TEXT,
                official_link TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS concours_subscriptions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                concours_id UUID REFERENCES concours_calendar(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, concours_id)
            )
        """)
        conn.commit()

        cur.execute("SELECT COUNT(*) AS cnt FROM concours_calendar")
        if cur.fetchone()["cnt"] == 0:
            cur.execute("""
                INSERT INTO concours_calendar
                    (name, school, category, registration_start, registration_end,
                     exam_date, results_date, description, official_link)
                VALUES
                ('CNC - Concours National Commun',
                 'ENSA/ENSIAS/EMI/INPT/EHTP', 'Ingénierie',
                 '2026-03-01','2026-04-15','2026-06-15','2026-07-15',
                 'Concours d''accès aux grandes écoles d''ingénieurs après CPGE',
                 'https://cnc.ac.ma'),
                ('Concours ENCG',
                 'ENCG (toutes villes)', 'Commerce & Management',
                 '2026-03-15','2026-04-30','2026-06-20','2026-07-20',
                 'Concours d''accès aux Écoles Nationales de Commerce et de Gestion',
                 'https://encg.ac.ma'),
                ('Concours ISCAE',
                 'ISCAE Casablanca & Rabat', 'Commerce & Management',
                 '2026-03-01','2026-04-20','2026-06-10','2026-07-10',
                 'Concours d''accès à l''Institut Supérieur de Commerce et d''Administration des Entreprises',
                 'https://iscae.ac.ma'),
                ('Concours Médecine & Pharmacie',
                 'Facultés de Médecine', 'Santé',
                 '2026-07-01','2026-08-15','2026-09-10','2026-09-30',
                 'Concours national d''accès aux études de médecine, pharmacie et médecine dentaire',
                 'https://fmpr.ac.ma'),
                ('Concours CPGE',
                 'Lycées CPGE', 'Classes Préparatoires',
                 '2026-03-01','2026-04-30','2026-05-15','2026-06-01',
                 'Concours d''accès aux Classes Préparatoires aux Grandes Écoles',
                 'https://cpge.ac.ma'),
                ('Concours ENA Architecture',
                 'École Nationale d''Architecture', 'Architecture',
                 '2026-04-01','2026-05-15','2026-06-25','2026-07-25',
                 'Concours d''accès à l''École Nationale d''Architecture de Rabat',
                 'https://ena.ma'),
                ('Concours INPT',
                 'INPT Rabat', 'Télécommunications',
                 '2026-03-15','2026-05-01','2026-06-18','2026-07-18',
                 'Concours d''accès à l''Institut National des Postes et Télécommunications',
                 'https://inpt.ac.ma'),
                ('Concours EHTP',
                 'EHTP Casablanca', 'Ingénierie',
                 '2026-03-15','2026-05-01','2026-06-18','2026-07-18',
                 'Concours d''accès à l''École Hassania des Travaux Publics',
                 'https://ehtp.ac.ma'),
                ('Concours Al Akhawayn',
                 'Al Akhawayn University', 'Université Internationale',
                 '2026-01-01','2026-05-31','2026-06-01','2026-06-15',
                 'Admissions continues pour l''Université Al Akhawayn d''Ifrane',
                 'https://aui.ma'),
                ('Concours UIR',
                 'Université Internationale de Rabat', 'Université Privée',
                 '2026-01-01','2026-07-31','2026-08-01','2026-08-15',
                 'Admissions continues pour l''UIR',
                 'https://uir.ac.ma')
            """)
            conn.commit()
            print("[concours] seeded 10 rows")

        _READY = True
    except Exception:
        print("CONCOURS SETUP ERROR:", traceback.format_exc())
        conn.rollback()
    finally:
        cur.close(); release_conn(conn)

_ensure_tables()


def _row_to_dict(r):
    today = date.today()
    days = (r["exam_date"] - today).days if r["exam_date"] else None
    return {
        "id":                 str(r["id"]),
        "name":               r["name"],
        "school":             r["school"],
        "category":           r["category"],
        "registration_start": r["registration_start"].isoformat() if r["registration_start"] else None,
        "registration_end":   r["registration_end"].isoformat()   if r["registration_end"]   else None,
        "exam_date":          r["exam_date"].isoformat()           if r["exam_date"]           else None,
        "results_date":       r["results_date"].isoformat()        if r["results_date"]        else None,
        "description":        r["description"],
        "official_link":      r["official_link"],
        "is_active":          r["is_active"],
        "days_until_exam":    days,
    }


# ── GET /api/concours ─────────────────────────────────────────────────────────

@concours_bp.route("", methods=["GET"])
def get_all():
    _ensure_tables()
    category = request.args.get("category", "").strip()
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if category:
            cur.execute(
                "SELECT * FROM concours_calendar WHERE is_active = TRUE AND category = %s ORDER BY exam_date ASC NULLS LAST",
                (category,)
            )
        else:
            cur.execute(
                "SELECT * FROM concours_calendar WHERE is_active = TRUE ORDER BY exam_date ASC NULLS LAST"
            )
        rows = cur.fetchall()
        return jsonify([_row_to_dict(r) for r in rows]), 200
    except Exception:
        print("CONCOURS GET ERROR:", traceback.format_exc())
        return jsonify([]), 200
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/concours/upcoming ────────────────────────────────────────────────

@concours_bp.route("/upcoming", methods=["GET"])
def get_upcoming():
    _ensure_tables()
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM concours_calendar
            WHERE is_active = TRUE AND exam_date >= CURRENT_DATE
            ORDER BY exam_date ASC
            LIMIT 5
        """)
        rows = cur.fetchall()
        return jsonify([_row_to_dict(r) for r in rows]), 200
    except Exception:
        print("CONCOURS UPCOMING ERROR:", traceback.format_exc())
        return jsonify([]), 200
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/concours/subscribe ──────────────────────────────────────────────

@concours_bp.route("/subscribe", methods=["POST"])
@token_required
def subscribe():
    _ensure_tables()
    data = request.get_json(silent=True) or {}
    concours_id = (data.get("concours_id") or "").strip()
    if not concours_id:
        return jsonify({"error": "concours_id requis"}), 400

    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT id FROM concours_calendar WHERE id = %s AND is_active = TRUE",
            (concours_id,)
        )
        if not cur.fetchone():
            return jsonify({"error": "Concours introuvable"}), 404

        # Check existing subscription
        cur.execute(
            "SELECT id FROM concours_subscriptions WHERE user_id = %s AND concours_id = %s",
            (user_id, concours_id)
        )
        existing = cur.fetchone()
        if existing:
            # Toggle off
            cur.execute(
                "DELETE FROM concours_subscriptions WHERE user_id = %s AND concours_id = %s",
                (user_id, concours_id)
            )
            conn.commit()
            return jsonify({"subscribed": False, "message": "Rappel annulé"}), 200

        cur.execute(
            "INSERT INTO concours_subscriptions (user_id, concours_id) VALUES (%s, %s)",
            (user_id, concours_id)
        )
        conn.commit()
        return jsonify({"subscribed": True, "message": "Rappel activé !"}), 201

    except Exception:
        conn.rollback()
        print("CONCOURS SUBSCRIBE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)
