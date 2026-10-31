"""Speech rate and reply-language voice commands (per-recognized-user via
accessibility.py / state.current_user)."""
import re

from .. import accessibility
from ..core.types import Response
from .registry import registry


class SpeedSlowerIntent:
    name = "speed_slower"
    priority = 120
    pattern = re.compile(r"\b(speak|talk) (slower|more slowly|slowly)\b|\bslow down\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", accessibility.set_speed("slower"))


class SpeedFasterIntent:
    name = "speed_faster"
    priority = 130
    pattern = re.compile(r"\b(speak|talk) (faster|quicker)\b|\bspeed up\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", accessibility.set_speed("faster"))


class SpeedNormalIntent:
    name = "speed_normal"
    priority = 140
    pattern = re.compile(r"\b(normal|regular) (speed|pace)\b|\bspeak normally\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", accessibility.set_speed("normal"))


class LanguageIntent:
    name = "language"
    priority = 150
    pattern = re.compile(r"\b(?:answer|speak|reply|talk|respond)\s+(?:back\s+)?in\s+(english|hindi)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        match = self.pattern.search(lowered)
        return Response("text", accessibility.set_lang("hi" if match.group(1) == "hindi" else "en"))


registry.register(SpeedSlowerIntent())
registry.register(SpeedFasterIntent())
registry.register(SpeedNormalIntent())
registry.register(LanguageIntent())
