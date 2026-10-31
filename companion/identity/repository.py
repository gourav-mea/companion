"""SQLite storage for users and their voice-embedding samples.

This is the only identity module the admin web UI may import. It exposes
account metadata (name, role, active, sample counts, timestamps) and never
note/transcript content -- that boundary is what keeps "admins manage
accounts" from accidentally becoming "admins can read your notes".
"""
import json
import sqlite3
import time

from .. import config


def _connect():
    conn = sqlite3.connect(config.IDENTITY_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        display_name TEXT UNIQUE NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        active INTEGER NOT NULL DEFAULT 1,
        created_ts REAL NOT NULL,
        last_active_ts REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS voice_embeddings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        sample_ts REAL NOT NULL,
        vector_json TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS user_prefs (
        user_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (user_id, key))""")
    conn.commit()
    conn.close()


def create_user(display_name, role="user"):
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO users (display_name, role, active, created_ts) VALUES (?, ?, 1, ?)",
            (display_name, role, time.time()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_user(user_id):
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_name(display_name):
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE display_name = ?", (display_name,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_users():
    """Account metadata only -- no note/transcript content."""
    conn = _connect()
    rows = conn.execute("SELECT * FROM users ORDER BY created_ts").fetchall()
    out = []
    for row in rows:
        u = dict(row)
        u["sample_count"] = conn.execute(
            "SELECT COUNT(*) FROM voice_embeddings WHERE user_id = ?", (u["id"],)).fetchone()[0]
        out.append(u)
    conn.close()
    return out


def update_user(user_id, display_name=None, role=None, active=None):
    fields, values = [], []
    if display_name is not None:
        fields.append("display_name = ?"); values.append(display_name)
    if role is not None:
        fields.append("role = ?"); values.append(role)
    if active is not None:
        fields.append("active = ?"); values.append(1 if active else 0)
    if not fields:
        return
    values.append(user_id)
    conn = _connect()
    conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()


def touch_last_active(user_id):
    conn = _connect()
    conn.execute("UPDATE users SET last_active_ts = ? WHERE id = ?", (time.time(), user_id))
    conn.commit()
    conn.close()


def delete_user(user_id):
    conn = _connect()
    conn.execute("DELETE FROM voice_embeddings WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM user_prefs WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def add_embedding(user_id, vector):
    conn = _connect()
    conn.execute("INSERT INTO voice_embeddings (user_id, sample_ts, vector_json) VALUES (?, ?, ?)",
                 (user_id, time.time(), json.dumps(vector)))
    conn.commit()
    conn.close()


def get_centroids():
    """Return {user_id: (display_name, centroid_vector)} for active users with samples."""
    conn = _connect()
    users = {row["id"]: row["display_name"] for row in
             conn.execute("SELECT id, display_name FROM users WHERE active = 1")}
    centroids = {}
    for user_id, name in users.items():
        rows = conn.execute("SELECT vector_json FROM voice_embeddings WHERE user_id = ?",
                            (user_id,)).fetchall()
        vectors = [json.loads(r["vector_json"]) for r in rows]
        if not vectors:
            continue
        dim = len(vectors[0])
        centroid = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
        centroids[user_id] = (name, centroid)
    conn.close()
    return centroids


def get_pref(user_id, key, default=None):
    conn = _connect()
    row = conn.execute("SELECT value FROM user_prefs WHERE user_id = ? AND key = ?",
                       (user_id, key)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_pref(user_id, key, value):
    conn = _connect()
    conn.execute("INSERT INTO user_prefs (user_id, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
                (user_id, key, str(value)))
    conn.commit()
    conn.close()
