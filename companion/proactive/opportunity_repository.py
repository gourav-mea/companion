"""Storage for the proactive opportunity engine: open conversational
threads, per-user-per-category suppressions, and an audit log. Kept
separate from memory_items (these aren't durable personal facts, they're
transient engagement state) and from identity.repository (not account data).
"""
import json
import sqlite3
import time

from .. import config


def _connect():
    conn = sqlite3.connect(config.MEM_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_open_thread(user_id):
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM opportunity_threads WHERE user_id IS ? AND state = 'awaiting_info' "
        "ORDER BY updated_ts DESC LIMIT 1", (user_id,)).fetchone()
    conn.close()
    if not row:
        return None
    thread = dict(row)
    try:
        thread["collected_info"] = json.loads(thread["collected_info"] or "{}")
    except Exception:
        thread["collected_info"] = {}
    return thread


def open_thread(user_id, category, topic_summary, collected_info, next_question):
    conn = _connect()
    now = time.time()
    conn.execute(
        "INSERT INTO opportunity_threads (user_id, topic_summary, category, state, "
        "collected_info, next_question, created_ts, updated_ts) "
        "VALUES (?, ?, ?, 'awaiting_info', ?, ?, ?, ?)",
        (user_id, topic_summary, category, json.dumps(collected_info or {}), next_question, now, now))
    conn.commit()
    conn.close()


def update_thread(thread_id, state, collected_info=None, next_question=None, final_message=None):
    fields, values = ["state = ?", "updated_ts = ?"], [state, time.time()]
    if collected_info is not None:
        fields.append("collected_info = ?"); values.append(json.dumps(collected_info))
    if next_question is not None:
        fields.append("next_question = ?"); values.append(next_question)
    if final_message is not None:
        fields.append("final_message = ?"); values.append(final_message)
    values.append(thread_id)
    conn = _connect()
    conn.execute(f"UPDATE opportunity_threads SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()


def expire_stale_threads(ttl_seconds):
    conn = _connect()
    cutoff = time.time() - ttl_seconds
    conn.execute("UPDATE opportunity_threads SET state = 'done' WHERE state = 'awaiting_info' "
                "AND updated_ts < ?", (cutoff,))
    conn.commit()
    conn.close()


def is_suppressed(user_id, category):
    if not category:
        return False
    conn = _connect()
    row = conn.execute(
        "SELECT 1 FROM opportunity_suppressions WHERE user_id IS ? AND category = ?",
        (user_id, category)).fetchone()
    conn.close()
    return row is not None


def suppress(user_id, category):
    if not category:
        return
    conn = _connect()
    conn.execute(
        "INSERT INTO opportunity_suppressions (user_id, category, created_ts) VALUES (?, ?, ?)",
        (user_id, category, time.time()))
    conn.commit()
    conn.close()


def log_decision(user_id, category, decision, detail=""):
    conn = _connect()
    conn.execute(
        "INSERT INTO opportunity_log (user_id, category, decision, detail, created_ts) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, category, decision, detail, time.time()))
    conn.commit()
    conn.close()


def recent_log(user_id, limit=10):
    conn = _connect()
    rows = conn.execute(
        "SELECT category, decision, detail, created_ts FROM opportunity_log "
        "WHERE user_id = ? ORDER BY created_ts DESC LIMIT ?", (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
