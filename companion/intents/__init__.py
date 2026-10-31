"""Adding a new voice command: add a class here (or a new file) implementing
matches()/handle(), then `registry.register(YourIntent())` -- never edit an
existing intent or a central if/elif chain (open/closed).
"""
from . import (
    enroll, model_cmd, history_cmd, notes_cmd, forget_cmd, help_cmd, reports_cmd,
    fact_report_cmd, conflict_report_cmd, accessibility_cmd, news_digest_cmd,
    fx_watch_cmd, macro_cmd, timer_cmd, time_date_cmd, math_cmd, live_tool_cmd,
    policy_cmd, fallback_cmd, birthday_cmd, list_cmd, routine_cmd, alarm_cmd,
    trivia_cmd, joke_cmd, offline_knowledge_cmd, opportunity_control_cmd,
)
from .registry import registry

__all__ = ["registry"]
