"""The plugin interface a voice command implements. Adding a new command
means adding one class + one `registry.register(...)` call in companion/
intents/ -- never editing an existing if/elif chain (open/closed).
"""
from typing import Optional, Protocol

from .types import Response, TurnContext


class Intent(Protocol):
    name: str
    priority: int   # lower runs first; replaces "position in the if/elif chain"

    def matches(self, question: str, lowered: str, ctx: TurnContext) -> bool: ...

    def handle(self, question: str, lowered: str, ctx: TurnContext) -> Response: ...


class IntentRegistry:
    """Ordered list of intents, first match wins -- same shape as the
    already-proven LIVE_TOOLS/proactive-triggers registries."""

    def __init__(self):
        self._intents: list = []

    def register(self, intent: Intent) -> Intent:
        self._intents.append(intent)
        self._intents.sort(key=lambda i: i.priority)
        return intent

    def dispatch(self, question: str, lowered: str, ctx: TurnContext) -> Optional[Response]:
        for intent in self._intents:
            if intent.matches(question, lowered, ctx):
                return intent.handle(question, lowered, ctx)
        return None
