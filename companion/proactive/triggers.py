"""Individual proactive trigger checks. Each returns a message to speak, or
None if it isn't due yet. New triggers are added here without touching the
scheduler loop (open/closed, same pattern as skills.live_data.LIVE_TOOLS).
"""
import time

from .. import config
from ..core.trigger import FunctionTrigger, TriggerRegistry
from ..skills import macros
from . import state_store

registry = TriggerRegistry()


def _today():
    return time.strftime("%Y-%m-%d")


def _hour():
    return int(time.strftime("%H"))


def morning_briefing():
    if _hour() != config.PROACTIVE_MORNING_HOUR or state_store.get("last_morning_date") == _today():
        return None
    state_store.set("last_morning_date", _today())
    return macros.run_macro("morning")


def evening_winddown():
    if _hour() != config.PROACTIVE_EVENING_HOUR or state_store.get("last_evening_date") == _today():
        return None
    state_store.set("last_evening_date", _today())
    return macros.run_macro("evening")


def news_digest():
    if not state_store.get("news_digest_enabled", False):
        return None
    if _hour() != config.PROACTIVE_NEWS_HOUR or state_store.get("last_news_date") == _today():
        return None
    state_store.set("last_news_date", _today())
    from ..skills.live_data import get_news
    return get_news()


def record_timer_usage():
    """Called by the router whenever a timer/alarm is set, to learn the hours
    this is a habit so habit_suggestion() can offer it proactively later."""
    counts = state_store.get("habit_timer_hour_counts", {})
    hour = str(_hour())
    counts[hour] = counts.get(hour, 0) + 1
    state_store.set("habit_timer_hour_counts", counts)


def habit_suggestion():
    hour = _hour()
    counts = state_store.get("habit_timer_hour_counts", {})
    if counts.get(str(hour), 0) < config.HABIT_MIN_OCCURRENCES:
        return None
    key = f"{_today()}-{hour}"
    if state_store.get("habit_last_suggested") == key:
        return None
    state_store.set("habit_last_suggested", key)
    return "You usually set a timer around now. Want me to set one?"


def birthday_wishes():
    """Wish any enrolled user a happy birthday once, on the day (birthday is
    stored as a "MM-DD" user_prefs value, see intents/birthday_cmd.py)."""
    today = time.strftime("%m-%d")
    if state_store.get("birthday_wished_date") != _today():
        state_store.set("birthday_wished_date", _today())
        state_store.set("birthday_wished_users", [])
    already_wished = state_store.get("birthday_wished_users", [])
    from ..identity import repository
    messages = []
    for user in repository.list_users():
        if user["id"] in already_wished:
            continue
        if repository.get_pref(user["id"], "birthday") == today:
            messages.append(f"Happy birthday, {user['display_name']}!")
            already_wished.append(user["id"])
    if messages:
        state_store.set("birthday_wished_users", already_wished)
    return " ".join(messages) if messages else None


def memory_compaction():
    """Once a day, compact any recognized user's older memory items into a
    summary so recall stays fast/relevant as history grows. Silent -- this
    never produces a spoken message, it just does the housekeeping."""
    if state_store.get("last_compaction_date") == _today():
        return None
    state_store.set("last_compaction_date", _today())
    from ..identity import repository
    from ..skills import memory_notes
    for user in repository.list_users():
        try:
            memory_notes.compact_user_memory(user["id"])
        except Exception as exc:
            print(f"memory compaction failed for user {user['id']}: {exc}")
    return None


def check_recurring_alarms():
    """Fire any daily alarm due this minute. Unconditional like
    memory_compaction -- an alarm the user explicitly set is a direct
    request, not a speculative nudge, so it isn't gated behind
    PROACTIVE_ENABLED/quiet hours."""
    from ..skills import alarms
    now = time.localtime()
    today_str = time.strftime("%Y-%m-%d", now)
    for alarm_id, _user_id, label in alarms.due_alarms(now.tm_hour, now.tm_min, today_str):
        alarms.mark_fired(alarm_id, today_str)
        from .. import state
        state.reminder_queue.put(f"Alarm: {label}" if label else "Alarm!")


# New scheduled/event-based nudges register here -- the scheduler never
# changes, it just iterates whatever is registered (open/closed).
# memory_compaction and check_recurring_alarms are deliberately NOT
# registered here: they must run regardless of PROACTIVE_ENABLED/quiet
# hours/rate caps, so scheduler.py calls them directly instead.
registry.register(FunctionTrigger("morning_briefing", morning_briefing))
registry.register(FunctionTrigger("evening_winddown", evening_winddown))
registry.register(FunctionTrigger("news_digest", news_digest))
registry.register(FunctionTrigger("habit_suggestion", habit_suggestion))
registry.register(FunctionTrigger("birthday_wishes", birthday_wishes))
