import uuid, random, string
from flask import Blueprint, request, jsonify, g
from db import get_conn, release_conn
from middleware import token_required

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
        cur.execute('''
            SELECT r.id, r.nom, r.sujet, r.code_acces,
                   r.max_participants, r.pomodoro_work, r.pomodoro_break,
                   COUNT(p.student_id) FILTER (WHERE p.is_present) AS participant_count
            FROM study_rooms r
            LEFT JOIN study_room_participants p ON p.room_id = r.id
            WHERE r.is_active = TRUE AND r.is_public = TRUE
            GROUP BY r.id
            ORDER BY r.created_at DESC
        ''')
        cols = ['id','nom','sujet','code_acces','max_participants',
                'pomodoro_work','pomodoro_break','participant_count']
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        for r in rows: r['id'] = str(r['id'])
        return jsonify(rows), 200
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

        room_id = str(uuid.uuid4())
        cur.execute('''
            INSERT INTO study_rooms
            (id, host_id, nom, sujet, code_acces, max_participants,
             is_public, pomodoro_work, pomodoro_break)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ''', (room_id, profile_id, nom,
              data.get('sujet') or None,
              code,
              data.get('max_participants', 10),
              data.get('is_public', True),
              data.get('pomodoro_work', 25),
              data.get('pomodoro_break', 5)))

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
        }), 201
    except Exception as e:
        conn.rollback()
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
            cur.execute('''
                INSERT INTO study_room_participants (room_id, student_id)
                VALUES (%s, %s) ON CONFLICT (room_id, student_id)
                DO UPDATE SET is_present = TRUE, joined_at = NOW()
            ''', (room['id'], str(profile[0])))
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
        cur = conn.cursor()
        cur.execute('SELECT id FROM student_profiles WHERE user_id = %s', (user_id,))
        profile = cur.fetchone()
        if not profile:
            return jsonify({'error': 'Profil introuvable'}), 404
        cur.execute('''
            INSERT INTO study_room_participants (room_id, student_id)
            VALUES (%s, %s) ON CONFLICT (room_id, student_id)
            DO UPDATE SET is_present = TRUE, joined_at = NOW()
        ''', (room_id, str(profile[0])))
        conn.commit()
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
            cur.execute('''
                UPDATE study_room_participants
                SET is_present = FALSE, left_at = NOW()
                WHERE room_id = %s AND student_id = %s
            ''', (room_id, str(profile[0])))
            conn.commit()
        return jsonify({'ok': True}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); release_conn(conn)