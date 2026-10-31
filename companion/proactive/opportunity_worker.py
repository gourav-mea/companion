"""Single background worker + bounded queue for the proactive opportunity
engine -- deliberately ONE worker (not a thread per turn), so fact-capture
(fast model) and opportunity-detection (heavier model) never run
concurrently and contend for the same CPU/model slot on this hardware. A
full queue drops the newest job rather than blocking the conversation or
growing without bound.
"""
import queue
import threading

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


def _deliver(text):
    if not text:
        return
    state.reminder_queue.put(text)   # spoken at the next safe gap, same as timers/proactive nudges


def _advance_open_thread(thread, user_id, question):
    from ..llm.opportunity_planner import planner
    decision = planner.advance(thread, question)
    if decision.action == "continue":
        repo.update_thread(thread["id"], "awaiting_info",
                          collected_info=decision.collected_info, next_question=decision.next_question)
        _deliver(decision.next_question)
        repo.log_decision(user_id, thread["category"], "continued")
    elif decision.action == "ready":
        repo.update_thread(thread["id"], "ready_to_act", final_message=decision.final_message)
        _deliver(decision.final_message)
        repo.log_decision(user_id, thread["category"], "ready")
    elif decision.action == "suppress":
        repo.update_thread(thread["id"], "suppressed")
        repo.suppress(user_id, thread["category"])
        repo.log_decision(user_id, thread["category"], "suppressed")
    else:
        repo.update_thread(thread["id"], "done")
        repo.log_decision(user_id, thread["category"], "closed")


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
    if config.OPPORTUNITY_SHADOW_MODE:
        # Shadow mode: judge and log, but don't actually speak anything yet --
        # lets you review real precision before trusting it live.
        repo.log_decision(user_id, decision.category, "skipped_shadow", decision.next_question or "")
        return
    _deliver(decision.next_question)
    repo.log_decision(user_id, decision.category, "opened")


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
