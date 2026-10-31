"""Presence bookkeeping: detect "welcome back" moments for recognized users,
and track repeated unrecognized voices to offer self-service enrollment.
"""
import time

from .. import config
from . import repository
from ..proactive import state_store


def note_presence(user_id):
    """Record this interaction and report whether it's a 'welcome back' moment
    (the user was last seen more than WELCOME_BACK_SECONDS ago)."""
    user = repository.get_user(user_id)
    last_active = user.get("last_active_ts") if user else None
    repository.touch_last_active(user_id)
    if last_active is None:
        return False
    return (time.time() - last_active) >= config.WELCOME_BACK_SECONDS


def note_guest_interaction():
    """Track repeated unrecognized-voice turns; returns True when it's time
    to offer voice enrollment (rate-limited so it only offers occasionally)."""
    now = time.time()
    window_start = state_store.get("guest_window_start", now)
    count = state_store.get("guest_count", 0)
    if now - window_start > config.GUEST_ENROLL_OFFER_WINDOW_SECONDS:
        count, window_start = 0, now
    count += 1
    state_store.set("guest_count", count)
    state_store.set("guest_window_start", window_start)
    last_offer = state_store.get("guest_last_offer_ts", 0)
    if (count >= config.GUEST_ENROLL_OFFER_THRESHOLD
            and now - last_offer >= config.GUEST_ENROLL_OFFER_COOLDOWN_SECONDS):
        state_store.set("guest_last_offer_ts", now)
        state_store.set("guest_count", 0)
        return True
    return False
