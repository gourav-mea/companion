"""Named multi-step routines ("start my morning routine") -- reuses
skills.macros.run_macro, the same content already used by the proactive
morning briefing / evening wind-down, so there's one source of truth."""
import re

from ..core.types import Response
from ..skills import macros
from .registry import registry


class StartRoutineIntent:
    name = "start_routine"
    priority = 192
    pattern = re.compile(r"\bstart (?:my |the )?(morning|evening) routine\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        name = self.pattern.search(lowered).group(1)
        result = macros.run_macro(name)
        return Response("text", result or f"I don't have a {name} routine yet.")


registry.register(StartRoutineIntent())
