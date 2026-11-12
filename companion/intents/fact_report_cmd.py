"""Transparency: tell the recognized speaker what is remembered about them, a few
facts at a time (the counterpart to privacy_report/guard_report). A long list is
spoken in short chunks with an offer for more -- a 40-second monologue is hard to
follow, and one very long utterance is the riskiest thing to hand the audio pipeline.
"""
import re

from .. import config, state
from ..core.types import Response
from ..skills import memory_notes
from .registry import registry


class WhatDoYouKnowIntent:
    name = "what_do_you_know"
    priority = 105
    pattern = re.compile(
        r"\b(what (all )?(do )?you (know|remember|have) (about|on) me|"
        r"what (all )?(info|information|data|details|facts)( do)? you have (about|on) me|"
        r"what have you remembered|tell me everything you know about me)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        if ctx.speaker is None:
            return Response("text", "I don't have anything remembered for you -- "
                                     "I don't recognize your voice yet.")
        facts = memory_notes.ranked_facts(ctx.speaker["id"])
        if not facts:
            return Response("text", "I don't have anything remembered about you yet.")
        chunk = config.FACT_REPORT_CHUNK
        if len(facts) <= chunk:
            return Response("text", "Here's what I know about you: " + "; ".join(facts) + ".")
        # Speak the most defining facts now and offer the rest; the next "yes" runs
        # macros.run_followup, which pages through the remainder the same way.
        state.pending_followup = {"kind": "memory_facts_more", "interactive": True,
                                  "user_id": ctx.speaker["id"], "offset": chunk}
        return Response("text", f"I know {len(facts)} things about you. The main ones: "
                                + "; ".join(facts[:chunk]) + ". Want to hear more?")


registry.register(WhatDoYouKnowIntent())
