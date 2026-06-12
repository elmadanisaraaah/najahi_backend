"""
Concours eligibility alert job.
Runs once on startup (after 30 s) then every 24 h.
For every active concours whose registration opens within 14 days,
notifies eligible users who have not already been alerted.
"""
import threading
import time
import traceback
from datetime import date, timedelta

from db import get_conn, release_conn
from psycopg2.extras import RealDictCursor
from routes.notifications import send_notification

_MONTHS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

# Category eligibility — uses substring matching to handle both profile BAC formats
# ("Sciences Maths", "Bac Sciences Maths A", etc.)
def _is_eligible(type_bac: str, category: str) -> bool:
    if not type_bac:
        return False
    tb = type_bac.lower()
    cat = category.lower()

    if "maths" in tb or "sm" == tb.strip():
        return True  # Sciences Maths: eligible for everything

    if "physiques" in tb or "sp" == tb.strip():
        return any(k in cat for k in ("ingénierie", "ingenierie", "commerce", "management",
                                      "architecture", "concours", "université", "universite",
                                      "informatique", "préparatoire", "preparatoire"))

    if "vie" in tb or "svt" in tb or "biologie" in tb:
        return any(k in cat for k in ("médecine", "medecine", "commerce", "management",
                                      "université", "universite"))

    if "éco" in tb or "eco" in tb:
        return any(k in cat for k in ("commerce", "management",
                                      "université", "universite"))

    if "lettre" in tb or "humanit" in tb:
        return any(k in cat for k in ("architecture", "commerce", "management",
                                      "université", "universite"))

    if "technique" in tb or "bts" in tb or "électrique" in tb or "electrique" in tb:
        return any(k in cat for k in ("ingénierie", "ingenierie",
                                      "université", "universite"))

    # Unknown BAC type → only notify for broadly open concours
    return "université" in cat or "universite" in cat


def _ensure_alert_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS concours_alerts_sent (
                user_id     UUID NOT NULL,
                concours_id UUID NOT NULL,
                sent_at     TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (user_id, concours_id)
            )
        """)
    conn.commit()


def check_concours_alerts():
    today    = date.today()
    deadline = today + timedelta(days=14)
    conn     = get_conn()
    try:
        _ensure_alert_table(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Active concours opening within the next 14 days
        cur.execute("""
            SELECT id, name, school, category, registration_start
            FROM concours_calendar
            WHERE is_active = TRUE
              AND registration_start IS NOT NULL
              AND registration_start BETWEEN %s AND %s
        """, (today, deadline))
        upcoming = cur.fetchall()
        if not upcoming:
            print("[alerts] no upcoming concours within 14 days")
            cur.close()
            return

        # Users who have a BAC type in their profile
        cur.execute("""
            SELECT u.id AS user_id, sp.type_bac
            FROM users u
            JOIN student_profiles sp ON sp.user_id = u.id
            WHERE sp.type_bac IS NOT NULL AND sp.type_bac <> ''
        """)
        users = cur.fetchall()

        notified = 0
        for c in upcoming:
            d = c["registration_start"]
            date_str = f"{d.day} {_MONTHS_FR[d.month]} {d.year}"
            for u in users:
                if not _is_eligible(u["type_bac"], c["category"]):
                    continue
                # Dedup: only send once per user/concours pair
                cur.execute("""
                    SELECT 1 FROM concours_alerts_sent
                    WHERE user_id = %s AND concours_id = %s
                """, (str(u["user_id"]), str(c["id"])))
                if cur.fetchone():
                    continue
                send_notification(
                    user_id=u["user_id"],
                    title=f"Concours {c['name']} — ouverture le {date_str}",
                    message=(
                        f"Le concours {c['name']} ({c['school']}) ouvre ses inscriptions "
                        f"le {date_str}. Tu es éligible avec ton profil !"
                    ),
                    type="concours",
                    link="/app/concours",
                )
                cur.execute("""
                    INSERT INTO concours_alerts_sent (user_id, concours_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, (str(u["user_id"]), str(c["id"])))
                conn.commit()
                notified += 1

        print(f"[alerts] sent {notified} eligibility notification(s)")
        cur.close()
    except Exception:
        print("[alerts] ERROR:", traceback.format_exc())
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        release_conn(conn)


def _alert_loop():
    time.sleep(30)  # Let the app finish starting up
    while True:
        try:
            check_concours_alerts()
        except Exception:
            print("[alerts] uncaught error in loop:", traceback.format_exc())
        time.sleep(86400)  # Every 24 hours


def start_alert_scheduler():
    t = threading.Thread(target=_alert_loop, daemon=True, name="alert-scheduler")
    t.start()
    print("[alerts] daily eligibility scheduler started")
