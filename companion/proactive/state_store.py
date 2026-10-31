"""Tiny JSON-backed key/value store for proactive-feature bookkeeping
(cooldowns, counters, user-configured watches, habit stats). This is purely
scheduling state, not sensitive content, so a flat file is enough.
"""
import json
import os
import threading

from .. import config

_lock = threading.Lock()


def _path():
    return os.path.join(config.WORK, "proactive_state.json")


def _load():
    try:
        with open(_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data):
    with open(_path(), "w") as f:
        json.dump(data, f)


def get(key, default=None):
    with _lock:
        return _load().get(key, default)


def set(key, value):
    with _lock:
        data = _load()
        data[key] = value
        _save(data)
