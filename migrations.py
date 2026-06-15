"""
Startup migration checks — safe to re-run on every startup (all idempotent).

Strategy:
- Tables whose CREATE TABLE lives in auth_schema.sql (users, student_profiles…)
  get unconditional ALTER TABLE ADD COLUMN IF NOT EXISTS calls.
- Tables that had no CREATE TABLE anywhere in the codebase (study_rooms,
  study_room_participants) are created here with CREATE TABLE IF NOT EXISTS
  so the subsequent ALTER TABLEs are always safe, regardless of deploy order.
- Tables created lazily by route files (forum_posts, forum_replies…) keep
  SAVEPOINT wrappers as a belt-and-suspenders guard.
"""

from db import get_conn, release_conn


def _safe(cur, sql, label=""):
    """Run sql inside a savepoint; silently roll back if the table doesn't exist."""
    cur.execute("SAVEPOINT _mg")
    try:
        cur.execute(sql)
        cur.execute("RELEASE SAVEPOINT _mg")
    except Exception as exc:
        cur.execute("ROLLBACK TO SAVEPOINT _mg")
        table = label or sql.strip().split()[2]
        print(f"[migrations] skipped {table!r}: {exc}")


def run_migrations():
    conn = get_conn()
    try:
        with conn.cursor() as cur:

            # ── student_profiles ──────────────────────────────────────────────
            # Always exists (created in auth_schema.sql) — no savepoint needed.

            # show_in_leaderboard: referenced directly in profile.py GET /me as
            # COALESCE(p.show_in_leaderboard, FALSE).  Without this column the
            # query fails hard before study.py's leaderboard handler ever adds it.
            cur.execute("""
                ALTER TABLE student_profiles
                    ADD COLUMN IF NOT EXISTS show_in_leaderboard BOOLEAN DEFAULT FALSE
            """)
            # type_ecole / nom_ecole: soft-referenced in auth.py registration
            # (information_schema guard), but silently dropped when absent.
            cur.execute("""
                ALTER TABLE student_profiles
                    ADD COLUMN IF NOT EXISTS type_ecole VARCHAR(120)
            """)
            cur.execute("""
                ALTER TABLE student_profiles
                    ADD COLUMN IF NOT EXISTS nom_ecole  VARCHAR(200)
            """)

            # ── study_rooms ───────────────────────────────────────────────────
            # This table was created manually in Railway and has no CREATE TABLE
            # anywhere in the codebase.  We declare it here so:
            #   1. A fresh deploy gets the table with all columns from the start.
            #   2. An existing deploy gets a no-op CREATE + safe ALTER for any
            #      missing columns (category / tag were added after the table was
            #      first created, and the per-request ALTER in list_rooms() was
            #      removed in a previous commit).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS study_rooms (
                    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    host_id          UUID REFERENCES student_profiles(id) ON DELETE CASCADE,
                    nom              VARCHAR(200) NOT NULL,
                    sujet            VARCHAR(300),
                    code_acces       VARCHAR(20) UNIQUE NOT NULL,
                    max_participants INTEGER DEFAULT 10,
                    is_public        BOOLEAN DEFAULT TRUE,
                    is_active        BOOLEAN DEFAULT TRUE,
                    pomodoro_work    INTEGER DEFAULT 25,
                    pomodoro_break   INTEGER DEFAULT 5,
                    category         VARCHAR(20) DEFAULT 'general',
                    tag              VARCHAR(100),
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Also ensure the columns exist on tables that pre-date them.
            cur.execute("""
                ALTER TABLE study_rooms
                    ADD COLUMN IF NOT EXISTS category VARCHAR(20) DEFAULT 'general'
            """)
            cur.execute("""
                ALTER TABLE study_rooms
                    ADD COLUMN IF NOT EXISTS tag VARCHAR(100)
            """)
            # pomodoro_work / pomodoro_break: used in every study room list/create/join
            # query in study.py but absent from the Railway-deployed table (the CREATE TABLE
            # above only runs on fresh deploys; existing tables need explicit ALTERs).
            cur.execute("""
                ALTER TABLE study_rooms
                    ADD COLUMN IF NOT EXISTS pomodoro_work INTEGER DEFAULT 25
            """)
            cur.execute("""
                ALTER TABLE study_rooms
                    ADD COLUMN IF NOT EXISTS pomodoro_break INTEGER DEFAULT 5
            """)

            # ── study_room_participants ───────────────────────────────────────
            # Same situation: no CREATE TABLE in codebase, created manually.
            # is_present / joined_at / left_at are referenced throughout study.py.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS study_room_participants (
                    room_id    UUID REFERENCES study_rooms(id) ON DELETE CASCADE,
                    student_id UUID REFERENCES student_profiles(id) ON DELETE CASCADE,
                    is_present BOOLEAN DEFAULT FALSE,
                    joined_at  TIMESTAMPTZ DEFAULT NOW(),
                    left_at    TIMESTAMPTZ,
                    PRIMARY KEY (room_id, student_id)
                )
            """)
            cur.execute("""
                ALTER TABLE study_room_participants
                    ADD COLUMN IF NOT EXISTS is_present BOOLEAN DEFAULT FALSE
            """)
            cur.execute("""
                ALTER TABLE study_room_participants
                    ADD COLUMN IF NOT EXISTS joined_at TIMESTAMPTZ DEFAULT NOW()
            """)
            cur.execute("""
                ALTER TABLE study_room_participants
                    ADD COLUMN IF NOT EXISTS left_at TIMESTAMPTZ
            """)
            # status: pending/accepted/rejected — used by join-approval system
            cur.execute("""
                ALTER TABLE study_room_participants
                    ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'accepted'
            """)

            # ── forum_posts ───────────────────────────────────────────────────
            # Created lazily by forum.py on first forum request.
            # is_pinned / is_locked were added via admin.py helper called only
            # when admin moderation endpoints run.
            _safe(cur,
                  "ALTER TABLE forum_posts "
                  "ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE",
                  "forum_posts.is_pinned")
            _safe(cur,
                  "ALTER TABLE forum_posts "
                  "ADD COLUMN IF NOT EXISTS is_locked BOOLEAN DEFAULT FALSE",
                  "forum_posts.is_locked")

            # ── forum_replies ─────────────────────────────────────────────────
            # parent_reply_id enables nested replies; added lazily in forum.py
            # _ensure_tables() which is gated by a module-level flag.
            _safe(cur, """
                ALTER TABLE forum_replies
                    ADD COLUMN IF NOT EXISTS parent_reply_id UUID
                    REFERENCES forum_replies(id) ON DELETE SET NULL
            """, "forum_replies.parent_reply_id")

        conn.commit()
        print("[migrations] All startup migrations applied successfully")

    except Exception as exc:
        conn.rollback()
        print(f"[migrations] ERROR during startup migrations: {exc}")
    finally:
        release_conn(conn)
