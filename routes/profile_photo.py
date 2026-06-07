import os
import uuid
from flask import Blueprint, request, jsonify, g
from db import get_conn, release_conn
from middleware import token_required

profile_bp = Blueprint('profile_photo', __name__, url_prefix='/api/profile')

AVATAR_DIR = os.getenv('AVATAR_DIR', 'uploads/avatars')
os.makedirs(AVATAR_DIR, exist_ok=True)


@profile_bp.route('/avatar', methods=['POST'])
@token_required
def upload_avatar():
    file = request.files.get('avatar')
    if not file or not file.filename:
        return jsonify({'error': 'Aucun fichier fourni'}), 400

    if file.mimetype not in {'image/jpeg', 'image/png', 'image/webp'}:
        return jsonify({'error': 'Format non supporté (JPG, PNG, WEBP)'}), 400

    content = file.read()
    if len(content) > 2 * 1024 * 1024:
        return jsonify({'error': 'Image trop grande (max 2 MB)'}), 400

    user = getattr(g, "current_user", None)
    if not user:
        return jsonify({'error': 'Utilisateur non authentifié'}), 401

    user_id = user["id"]

    ext = file.filename.rsplit('.', 1)[-1].lower()
    filename = f"{user_id}_{uuid.uuid4().hex[:6]}.{ext}"
    filepath = os.path.join(AVATAR_DIR, filename)

    with open(filepath, 'wb') as f:
        f.write(content)

    avatar_url = f"/uploads/avatars/{filename}"

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            'UPDATE student_profiles SET avatar_url = %s WHERE user_id = %s',
            (avatar_url, user_id)
        )
        conn.commit()
        return jsonify({'avatarUrl': avatar_url}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        release_conn(conn)