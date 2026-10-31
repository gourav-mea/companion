"""User control over the proactive opportunity engine: stop/suppress the
currently open topic, or review what's been suggested recently."""
import re

from ..core.types import Response
from ..proactive import opportunity_repository as repo
from .registry import registry


class StopOpportunityIntent:
    name = "stop_opportunity"
    priority = 68
    pattern = re.compile(r"\b(stop asking me about this|i don't want to talk about this|"
                         r"not interested in this|drop this topic)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        user_id = ctx.speaker["id"] if ctx.speaker else None
        thread = repo.get_open_thread(user_id)
        if not thread:
            return Response("text", "I don't have anything open to stop.")
        repo.update_thread(thread["id"], "suppressed")
        repo.suppress(user_id, thread["category"])
        return Response("text", "Got it, I won't bring that up again.")


class WhatHaveYouSuggestedIntent:
    name = "what_have_you_suggested"
    priority = 107
    pattern = re.compile(r"\bwhat have you suggested\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        if ctx.speaker is None:
            return Response("text", "I haven't suggested anything to you.")
        entries = repo.recent_log(ctx.speaker["id"])
        parts = [f"{e['category']}: {e['decision']}" for e in entries[:5] if e.get("category")]
        if not parts:
            return Response("text", "I haven't suggested anything yet.")
        return Response("text", "Recently: " + "; ".join(parts))


registry.register(StopOpportunityIntent())
registry.register(WhatHaveYouSuggestedIntent())
