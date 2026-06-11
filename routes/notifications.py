import json
import os
import traceback
from functools import wraps
from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
from middleware import token_required

try:
    from pywebpush import webpush, WebPushException
    _WEBPUSH_AVAILABLE = True
except ImportError:
    _WEBPUSH_AVAILABLE = False

notifications_bp = Blueprint("notifications", __name__)

_READY = False
_PUSH_READY = False


def _ensure_table():
    global _READY
    if _READY:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                    title VARCHAR(200) NOT NULL,
                    message TEXT NOT NULL,
                    type VARCHAR(50) DEFAULT 'info',
                    link TEXT,
                    is_read BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_notifications_user_id
                ON notifications(user_id, created_at DESC)
            """)
        conn.commit()
        _READY = True
    except Exception:
        print("NOTIFICATIONS TABLE ERROR:", traceback.format_exc())
        conn.rollback()
    finally:
        release_conn(conn)


def _ensure_push_table():
    global _PUSH_READY
    if _PUSH_READY:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                    endpoint TEXT NOT NULL UNIQUE,
                    p256dh TEXT,
                    auth_key TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_push_subs_user_id
                ON push_subscriptions(user_id)
            """)
        conn.commit()
        _PUSH_READY = True
    except Exception:
        print("PUSH TABLE ERROR:", traceback.format_exc())
        conn.rollback()
    finally:
        release_conn(conn)


_ensure_table()
_ensure_push_table()


# ── Public helpers — import these in other modules ────────────────────────────

def send_push_to_user(user_id, title, body, link=None):
    """Send a browser push notification to all subscriptions for a user. Never raises."""
    if not _WEBPUSH_AVAILABLE:
        return
    vapid_key   = os.getenv("VAPID_PRIVATE_KEY", "")
    vapid_email = os.getenv("VAPID_EMAIL", "mailto:contact@najahi.app")
    if not vapid_key:
        return

    _ensure_push_table()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT endpoint, p256dh, auth_key FROM push_subscriptions WHERE user_id = %s",
                (str(user_id),)
            )
            subs = cur.fetchall()
    except Exception:
        return
    finally:
        release_conn(conn)

    if not subs:
        return

    payload = json.dumps({"title": title, "body": body, "link": link or "/app/notifications"})
    stale = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth_key"]},
                },
                data=payload,
                vapid_private_key=vapid_key,
                vapid_claims={"sub": vapid_email},
            )
        except Exception as ex:
            resp = getattr(ex, "response", None)
            if resp and resp.status_code in (404, 410):
                stale.append(sub["endpoint"])
            else:
                print("PUSH SEND ERROR:", ex)

    if stale:
        conn2 = get_conn()
        try:
            with conn2.cursor() as cur2:
                for ep in stale:
                    cur2.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (ep,))
            conn2.commit()
        except Exception:
            conn2.rollback()
        finally:
            release_conn(conn2)


def send_notification(user_id, title, message, type="info", link=None):
    """Fire-and-forget: insert one notification row + send browser push. Never raises."""
    _ensure_table()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO notifications (user_id, title, message, type, link)
                VALUES (%s, %s, %s, %s, %s)
            """, (str(user_id), title, message, type, link))
        conn.commit()
    except Exception:
        print("send_notification ERROR:", traceback.format_exc())
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        release_conn(conn)

    try:
        send_push_to_user(user_id, title, message, link)
    except Exception:
        pass


def send_notification_to_all(title, message, type="info", link=None):
    """Send a notification to every active user."""
    _ensure_table()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM users WHERE status = 'active'")
            user_ids = [str(r["id"]) for r in cur.fetchall()]
        if not user_ids:
            return
        conn2 = get_conn()
        try:
            with conn2.cursor() as cur2:
                for uid in user_ids:
                    cur2.execute("""
                        INSERT INTO notifications (user_id, title, message, type, link)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (uid, title, message, type, link))
            conn2.commit()
        except Exception:
            print("send_notification_to_all INSERT ERROR:", traceback.format_exc())
            conn2.rollback()
        finally:
            release_conn(conn2)
    except Exception:
        print("send_notification_to_all ERROR:", traceback.format_exc())
    finally:
        release_conn(conn)


def _admin_required(fn):
    @wraps(fn)
    @token_required
    def wrapper(*args, **kwargs):
        if g.current_user.get("role") != "admin":
            return jsonify({"error": "Accès refusé — admin uniquement"}), 403
        return fn(*args, **kwargs)
    return wrapper


def _row(r):
    return {
        "id":         str(r["id"]),
        "title":      r["title"],
        "message":    r["message"],
        "type":       r["type"],
        "link":       r["link"],
        "is_read":    r["is_read"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


# ── GET /api/notifications ────────────────────────────────────────────────────

@notifications_bp.route("", methods=["GET"])
@token_required
def get_notifications():
    _ensure_table()
    user_id = str(g.current_user["id"])
    limit   = min(int(request.args.get("limit", 50)), 100)
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM notifications
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (user_id, limit))
        rows = cur.fetchall()
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM notifications WHERE user_id = %s AND is_read = FALSE",
            (user_id,)
        )
        unread = cur.fetchone()["cnt"]
        return jsonify({"notifications": [_row(r) for r in rows], "unread_count": int(unread)}), 200
    except Exception:
        print("GET NOTIFICATIONS ERROR:", traceback.format_exc())
        return jsonify({"notifications": [], "unread_count": 0}), 200
    finally:
        cur.close(); release_conn(conn)


# ── PUT /api/notifications/<id>/read ─────────────────────────────────────────

@notifications_bp.route("/<notif_id>/read", methods=["PUT"])
@token_required
def mark_read(notif_id):
    _ensure_table()
    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "UPDATE notifications SET is_read = TRUE WHERE id = %s AND user_id = %s RETURNING *",
            (notif_id, user_id)
        )
        row = cur.fetchone()
        conn.commit()
        if not row:
            return jsonify({"error": "Notification introuvable"}), 404
        return jsonify(_row(row)), 200
    except Exception:
        conn.rollback()
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── PUT /api/notifications/read-all ──────────────────────────────────────────

@notifications_bp.route("/read-all", methods=["PUT"])
@token_required
def mark_all_read():
    _ensure_table()
    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE notifications SET is_read = TRUE WHERE user_id = %s AND is_read = FALSE",
            (user_id,)
        )
        conn.commit()
        return jsonify({"ok": True}), 200
    except Exception:
        conn.rollback()
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── DELETE /api/notifications/<id> ───────────────────────────────────────────

@notifications_bp.route("/<notif_id>", methods=["DELETE"])
@token_required
def delete_notification(notif_id):
    _ensure_table()
    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM notifications WHERE id = %s AND user_id = %s",
            (notif_id, user_id)
        )
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Notification introuvable"}), 404
        conn.commit()
        return jsonify({"ok": True}), 200
    except Exception:
        conn.rollback()
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/notifications/push-subscribe ───────────────────────────────────

@notifications_bp.route("/push-subscribe", methods=["POST"])
@token_required
def push_subscribe():
    _ensure_push_table()
    user_id  = str(g.current_user["id"])
    data     = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    p256dh   = (data.get("p256dh")   or "").strip()
    auth_key = (data.get("auth")     or "").strip()

    if not endpoint:
        return jsonify({"error": "endpoint requis"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth_key)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (endpoint) DO UPDATE
            SET user_id = EXCLUDED.user_id,
                p256dh  = EXCLUDED.p256dh,
                auth_key = EXCLUDED.auth_key
        """, (user_id, endpoint, p256dh, auth_key))
        conn.commit()
        return jsonify({"ok": True}), 201
    except Exception:
        conn.rollback()
        print("PUSH SUBSCRIBE ERROR:", traceback.format_exc())
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close(); release_conn(conn)


# ── POST /api/notifications/send  (admin) ────────────────────────────────────

@notifications_bp.route("/send", methods=["POST"])
@_admin_required
def admin_send():
    _ensure_table()
    data    = request.get_json(silent=True) or {}
    title   = (data.get("title") or "").strip()
    message = (data.get("message") or "").strip()
    ntype   = (data.get("type") or "info").strip()
    link    = (data.get("link") or "").strip() or None
    user_id = (data.get("user_id") or "").strip() or None

    if not title or not message:
        return jsonify({"error": "title et message requis"}), 400

    if user_id:
        send_notification(user_id, title, message, ntype, link)
        return jsonify({"ok": True, "sent_to": "user", "user_id": user_id}), 200
    else:
        send_notification_to_all(title, message, ntype, link)
        return jsonify({"ok": True, "sent_to": "all"}), 200
