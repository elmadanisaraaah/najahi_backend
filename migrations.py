"""
Startup migration checks — safe to re-run on every startup (all idempotent).

Adds ALTER TABLE ... ADD COLUMN IF NOT EXISTS for every column that is
referenced in routes/*.py queries but was not present in the original
CREATE TABLE definitions (auth_schema.sql or lazy-init _ensure_* functions).

Columns on tables that are themselves lazily created (study_rooms, forum_*)
are wrapped in SAVEPOINT blocks and silently skipped when the parent table
doesn't exist yet (e.g. a fresh Railway deploy before any user hits those routes).
"""

from db import get_conn, release_conn


def _safe(cur, sql, label=""):
    """Execute sql inside a savepoint; roll back and warn on failure."""
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
            # student_profiles is always created by auth_schema.sql, so these
            # ALTER TABLEs are unconditional (no savepoint needed).

            # show_in_leaderboard: directly referenced in profile.py GET /me as
            # COALESCE(p.show_in_leaderboard, FALSE) — without this column that
            # query fails.  Also used in study.py leaderboard, but its ADD COLUMN
            # only runs when that specific endpoint is hit, not on startup.
            cur.execute("""
                ALTER TABLE student_profiles
                    ADD COLUMN IF NOT EXISTS show_in_leaderboard BOOLEAN DEFAULT FALSE
            """)

            # type_ecole / nom_ecole: soft-referenced in auth.py register
            # (guarded by information_schema check) but the DB row is silently
            # dropped when these columns are absent.
            cur.execute("""
                ALTER TABLE student_profiles
                    ADD COLUMN IF NOT EXISTS type_ecole VARCHAR(120)
            """)
            cur.execute("""
                ALTER TABLE student_profiles
                    ADD COLUMN IF NOT EXISTS nom_ecole  VARCHAR(200)
            """)

            # ── study_rooms ───────────────────────────────────────────────────
            # study_rooms is not created in auth_schema.sql (it was created
            # manually / via socket setup).  Use savepoints so a fresh deploy
            # where the table doesn't exist yet doesn't abort the migration.

            _safe(cur,
                  "ALTER TABLE study_rooms "
                  "ADD COLUMN IF NOT EXISTS category VARCHAR(20) DEFAULT 'general'",
                  "study_rooms.category")

            _safe(cur,
                  "ALTER TABLE study_rooms "
                  "ADD COLUMN IF NOT EXISTS tag VARCHAR(100)",
                  "study_rooms.tag")

            # ── forum_posts ───────────────────────────────────────────────────
            # forum_posts is created lazily in forum.py on the first forum
            # request; is_pinned / is_locked were added via admin.py helper
            # that only runs when admin endpoints are called.

            _safe(cur,
                  "ALTER TABLE forum_posts "
                  "ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE",
                  "forum_posts.is_pinned")

            _safe(cur,
                  "ALTER TABLE forum_posts "
                  "ADD COLUMN IF NOT EXISTS is_locked BOOLEAN DEFAULT FALSE",
                  "forum_posts.is_locked")

            # ── forum_replies ─────────────────────────────────────────────────
            # parent_reply_id enables nested replies.  Already added in
            # forum.py _ensure_tables(), but that function is gated by a
            # module-level flag and only runs once after the first forum request,
            # not on startup.

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
