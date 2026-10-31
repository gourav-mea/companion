"""Pluggable proactive-trigger interface -- formalizes the function-list
pattern proactive/triggers.py already used, so it's consistent with
Intent/GuardRule and the scheduler can iterate one registry instead of a
hardcoded tuple of calls.
"""
from typing import Optional, Protocol


class Trigger(Protocol):
    name: str

    def check(self) -> Optional[str]: ...


class FunctionTrigger:
    """Adapts a plain `() -> str|None` function (today's style) to the
    Trigger interface with zero rewriting of the function bodies."""

    def __init__(self, name, fn):
        self.name = name
        self._fn = fn

    def check(self):
        return self._fn()


class TriggerRegistry:
    def __init__(self):
        self._triggers: list = []

    def register(self, trigger: Trigger) -> Trigger:
        self._triggers.append(trigger)
        return trigger

    def check_all(self):
        """Run every trigger, skipping one that errors instead of aborting
        the rest (a bug in one trigger must never silence the others)."""
        messages = []
        for trigger in self._triggers:
            try:
                msg = trigger.check()
            except Exception as exc:
                print(f"proactive trigger '{trigger.name}' failed: {exc}")
                continue
            if msg:
                messages.append(msg)
        return messages
