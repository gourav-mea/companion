"""Small shared value types passed between the router and intent plugins."""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TurnContext:
    """Per-turn data for intents to read -- the alternative to reaching into
    global state for things that are really about *this* question. `scratch`
    lets an intent stash a value computed in matches() for reuse in handle()
    without recomputing it (e.g. a parsed math result)."""
    speaker: Optional[dict] = None
    scratch: dict = field(default_factory=dict)


@dataclass
class Response:
    """What an intent (or the router) hands back: the same ('kind', payload)
    shape the rest of the app already expects, just named."""
    kind: str
    payload: Any
