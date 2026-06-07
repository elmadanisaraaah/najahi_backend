from flask import Blueprint, jsonify

chatbot_bp = Blueprint('chatbot', __name__)

@chatbot_bp.route('/', methods=['GET'])
def chatbot_home():
    return jsonify({"message": "Chatbot route works"}), 200