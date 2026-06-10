import os
import io
import cloudinary
import cloudinary.uploader
from flask import Blueprint, request, jsonify, g
from db import get_conn, release_conn
from middleware import token_required

cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
)

profile_bp = Blueprint('profile_photo', __name__, url_prefix='/api/profile')


@profile_bp.route('/avatar', methods=['POST'])
@token_required
def upload_avatar():
    file = request.files.get('avatar')
    if not file or not file.filename:
        return jsonify({'error': 'Aucun fichier fourni'}), 400

    if file.mimetype not in {'image/jpeg', 'image/png', 'image/webp'}:
        return jsonify({'error': 'Format non supporté (JPG, PNG, WEBP)'}), 400

    content = file.read()
    if len(content) > 10 * 1024 * 1024:
        return jsonify({'error': 'Image trop grande (max 10 MB)'}), 400

    user = getattr(g, "current_user", None)
    if not user:
        return jsonify({'error': 'Utilisateur non authentifié'}), 401

    user_id = user["id"]

    try:
        result = cloudinary.uploader.upload(
            io.BytesIO(content),
            folder="najahi/avatars",
            public_id=str(user_id),
            overwrite=True,
        )
        avatar_url = result["secure_url"]
    except Exception as e:
        return jsonify({'error': f'Erreur Cloudinary: {str(e)}'}), 500

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
