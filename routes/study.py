import uuid, random, string, traceback
from datetime import date, timedelta
from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
from middleware import token_required
from routes.socket_events import socketio_instance, get_room

_SOLO_TABLE_CREATED = False

def _ensure_solo_table(cur):
    global _SOLO_TABLE_CREATED
    if _SOLO_TABLE_CREATED:
        return
    cur.execute('''
        CREATE TABLE IF NOT EXISTS solo_study_sessions (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ended_at TIMESTAMPTZ,
            duration_minutes NUMERIC(6,2)
        )
    ''')
    _SOLO_TABLE_CREATED = True

study_bp = Blueprint('study', __name__, url_prefix='/api/study')


def gen_code(n=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))


# ── GET /api/study/rooms — public rooms ─────────────────────
@study_bp.route('/rooms', methods=['GET'])
@token_required
def list_rooms():
    conn = get_conn()
    try:
        cur = conn.cursor()
        category = request.args.get("category", "").strip()
        tag      = request.args.get("tag", "").strip()
        filters  = ["r.is_active = TRUE", "r.is_public = TRUE"]
        params   = []
        if category and category in ("general", "ville", "lycee"):
            filters.append("r.category = %s")
            params.append(category)
        if tag:
            filters.append("r.tag ILIKE %s")
            params.append(f"%{tag}%")

        where = " AND ".join(filters)
        cur.execute(f'''
            SELECT r.id, r.nom, r.sujet, r.code_acces,
                   r.max_participants, r.pomodoro_work, r.pomodoro_break,
                   r.category, r.tag,
                   COUNT(p.student_id) FILTER (WHERE p.is_present) AS participant_count
            FROM study_rooms r
            LEFT JOIN study_room_participants p ON p.room_id = r.id
            WHERE {where}
            GROUP BY r.id
            ORDER BY r.created_at DESC
        ''', params)
        cols = ['id','nom','sujet','code_acces','max_participants',
                'pomodoro_work','pomodoro_break','category','tag','participant_count']
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        for r in rows: r['id'] = str(r['id'])
        return jsonify(rows), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/study/rooms/<id> — single room ─────────────────
@study_bp.route('/rooms/<room_id>', methods=['GET'])
@token_required
def get_room_detail(room_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT r.id, r.nom, r.sujet, r.code_acces, r.max_participants,
                   r.pomodoro_work, r.pomodoro_break, r.category, r.tag,
                   r.is_public, r.host_id,
                   COUNT(p.student_id) FILTER (WHERE p.is_present) AS participant_count
            FROM study_rooms r
            LEFT JOIN study_room_participants p ON p.room_id = r.id
            WHERE r.id = %s AND r.is_active = TRUE
            GROUP BY r.id
        ''', (room_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Salle introuvable'}), 404
        cols = ['id','nom','sujet','code_acces','max_participants',
                'pomodoro_work','pomodoro_break','category','tag','is_public',
                'host_id','participant_count']
        room = dict(zip(cols, row))
        room['id']      = str(room['id'])
        room['host_id'] = str(room['host_id']) if room['host_id'] else None
        return jsonify(room), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/study/rooms — create ──────────────────────────
@study_bp.route('/rooms', methods=['POST'])
@token_required
def create_room():
    data = request.get_json(silent=True) or {}
    nom  = (data.get('nom') or '').strip()
    if not nom:
        return jsonify({'error': 'Nom requis'}), 400

    user_id = g.current_user["id"]

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('SELECT id FROM student_profiles WHERE user_id = %s', (user_id,))
        profile = cur.fetchone()
        if not profile:
            return jsonify({'error': 'Profil introuvable'}), 404
        profile_id = str(profile[0])

        code = gen_code()
        for _ in range(5):
            cur.execute('SELECT id FROM study_rooms WHERE code_acces = %s', (code,))
            if not cur.fetchone(): break
            code = gen_code()

        room_id  = str(uuid.uuid4())
        category = (data.get('category') or 'general').strip()
        if category not in ('general', 'ville', 'lycee'):
            category = 'general'
        tag = (data.get('tag') or '').strip() or None
        cur.execute('''
            INSERT INTO study_rooms
            (id, host_id, nom, sujet, code_acces, max_participants,
             is_public, pomodoro_work, pomodoro_break, category, tag)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ''', (room_id, user_id, nom,
              data.get('sujet') or None,
              code,
              data.get('max_participants', 10),
              data.get('is_public', False),  # private by default — pass is_public=true to list in Explorer
              data.get('pomodoro_work', 25),
              data.get('pomodoro_break', 5),
              category, tag))

        cur.execute('''
            INSERT INTO study_room_participants (room_id, student_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        ''', (room_id, profile_id))

        conn.commit()
        return jsonify({
            'id': room_id, 'nom': nom,
            'sujet': data.get('sujet') or '',
            'code_acces': code,
            'max_participants': data.get('max_participants', 10),
            'pomodoro_work': data.get('pomodoro_work', 25),
            'pomodoro_break': data.get('pomodoro_break', 5),
            'is_public': data.get('is_public', True),
            'category': category,
            'tag': tag or '',
        }), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/study/leaderboard ──────────────────────────────
@study_bp.route('/leaderboard', methods=['GET'])
@token_required
def get_leaderboard():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT sp.prenom, sp.nom, sp.avatar_url, sp.ville,
                   COALESCE(SUM(s.duration_minutes), 0) AS weekly_minutes,
                   COUNT(s.id) AS session_count
            FROM student_profiles sp
            JOIN solo_study_sessions s ON s.user_id = sp.user_id
            WHERE s.started_at >= NOW() - INTERVAL \'7 days\'
              AND s.ended_at IS NOT NULL
              AND sp.show_in_leaderboard = TRUE
            GROUP BY sp.id, sp.prenom, sp.nom, sp.avatar_url, sp.ville
            ORDER BY weekly_minutes DESC
            LIMIT 10
        ''')
        cols = ['prenom', 'nom', 'avatar_url', 'ville', 'weekly_minutes', 'session_count']
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r['weekly_minutes'] = float(r['weekly_minutes'] or 0)
            r['session_count']  = int(r['session_count'] or 0)
        return jsonify(rows), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/study/rooms/join — join by code ───────────────
@study_bp.route('/rooms/join', methods=['POST'])
@token_required
def join_by_code():
    code = (request.get_json(silent=True) or {}).get('code', '').strip().upper()
    if not code:
        return jsonify({'error': 'Code requis'}), 400

    user_id = g.current_user["id"]

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT id, nom, sujet, code_acces, max_participants,
                   pomodoro_work, pomodoro_break
            FROM study_rooms
            WHERE code_acces = %s AND is_active = TRUE
        ''', (code,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Code invalide ou salle fermée'}), 404

        cols = ['id','nom','sujet','code_acces','max_participants',
                'pomodoro_work','pomodoro_break']
        room = dict(zip(cols, row))
        room['id'] = str(room['id'])

        cur.execute('SELECT id FROM student_profiles WHERE user_id = %s', (user_id,))
        profile = cur.fetchone()
        if profile:
            pid = str(profile[0])
            cur.execute(
                'SELECT 1 FROM study_room_participants WHERE room_id = %s AND student_id = %s',
                (room['id'], pid)
            )
            if cur.fetchone():
                cur.execute(
                    'UPDATE study_room_participants SET is_present = TRUE, joined_at = NOW() WHERE room_id = %s AND student_id = %s',
                    (room['id'], pid)
                )
            else:
                cur.execute(
                    'INSERT INTO study_room_participants (room_id, student_id) VALUES (%s, %s)',
                    (room['id'], pid)
                )
            conn.commit()

        return jsonify(room), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/study/rooms/<id>/join ─────────────────────────
@study_bp.route('/rooms/<room_id>/join', methods=['POST'])
@token_required
def join_room(room_id):
    user_id = g.current_user["id"]

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT id, prenom, nom FROM student_profiles WHERE user_id = %s', (user_id,))
        profile = cur.fetchone()
        if not profile:
            return jsonify({'error': 'Profil introuvable'}), 404

        profile_id = str(profile['id'])
        name = ((profile.get('prenom') or '') + ' ' + (profile.get('nom') or '')).strip() or 'Anonyme'

        cur.execute(
            'SELECT 1 FROM study_room_participants WHERE room_id = %s AND student_id = %s',
            (room_id, profile_id)
        )
        if cur.fetchone():
            cur.execute(
                'UPDATE study_room_participants SET is_present = TRUE, joined_at = NOW() WHERE room_id = %s AND student_id = %s',
                (room_id, profile_id)
            )
        else:
            cur.execute(
                'INSERT INTO study_room_participants (room_id, student_id) VALUES (%s, %s)',
                (room_id, profile_id)
            )
        conn.commit()

        # Notify all participants in the socket room
        if socketio_instance:
            socketio_instance.emit('participant_joined', {
                'user_id': str(user_id),
                'profile_id': profile_id,
                'name': name,
            }, room=room_id)

        return jsonify({'ok': True}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/study/rooms/<id>/leave ────────────────────────
@study_bp.route('/rooms/<room_id>/leave', methods=['POST'])
@token_required
def leave_room(room_id):
    user_id = g.current_user["id"]

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('SELECT id FROM student_profiles WHERE user_id = %s', (user_id,))
        profile = cur.fetchone()
        if profile:
            profile_id = str(profile[0])
            cur.execute('''
                UPDATE study_room_participants
                SET is_present = FALSE, left_at = NOW()
                WHERE room_id = %s AND student_id = %s
            ''', (room_id, profile_id))
            conn.commit()

            if socketio_instance:
                socketio_instance.emit('participant_left', {
                    'user_id': str(user_id),
                    'profile_id': profile_id,
                }, room=room_id)

        return jsonify({'ok': True}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/study/rooms/<id>/request-join ─────────────────
@study_bp.route('/rooms/<room_id>/request-join', methods=['POST'])
@token_required
def request_join(room_id):
    """Creates a pending join request; host must approve before the user enters."""
    user_id = g.current_user["id"]

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute('''
            SELECT r.id, r.nom, r.host_id,
                   sp_host.user_id AS host_user_id
            FROM study_rooms r
            JOIN student_profiles sp_host ON sp_host.id = r.host_id
            WHERE r.id = %s AND r.is_active = TRUE
        ''', (room_id,))
        room = cur.fetchone()
        if not room:
            return jsonify({'error': 'Salle introuvable'}), 404

        cur.execute('SELECT id, prenom, nom FROM student_profiles WHERE user_id = %s', (user_id,))
        profile = cur.fetchone()
        if not profile:
            return jsonify({'error': 'Profil introuvable'}), 404

        profile_id = str(profile['id'])
        name = ((profile.get('prenom') or '') + ' ' + (profile.get('nom') or '')).strip() or 'Anonyme'

        # If already a member (accepted), return directly
        cur.execute('''
            SELECT status FROM study_room_participants
            WHERE room_id = %s AND student_id = %s
        ''', (room_id, profile_id))
        existing = cur.fetchone()
        if existing and existing['status'] == 'accepted':
            return jsonify({'status': 'accepted', 'message': 'Déjà membre'}), 200

        # Create or update to pending
        cur.execute('''
            INSERT INTO study_room_participants (room_id, student_id, status, is_present)
            VALUES (%s, %s, 'pending', FALSE)
            ON CONFLICT (room_id, student_id)
            DO UPDATE SET status = 'pending', joined_at = NOW()
        ''', (room_id, profile_id))
        conn.commit()

        # Notify the host via socket
        if socketio_instance:
            socketio_instance.emit('join_request', {
                'room_id': room_id,
                'user_id': str(user_id),
                'profile_id': profile_id,
                'name': name,
            }, room=room_id)

        return jsonify({'status': 'pending', 'message': 'Demande envoyée à l\'hôte'}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/study/rooms/<id>/requests ───────────────────────
@study_bp.route('/rooms/<room_id>/requests', methods=['GET'])
@token_required
def get_join_requests(room_id):
    """Returns pending join requests for the host."""
    user_id = g.current_user["id"]

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Verify caller is host
        cur.execute('''
            SELECT r.id FROM study_rooms r
            JOIN student_profiles sp ON sp.id = r.host_id AND sp.user_id = %s
            WHERE r.id = %s
        ''', (str(user_id), room_id))
        if not cur.fetchone():
            return jsonify({'error': 'Accès refusé — hôte uniquement'}), 403

        cur.execute('''
            SELECT sp.id AS profile_id, sp.prenom, sp.nom, sp.user_id,
                   srp.joined_at
            FROM study_room_participants srp
            JOIN student_profiles sp ON sp.id = srp.student_id
            WHERE srp.room_id = %s AND srp.status = 'pending'
            ORDER BY srp.joined_at ASC
        ''', (room_id,))
        rows = cur.fetchall()
        requests = [{
            'profile_id': str(r['profile_id']),
            'user_id':    str(r['user_id']),
            'name': ((r.get('prenom') or '') + ' ' + (r.get('nom') or '')).strip() or 'Anonyme',
            'joined_at':  r['joined_at'].isoformat() if r['joined_at'] else None,
        } for r in rows]
        return jsonify(requests), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/study/rooms/<id>/requests/<profile_id>/accept ──
@study_bp.route('/rooms/<room_id>/requests/<profile_id>/accept', methods=['POST'])
@token_required
def accept_join_request(room_id, profile_id):
    user_id = g.current_user["id"]

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute('''
            SELECT r.id FROM study_rooms r
            JOIN student_profiles sp ON sp.id = r.host_id AND sp.user_id = %s
            WHERE r.id = %s
        ''', (str(user_id), room_id))
        if not cur.fetchone():
            return jsonify({'error': 'Accès refusé — hôte uniquement'}), 403

        cur.execute('''
            UPDATE study_room_participants
            SET status = 'accepted', is_present = TRUE, joined_at = NOW()
            WHERE room_id = %s AND student_id = %s AND status = 'pending'
        ''', (room_id, profile_id))
        conn.commit()

        # Fetch requester name for notification
        cur.execute('SELECT prenom, nom, user_id FROM student_profiles WHERE id = %s', (profile_id,))
        p = cur.fetchone()
        name = ((p.get('prenom') or '') + ' ' + (p.get('nom') or '')).strip() if p else 'Anonyme'

        if socketio_instance:
            socketio_instance.emit('join_decision', {
                'room_id':    room_id,
                'profile_id': profile_id,
                'user_id':    str(p['user_id']) if p else None,
                'decision':   'accepted',
                'name':       name,
            }, room=room_id)

        return jsonify({'ok': True}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/study/rooms/<id>/requests/<profile_id>/reject ──
@study_bp.route('/rooms/<room_id>/requests/<profile_id>/reject', methods=['POST'])
@token_required
def reject_join_request(room_id, profile_id):
    user_id = g.current_user["id"]

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute('''
            SELECT r.id FROM study_rooms r
            JOIN student_profiles sp ON sp.id = r.host_id AND sp.user_id = %s
            WHERE r.id = %s
        ''', (str(user_id), room_id))
        if not cur.fetchone():
            return jsonify({'error': 'Accès refusé — hôte uniquement'}), 403

        cur.execute('''
            UPDATE study_room_participants
            SET status = 'rejected', is_present = FALSE
            WHERE room_id = %s AND student_id = %s AND status = 'pending'
        ''', (room_id, profile_id))
        conn.commit()

        cur.execute('SELECT user_id FROM student_profiles WHERE id = %s', (profile_id,))
        p = cur.fetchone()

        if socketio_instance:
            socketio_instance.emit('join_decision', {
                'room_id':    room_id,
                'profile_id': profile_id,
                'user_id':    str(p['user_id']) if p else None,
                'decision':   'rejected',
            }, room=room_id)

        return jsonify({'ok': True}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/study/solo/start ───────────────────────────────────────────────
@study_bp.route('/solo/start', methods=['POST'])
@token_required
def solo_start():
    user_id = g.current_user["id"]
    conn = get_conn()
    try:
        cur = conn.cursor()
        _ensure_solo_table(cur)
        session_id = str(uuid.uuid4())
        cur.execute(
            'INSERT INTO solo_study_sessions (id, user_id) VALUES (%s, %s)',
            (session_id, user_id)
        )
        conn.commit()
        return jsonify({'session_id': session_id}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/study/solo/end ─────────────────────────────────────────────────
@study_bp.route('/solo/end', methods=['POST'])
@token_required
def solo_end():
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    duration_minutes = data.get('duration_minutes', 0)
    if not session_id:
        return jsonify({'error': 'session_id requis'}), 400
    user_id = g.current_user["id"]
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            UPDATE solo_study_sessions
            SET ended_at = NOW(), duration_minutes = %s
            WHERE id = %s AND user_id = %s AND ended_at IS NULL
        ''', (float(duration_minutes), session_id, user_id))
        conn.commit()
        return jsonify({'ok': True}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/study/solo/stats ────────────────────────────────────────────────
@study_bp.route('/solo/stats', methods=['GET'])
@token_required
def solo_stats():
    user_id = g.current_user["id"]
    conn = get_conn()
    try:
        cur = conn.cursor()
        _ensure_solo_table(cur)
        conn.commit()
        cur.execute('''
            SELECT
                COALESCE(SUM(duration_minutes), 0) AS total_minutes,
                COUNT(*) FILTER (WHERE ended_at IS NOT NULL) AS total_sessions,
                MAX(ended_at) AS last_session
            FROM solo_study_sessions
            WHERE user_id = %s
        ''', (user_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'total_hours': 0, 'total_sessions': 0, 'last_session': None}), 200
        total_minutes, total_sessions, last_session = row
        return jsonify({
            'total_hours': round(float(total_minutes) / 60, 1),
            'total_sessions': int(total_sessions),
            'last_session': last_session.isoformat() if last_session else None,
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)


# ── GET /api/study/stats ─────────────────────────────────────────────────────
@study_bp.route('/stats', methods=['GET'])
@token_required
def study_stats():
    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_solo_table(cur)
        conn.commit()

        cur.execute("SELECT id FROM student_profiles WHERE user_id = %s", (user_id,))
        profile_row = cur.fetchone()
        profile_id = str(profile_row["id"]) if profile_row else None

        # ── Solo aggregate ────────────────────────────────────────────────────
        cur.execute("""
            SELECT
                COALESCE(SUM(duration_minutes), 0)                                       AS total_solo_min,
                COUNT(*) FILTER (WHERE ended_at IS NOT NULL)                             AS total_solo_sess,
                COALESCE(SUM(duration_minutes) FILTER (
                    WHERE started_at >= NOW() - INTERVAL '7 days'), 0)                   AS week_solo_min,
                COALESCE(SUM(duration_minutes) FILTER (
                    WHERE started_at >= NOW() - INTERVAL '14 days'
                    AND   started_at <  NOW() - INTERVAL '7 days'), 0)                   AS prev_week_solo_min,
                COALESCE(SUM(duration_minutes) FILTER (
                    WHERE started_at >= date_trunc('month', NOW())), 0)                  AS month_solo_min,
                COALESCE(SUM(duration_minutes) FILTER (
                    WHERE started_at >= date_trunc('month', NOW() - INTERVAL '1 month')
                    AND   started_at <  date_trunc('month', NOW())), 0)                  AS prev_month_solo_min,
                COUNT(*) FILTER (WHERE ended_at IS NOT NULL
                    AND started_at >= NOW() - INTERVAL '7 days')                         AS week_solo_sess
            FROM solo_study_sessions
            WHERE user_id = %s AND ended_at IS NOT NULL
        """, (user_id,))
        solo = cur.fetchone()

        # ── Room aggregate ────────────────────────────────────────────────────
        room = dict(total_room_min=0, total_room_sess=0, week_room_min=0,
                    prev_week_room_min=0, month_room_min=0, prev_month_room_min=0,
                    week_room_sess=0)
        if profile_id:
            cur.execute("""
                SELECT
                    COALESCE(SUM(EXTRACT(EPOCH FROM(left_at-joined_at))/60), 0)           AS total_room_min,
                    COUNT(*) FILTER (WHERE left_at IS NOT NULL)                            AS total_room_sess,
                    COALESCE(SUM(EXTRACT(EPOCH FROM(left_at-joined_at))/60) FILTER (
                        WHERE joined_at >= NOW() - INTERVAL '7 days'), 0)                 AS week_room_min,
                    COALESCE(SUM(EXTRACT(EPOCH FROM(left_at-joined_at))/60) FILTER (
                        WHERE joined_at >= NOW() - INTERVAL '14 days'
                        AND   joined_at <  NOW() - INTERVAL '7 days'), 0)                 AS prev_week_room_min,
                    COALESCE(SUM(EXTRACT(EPOCH FROM(left_at-joined_at))/60) FILTER (
                        WHERE joined_at >= date_trunc('month', NOW())), 0)                AS month_room_min,
                    COALESCE(SUM(EXTRACT(EPOCH FROM(left_at-joined_at))/60) FILTER (
                        WHERE joined_at >= date_trunc('month', NOW() - INTERVAL '1 month')
                        AND   joined_at <  date_trunc('month', NOW())), 0)                AS prev_month_room_min,
                    COUNT(*) FILTER (WHERE left_at IS NOT NULL
                        AND joined_at >= NOW() - INTERVAL '7 days')                       AS week_room_sess
                FROM study_room_participants
                WHERE student_id = %s
                  AND left_at IS NOT NULL
                  AND EXTRACT(EPOCH FROM(left_at-joined_at)) > 0
            """, (profile_id,))
            r = cur.fetchone()
            if r:
                room = {k: float(v) if v is not None else 0 for k, v in r.items()}

        # ── Daily stats (last 7 days) ─────────────────────────────────────────
        cur.execute("""
            SELECT (started_at AT TIME ZONE 'UTC')::date AS day,
                   SUM(duration_minutes) AS minutes
            FROM solo_study_sessions
            WHERE user_id = %s AND ended_at IS NOT NULL
              AND started_at >= NOW() - INTERVAL '7 days'
            GROUP BY 1
        """, (user_id,))
        solo_daily = {str(r["day"]): float(r["minutes"]) for r in cur.fetchall()}

        room_daily = {}
        if profile_id:
            cur.execute("""
                SELECT (joined_at AT TIME ZONE 'UTC')::date AS day,
                       SUM(EXTRACT(EPOCH FROM(left_at-joined_at))/60) AS minutes
                FROM study_room_participants
                WHERE student_id = %s AND left_at IS NOT NULL
                  AND EXTRACT(EPOCH FROM(left_at-joined_at)) > 0
                  AND joined_at >= NOW() - INTERVAL '7 days'
                GROUP BY 1
            """, (profile_id,))
            room_daily = {str(r["day"]): float(r["minutes"]) for r in cur.fetchall()}

        today = date.today()
        daily_stats = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            ds = str(d)
            mins = solo_daily.get(ds, 0) + room_daily.get(ds, 0)
            daily_stats.append({"date": ds, "minutes": round(mins, 1), "weekday": d.weekday()})

        # ── Streak ────────────────────────────────────────────────────────────
        cur.execute("""
            SELECT DISTINCT (started_at AT TIME ZONE 'UTC')::date AS d
            FROM solo_study_sessions WHERE user_id = %s AND ended_at IS NOT NULL
        """, (user_id,))
        solo_dates = {r["d"] for r in cur.fetchall()}

        room_dates = set()
        if profile_id:
            cur.execute("""
                SELECT DISTINCT (joined_at AT TIME ZONE 'UTC')::date AS d
                FROM study_room_participants WHERE student_id = %s AND left_at IS NOT NULL
            """, (profile_id,))
            room_dates = {r["d"] for r in cur.fetchall()}

        all_dates = sorted(solo_dates | room_dates, reverse=True)
        streak, expected = 0, today
        for d in all_dates:
            if d == expected:
                streak += 1
                expected = d - timedelta(days=1)
            else:
                break

        # ── Best day of week ──────────────────────────────────────────────────
        dow_names = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]
        cur.execute("""
            SELECT EXTRACT(ISODOW FROM (started_at AT TIME ZONE 'UTC')) AS dow,
                   SUM(duration_minutes) AS minutes
            FROM solo_study_sessions
            WHERE user_id = %s AND ended_at IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC LIMIT 1
        """, (user_id,))
        best_row = cur.fetchone()
        best_day = dow_names[int(best_row["dow"]) - 1] if best_row else None

        # ── Recent sessions ───────────────────────────────────────────────────
        cur.execute("""
            SELECT started_at AS ts, duration_minutes AS minutes
            FROM solo_study_sessions
            WHERE user_id = %s AND ended_at IS NOT NULL
            ORDER BY started_at DESC LIMIT 10
        """, (user_id,))
        recent_solo = [{"type":"solo","ts":r["ts"].isoformat(),
                        "minutes":round(float(r["minutes"]),1),"subject":None}
                       for r in cur.fetchall()]

        recent_rooms = []
        if profile_id:
            cur.execute("""
                SELECT srp.joined_at AS ts,
                       EXTRACT(EPOCH FROM(srp.left_at-srp.joined_at))/60 AS minutes,
                       sr.sujet AS subject
                FROM study_room_participants srp
                JOIN study_rooms sr ON sr.id = srp.room_id
                WHERE srp.student_id = %s AND srp.left_at IS NOT NULL
                  AND EXTRACT(EPOCH FROM(srp.left_at-srp.joined_at)) > 0
                ORDER BY srp.joined_at DESC LIMIT 10
            """, (profile_id,))
            recent_rooms = [{"type":"room","ts":r["ts"].isoformat(),
                             "minutes":round(float(r["minutes"]),1),"subject":r["subject"]}
                            for r in cur.fetchall()]

        recent = sorted(recent_solo + recent_rooms, key=lambda x: x["ts"], reverse=True)[:10]

        # ── Assemble ──────────────────────────────────────────────────────────
        total_solo_min = float(solo["total_solo_min"])
        total_room_min = float(room["total_room_min"])

        return jsonify({
            "total_minutes":      round(total_solo_min + total_room_min, 1),
            "total_sessions":     int(solo["total_solo_sess"]) + int(room["total_room_sess"]),
            "this_week_minutes":  round(float(solo["week_solo_min"]) + float(room["week_room_min"]), 1),
            "last_week_minutes":  round(float(solo["prev_week_solo_min"]) + float(room["prev_week_room_min"]), 1),
            "this_month_minutes": round(float(solo["month_solo_min"]) + float(room["month_room_min"]), 1),
            "last_month_minutes": round(float(solo["prev_month_solo_min"]) + float(room["prev_month_room_min"]), 1),
            "week_solo_sessions": int(solo["week_solo_sess"]),
            "week_room_sessions": int(room["week_room_sess"]),
            "streak_days":        streak,
            "solo_minutes":       round(total_solo_min, 1),
            "room_minutes":       round(total_room_min, 1),
            "best_day":           best_day,
            "daily_stats":        daily_stats,
            "recent_sessions":    recent,
        }), 200

    except Exception:
        print("STUDY STATS ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)