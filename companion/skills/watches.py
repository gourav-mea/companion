"""Voice-set watches, e.g. "notify me if USD to INR crosses 85" -- checked
periodically by the proactive scheduler and fired once per crossing.
"""
import time
import uuid

import requests

from .. import config
from . import live_data
from ..proactive import state_store


def _current_rate(base, target):
    try:
        url = f"https://api.frankfurter.app/latest?from={base}&to={target}"
        rate = requests.get(url, timeout=config.LIVE_TIMEOUT).json()["rates"][target]
        live_data.egress_log("api.frankfurter.app", f"fx watch {base}->{target}")
        return rate
    except Exception:
        return None


def create_watch(base, target, threshold, user_id=None):
    watches = state_store.get("fx_watches", [])
    current = _current_rate(base, target)
    side = ("above" if current >= threshold else "below") if current is not None else None
    watches.append({"id": uuid.uuid4().hex[:8], "user_id": user_id, "base": base,
                    "target": target, "threshold": threshold, "side": side,
                    "created_ts": time.time()})
    state_store.set("fx_watches", watches)
    return f"Okay, I'll let you know if {base} to {target} crosses {threshold:g}."


def check_watches():
    """Return spoken messages for watches that just crossed, removing them
    (one-shot alerts)."""
    pending = state_store.get("fx_watches", [])
    if not pending:
        return []
    remaining, messages = [], []
    for w in pending:
        rate = _current_rate(w["base"], w["target"])
        if rate is None or w.get("side") is None:
            remaining.append(w)
            continue
        new_side = "above" if rate >= w["threshold"] else "below"
        if new_side != w["side"]:
            messages.append(f"{w['base']} to {w['target']} just crossed {w['threshold']:g} "
                            f"-- it's now {rate:g}.")
        else:
            remaining.append(w)
    state_store.set("fx_watches", remaining)
    return messages
