"""Company policy Q&A -- matched between the early and late live tools so it
doesn't get hijacked by Wikipedia/news phrasing."""
from .. import config
from ..core.types import Response
from ..skills import policy_qa
from .registry import registry


class PolicyIntent:
    name = "policy"
    priority = 240

    def matches(self, question, lowered, ctx):
        hits = policy_qa.policy_retrieve(question)
        ctx.scratch["policy_hits"] = hits
        return bool(hits) and hits[0][0] >= config.MIN_POLICY_SCORE

    def handle(self, question, lowered, ctx):
        hits = ctx.scratch["policy_hits"]
        related = policy_qa.suggest_related_doc(hits)
        followup = ({"kind": "policy_related", "interactive": True, "doc": related,
                    "question": question,
                    "prompt": f"Would you like me to also check the {related} policy?"}
                   if related else None)
        return Response("llm", (policy_qa.policy_system(hits), question, followup))


registry.register(PolicyIntent())
