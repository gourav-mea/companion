"""Single background worker + bounded queue for the proactive opportunity
engine -- deliberately ONE worker (not a thread per turn), so fact-capture
(fast model) and opportunity-detection (heavier model) never run
concurrently and contend for the same CPU/model slot on this hardware. A
full queue drops the newest job rather than blocking the conversation or
growing without bound.
"""
import queue
import threading
import time

from .. import config, state
from . import opportunity_repository as repo
from .opportunity_filter import worth_checking

_queue = queue.Queue(maxsize=config.OPPORTUNITY_QUEUE_MAXSIZE)
_worker_started = False


def enqueue(user_id, question, answer):
    if not config.OPPORTUNITY_ENABLED:
        return
    try:
        _queue.put_nowait((user_id, question, answer))
    except queue.Full:
        pass   # best-effort: drop rather than block the conversation or pile up


def is_shadow(category):
    """Whether this category is still shadow-only. A category speaks once it is
    explicitly graduated (listed in OPPORTUNITY_LIVE_CATEGORIES), or when global
    shadow mode is turned off entirely. Everything else keeps logging silently
    so you can review its real precision first."""
    if category and category in config.OPPORTUNITY_LIVE_CATEGORIES:
        return False
    return config.OPPORTUNITY_SHADOW_MODE


def _deliver_nudge(text, user_id, category):
    """Deliver a proactive nudge (priority-ranked, decays if undelivered) and
    arm feedback attribution so the next user turn can teach the engine whether
    this category is welcome."""
    if not text:
        return
    state.pending_opportunity_feedback = {"user_id": user_id, "category": category, "ts": time.time()}
    from ..skills.utilities import queue_reminder
    queue_reminder(text, priority=5, ttl=config.OPPORTUNITY_NUDGE_TTL_SECONDS, category=category)


def _advance_open_thread(thread, user_id, question):
    from ..llm.opportunity_planner import planner
    category = thread["category"]
    decision = planner.advance(thread, question)
    shadow = is_shadow(category)
    if decision.action == "continue":
        repo.update_thread(thread["id"], "awaiting_info",
                          collected_info=decision.collected_info, next_question=decision.next_question)
        if shadow:
            repo.log_decision(user_id, category, "skipped_shadow", decision.next_question or "")
        else:
            _deliver_nudge(decision.next_question, user_id, category)
            repo.log_decision(user_id, category, "continued")
    elif decision.action == "ready":
        repo.update_thread(thread["id"], "ready_to_act", final_message=decision.final_message)
        if shadow:
            repo.log_decision(user_id, category, "skipped_shadow", decision.final_message or "")
        else:
            _deliver_nudge(decision.final_message, user_id, category)
            repo.log_decision(user_id, category, "ready")
    elif decision.action == "suppress":
        repo.update_thread(thread["id"], "suppressed")
        repo.suppress(user_id, category)
        repo.log_decision(user_id, category, "suppressed")
    else:
        repo.update_thread(thread["id"], "done")
        repo.log_decision(user_id, category, "closed")


def _detect_new_opportunity(user_id, question, answer):
    if not worth_checking(question):
        return
    from ..llm.opportunity_planner import planner
    decision = planner.detect(question, answer)
    if not decision.has_opportunity or decision.confidence < config.OPPORTUNITY_CONFIDENCE_THRESHOLD:
        repo.log_decision(user_id, decision.category, "skipped_lowconf",
                          f"confidence={decision.confidence:.2f}")
        return
    if repo.is_suppressed(user_id, decision.category):
        repo.log_decision(user_id, decision.category, "skipped_suppressed")
        return
    repo.open_thread(user_id, decision.category, decision.topic_summary, {}, decision.next_question)
    if is_shadow(decision.category):
        # Shadow: judge and log, but don't actually speak this category yet --
        # lets you review real precision before graduating it.
        repo.log_decision(user_id, decision.category, "skipped_shadow", decision.next_question or "")
        return
    _deliver_nudge(decision.next_question, user_id, decision.category)
    repo.log_decision(user_id, decision.category, "opened")


def record_feedback(user_id, text):
    """Attribute the user's reply to the last spoken nudge: a clear negative is
    a dismissal, and enough dismissals for a category auto-suppress it for this
    user. This only ever *reduces* proactivity, so a misattribution is harmless.
    Consumes the pending marker after one turn regardless."""
    fb = state.pending_opportunity_feedback
    if not fb:
        return
    state.pending_opportunity_feedback = None
    if time.time() - fb["ts"] > config.OPPORTUNITY_FEEDBACK_WINDOW_SECONDS:
        return
    if fb.get("user_id") != user_id:
        return
    category = fb.get("category")
    from ..conversation import router   # lazy: avoid an import cycle at module load
    if router.is_negative(text):
        repo.log_decision(user_id, category, "dismissed")
        since = time.time() - config.OPPORTUNITY_DISMISS_LOOKBACK_SECONDS
        if repo.dismissal_count(user_id, category, since) >= config.OPPORTUNITY_DISMISS_SUPPRESS_THRESHOLD:
            repo.suppress(user_id, category)
            repo.log_decision(user_id, category, "auto_suppressed")
    elif router.is_affirmative(text):
        repo.log_decision(user_id, category, "engaged")
    else:
        repo.log_decision(user_id, category, "ignored")


def _run_job(user_id, question, answer):
    from ..skills import memory_notes
    capture = memory_notes.maybe_capture_fact(user_id, question, answer)
    if capture and capture.get("action") == "confirm":
        state.pending_memory_confirmation = capture["followup"]

    thread = repo.get_open_thread(user_id)
    if thread:
        _advance_open_thread(thread, user_id, question)
    else:
        _detect_new_opportunity(user_id, question, answer)


def _worker_loop():
    while True:
        user_id, question, answer = _queue.get()
        try:
            repo.expire_stale_threads(config.OPPORTUNITY_THREAD_TTL_SECONDS)
            _run_job(user_id, question, answer)
        except Exception as exc:
            print(f"opportunity worker job failed: {exc}")
        finally:
            _queue.task_done()


def start_worker():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=_worker_loop, daemon=True).start()
