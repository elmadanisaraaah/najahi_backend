import os
from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO
from config import Config
from extensions import mail
from routes.auth import auth_bp
from routes.profile import profile_bp
from routes.profile_photo import profile_bp as photo_profile_bp
from routes.orientation import orientation_bp
from routes.schools import schools_bp
from routes.chatbot import chatbot_bp
from routes.study import study_bp
from routes.google_auth import google_auth_bp
from routes.servers import servers_bp
from routes.socket_events import register_socket_events
from routes.rooms import rooms_bp

app = Flask(__name__)
app.config.from_object(Config)
mail.init_app(app)

allowed_origins = os.environ.get("FRONTEND_URL", "http://localhost:5173")

CORS(
    app,
    resources={r"/api/*": {"origins": allowed_origins}},
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
    supports_credentials=True
)

socketio = SocketIO(
    app,
    cors_allowed_origins=allowed_origins,
    async_mode="threading"
)

app.register_blueprint(auth_bp,         url_prefix="/api/auth")
app.register_blueprint(profile_bp,      url_prefix="/api/profile")
app.register_blueprint(photo_profile_bp)
app.register_blueprint(orientation_bp,  url_prefix="/api/orientation")
app.register_blueprint(schools_bp,      url_prefix="/api/schools")
app.register_blueprint(chatbot_bp,      url_prefix="/api/chatbot")
app.register_blueprint(study_bp,        url_prefix="/api/study")
app.register_blueprint(google_auth_bp,  url_prefix="/api/auth")
app.register_blueprint(servers_bp,      url_prefix="/api/servers")
app.register_blueprint(rooms_bp, url_prefix="/api/rooms")

register_socket_events(socketio)

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "app": "Najahi API"}), 200

@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Route introuvable"}), 404

@app.errorhandler(405)
def method_not_allowed(_):
    return jsonify({"error": "Méthode non autorisée"}), 405

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)