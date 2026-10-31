"""Per-user shopping/to-do lists -- deliberately a separate table from
memory_items (see bootstrap.py schema v4): a grocery list isn't a "personal
fact" the LLM should recall into conversation, it's a simple checklist.
"""
import re
import sqlite3
import time

from .. import config


def _connect():
    conn = sqlite3.connect(config.MEM_DB)
    conn.row_factory = sqlite3.Row
    return conn


def add_item(user_id, item):
    conn = _connect()
    conn.execute("INSERT INTO list_items (user_id, item, done, created_ts) VALUES (?, ?, 0, ?)",
                (user_id, item, time.time()))
    conn.commit()
    conn.close()


def list_items(user_id):
    conn = _connect()
    rows = conn.execute(
        "SELECT id, item FROM list_items WHERE user_id IS ? AND done = 0 ORDER BY created_ts",
        (user_id,)).fetchall()
    conn.close()
    return [(r["id"], r["item"]) for r in rows]


def remove_item(user_id, hint):
    """Mark the best lexical match for `hint` as done. Returns its text, or
    None if nothing matched."""
    conn = _connect()
    rows = conn.execute(
        "SELECT id, item FROM list_items WHERE user_id IS ? AND done = 0", (user_id,)).fetchall()
    words = [w for w in re.findall(r"[a-z]+", hint.lower()) if len(w) > 2]
    matches = [r for r in rows if any(w in r["item"].lower() for w in words)] if words else list(rows)
    if not matches:
        conn.close()
        return None
    target = matches[0]
    conn.execute("UPDATE list_items SET done = 1 WHERE id = ?", (target["id"],))
    conn.commit()
    conn.close()
    return target["item"]


def clear_list(user_id):
    conn = _connect()
    conn.execute("UPDATE list_items SET done = 1 WHERE user_id IS ? AND done = 0", (user_id,))
    conn.commit()
    conn.close()
