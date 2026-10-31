"""First-run setup: materialise the policy documents and face.html onto disk,
and make sure the memory database has the tables/columns the app expects.
"""
import os
import sqlite3

from . import config
from .data.policies import POLICIES
from .data.face_html import FACE_HTML
from .identity import repository as identity_repository


def bootstrap():
    os.makedirs(config.WORK, exist_ok=True)
    os.makedirs(config.POLICY_DIR, exist_ok=True)
    for name, text in POLICIES.items():
        path = os.path.join(config.POLICY_DIR, name)
        if not os.path.exists(path):
            with open(path, "w") as f:
                f.write(text)
    if not os.path.exists(config.FACE_FILE):
        with open(config.FACE_FILE, "w") as f:
            f.write(FACE_HTML)
    conn = sqlite3.connect(config.MEM_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS notes (ts REAL, text TEXT, vec TEXT)")
    try:
        conn.execute("ALTER TABLE notes ADD COLUMN vec TEXT")   # migrate older DBs
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE notes ADD COLUMN user_id INTEGER")   # NULL = guest/unscoped
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE notes ADD COLUMN due_date TEXT")   # YYYY-MM-DD follow-through
    except Exception:
        pass
    conn.commit()
    _migrate_memory_schema(conn)
    conn.close()
    identity_repository.init_db()


def _migrate_memory_schema(conn):
    """Versioned migration: the typed memory_items/short_term_context/
    memory_summaries schema, plus a one-time copy of the legacy `notes` rows.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (id INTEGER PRIMARY KEY CHECK (id = 1), "
                "version INTEGER NOT NULL)")
    version = conn.execute("SELECT version FROM schema_meta WHERE id = 1").fetchone()
    version = version[0] if version else 0

    if version < 2:
        conn.execute("""CREATE TABLE IF NOT EXISTS memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT NOT NULL DEFAULT 'fact',
            content TEXT NOT NULL,
            vec TEXT,
            source TEXT NOT NULL DEFAULT 'explicit',
            confidence REAL NOT NULL DEFAULT 1.0,
            created_ts REAL NOT NULL,
            last_accessed_ts REAL,
            due_date TEXT,
            superseded_by INTEGER)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_items_user_ts "
                    "ON memory_items(user_id, created_ts DESC)")
        conn.execute("""CREATE TABLE IF NOT EXISTS short_term_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ts REAL NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_short_term_user_ts "
                    "ON short_term_context(user_id, ts DESC)")
        conn.execute("""CREATE TABLE IF NOT EXISTS memory_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            period_start REAL NOT NULL,
            period_end REAL NOT NULL,
            summary_text TEXT NOT NULL,
            created_ts REAL NOT NULL)""")
        # One-time copy of legacy notes -> memory_items (notes table stays as-is for rollback safety).
        existing = conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]
        if existing == 0:
            for ts, text, vec, user_id, due_date in conn.execute(
                    "SELECT ts, text, vec, user_id, due_date FROM notes"):
                conn.execute(
                    "INSERT INTO memory_items (user_id, type, content, vec, source, confidence, "
                    "created_ts, due_date) VALUES (?, ?, ?, ?, 'explicit', 1.0, ?, ?)",
                    (user_id, "reminder" if due_date else "fact", text, vec, ts, due_date))
        conn.execute("INSERT OR REPLACE INTO schema_meta (id, version) VALUES (1, 2)")

    if version < 3:
        # Attribute-aware facts (for conflict detection) + a conflict audit trail.
        try:
            conn.execute("ALTER TABLE memory_items ADD COLUMN attribute TEXT")
        except Exception:
            pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_items_user_attr ON memory_items(user_id, attribute) "
            "WHERE attribute IS NOT NULL AND superseded_by IS NULL")
        conn.execute("""CREATE TABLE IF NOT EXISTS memory_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            attribute TEXT,
            old_text TEXT NOT NULL,
            new_text TEXT NOT NULL,
            resolution TEXT NOT NULL,
            created_ts REAL NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_conflicts_user_ts "
                    "ON memory_conflicts(user_id, created_ts DESC)")
        conn.execute("INSERT OR REPLACE INTO schema_meta (id, version) VALUES (1, 3)")

    if version < 4:
        # Shopping/to-do lists -- deliberately separate from memory_items,
        # a grocery list isn't a "personal fact" the LLM should recall.
        conn.execute("""CREATE TABLE IF NOT EXISTS list_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            created_ts REAL NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_list_items_user "
                    "ON list_items(user_id, done, created_ts)")
        # Recurring daily alarms -- persist across restarts, unlike the
        # in-memory one-shot threading.Timer timers.
        conn.execute("""CREATE TABLE IF NOT EXISTS alarms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            hour INTEGER NOT NULL,
            minute INTEGER NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            last_fired_date TEXT NOT NULL DEFAULT '',
            created_ts REAL NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alarms_time ON alarms(hour, minute, active)")
        conn.execute("INSERT OR REPLACE INTO schema_meta (id, version) VALUES (1, 4)")

    if version < 5:
        # Proactive opportunity engine: transient engagement state, kept
        # separate from memory_items (not durable personal facts) and from
        # identity.repository (not account data).
        conn.execute("""CREATE TABLE IF NOT EXISTS opportunity_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            topic_summary TEXT NOT NULL,
            category TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'awaiting_info',
            collected_info TEXT NOT NULL DEFAULT '{}',
            next_question TEXT,
            final_message TEXT,
            created_ts REAL NOT NULL,
            updated_ts REAL NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opportunity_threads_user "
                    "ON opportunity_threads(user_id, state)")
        conn.execute("""CREATE TABLE IF NOT EXISTS opportunity_suppressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            category TEXT NOT NULL,
            created_ts REAL NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opportunity_suppressions_user "
                    "ON opportunity_suppressions(user_id, category)")
        conn.execute("""CREATE TABLE IF NOT EXISTS opportunity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            category TEXT,
            decision TEXT NOT NULL,
            detail TEXT,
            created_ts REAL NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opportunity_log_user "
                    "ON opportunity_log(user_id, created_ts DESC)")
        conn.execute("INSERT OR REPLACE INTO schema_meta (id, version) VALUES (1, 5)")
    conn.commit()
