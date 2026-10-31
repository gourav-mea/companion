"""Pluggable guardrail rule interface -- formalizes the pattern GUARD_RULES
already used as a plain regex dict, so new categories register an instance
instead of growing a dict literal (open/closed, and consistent with Intent).
"""
from typing import Optional, Protocol


class GuardRule(Protocol):
    category: str

    def scan(self, text: str) -> bool: ...


class RegexGuardRule:
    """Adapts a compiled regex (today's GUARD_RULES style) to the interface
    with zero behaviour change."""

    def __init__(self, category, pattern):
        self.category = category
        self.pattern = pattern

    def scan(self, text):
        return bool(self.pattern.search(text))


class GuardRuleRegistry:
    def __init__(self):
        self._rules: list = []

    def register(self, rule: GuardRule) -> GuardRule:
        self._rules.append(rule)
        return rule

    def scan(self, text: str) -> Optional[str]:
        for rule in self._rules:
            if rule.scan(text):
                return rule.category
        return None
