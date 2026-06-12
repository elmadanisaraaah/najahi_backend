import os
from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO
from config import Config
from extensions import mail, limiter
from db import get_conn, release_conn
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
from routes.admin import admin_bp
from routes.forum import forum_bp
from routes.concours import concours_bp
from routes.notifications import notifications_bp
from routes.temoignages import temoignages_bp
from routes.alerts import start_alert_scheduler

def run_schema():
    schema_path = os.path.join(os.path.dirname(__file__), "models_sql", "auth_schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[schema] warning: {e}")
    finally:
        release_conn(conn)

app = Flask(__name__)
app.config.from_object(Config)
mail.init_app(app)
limiter.init_app(app)

run_schema()

CORS(app, resources={r"/api/*": {
    "origins": ["https://najahi-frontend.vercel.app", "http://localhost:5173", "*"],
    "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    "allow_headers": ["Content-Type", "Authorization"]
}})

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
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
app.register_blueprint(rooms_bp,  url_prefix="/api/rooms")
app.register_blueprint(admin_bp,    url_prefix="/api/admin")
app.register_blueprint(forum_bp,    url_prefix="/api/forum")
app.register_blueprint(concours_bp,       url_prefix="/api/concours")
app.register_blueprint(notifications_bp,  url_prefix="/api/notifications")
app.register_blueprint(temoignages_bp,    url_prefix="/api/temoignages")

register_socket_events(socketio)
start_alert_scheduler()

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

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