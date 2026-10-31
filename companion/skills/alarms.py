"""Recurring daily alarms ("remind me every day at 7am...") -- unlike the
existing one-shot threading.Timer timers (skills/utilities.py), these persist
across restarts and are checked once a minute by the proactive scheduler
(unconditionally -- an explicit alarm the user set isn't a speculative nudge,
so it isn't gated behind config.PROACTIVE_ENABLED).
"""
import sqlite3
import time

from .. import config


def _connect():
    conn = sqlite3.connect(config.MEM_DB)
    conn.row_factory = sqlite3.Row
    return conn


def add_alarm(user_id, hour, minute, label=""):
    conn = _connect()
    conn.execute(
        "INSERT INTO alarms (user_id, hour, minute, label, active, last_fired_date, created_ts) "
        "VALUES (?, ?, ?, ?, 1, '', ?)",
        (user_id, hour, minute, label, time.time()))
    conn.commit()
    conn.close()


def due_alarms(hour, minute, today_str):
    conn = _connect()
    rows = conn.execute(
        "SELECT id, user_id, label FROM alarms WHERE active = 1 AND hour = ? AND minute = ? "
        "AND last_fired_date != ?", (hour, minute, today_str)).fetchall()
    conn.close()
    return [(r["id"], r["user_id"], r["label"]) for r in rows]


def mark_fired(alarm_id, today_str):
    conn = _connect()
    conn.execute("UPDATE alarms SET last_fired_date = ? WHERE id = ?", (today_str, alarm_id))
    conn.commit()
    conn.close()


def list_alarms(user_id):
    conn = _connect()
    rows = conn.execute(
        "SELECT hour, minute, label FROM alarms WHERE user_id IS ? AND active = 1 ORDER BY hour, minute",
        (user_id,)).fetchall()
    conn.close()
    return [(r["hour"], r["minute"], r["label"]) for r in rows]
