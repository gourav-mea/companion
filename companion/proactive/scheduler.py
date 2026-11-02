"""Background thread that periodically evaluates proactive triggers and
queues any resulting nudge through the existing reminder_queue, so delivery
only ever happens at the same safe idle gaps timers already use.
"""
import time

from .. import config, state
from ..skills import watches
from ..skills.utilities import queue_reminder
from . import triggers


def _in_quiet_hours():
    start, end = config.PROACTIVE_QUIET_HOURS
    hour = int(time.strftime("%H"))
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end   # wraps past midnight


def proactive_loop():
    fired_this_hour = []
    current_hour_bucket = None
    last_fx_check = 0.0
    while True:
        time.sleep(config.PROACTIVE_POLL_SECONDS)
        triggers.memory_compaction()   # housekeeping: always runs, never speaks, no rate limit
        triggers.check_recurring_alarms()   # explicit user-set alarms: always runs, not gated
        if not config.PROACTIVE_ENABLED or not state.switch_enabled() or _in_quiet_hours():
            continue
        hour_bucket = time.strftime("%Y-%m-%d-%H")
        if hour_bucket != current_hour_bucket:
            fired_this_hour, current_hour_bucket = [], hour_bucket
        if len(fired_this_hour) >= config.PROACTIVE_MAX_PER_HOUR:
            continue
        messages = triggers.registry.check_all()
        now = time.time()
        if now - last_fx_check >= config.FX_WATCH_CHECK_SECONDS:
            last_fx_check = now
            messages.extend(watches.check_watches())
        for msg in messages:
            if len(fired_this_hour) >= config.PROACTIVE_MAX_PER_HOUR:
                break
            queue_reminder(msg, priority=3)   # scheduled briefings: below alarms/timers, above nudges
            fired_this_hour.append(msg)
