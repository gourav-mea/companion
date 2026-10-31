"""Policy document retrieval and answering: a tiny keyword-stem retriever over
built-in company policy text, so answers can cite which document they came
from without needing a vector DB.
"""
import glob
import os
import re

from .. import accessibility, config
from ..llm.client import chat

_POLICY_CACHE = None


def _stems(text):
    return set(t[:4] for t in re.findall(r"[a-z]+", text.lower()) if len(t) > 3)


def policy_sections():
    global _POLICY_CACHE
    if _POLICY_CACHE is not None:
        return _POLICY_CACHE
    secs = []
    for path in sorted(glob.glob(os.path.join(config.POLICY_DIR, "*.txt"))):
        name = os.path.basename(path)[:-4].replace("_", " ").title()
        try:
            txt = open(path).read()
        except Exception:
            continue
        for para in txt.split("\n\n"):
            para = para.strip()
            if para.upper().startswith("SOURCE:") or len(para) < 30:
                continue
            secs.append((name, para))
    _POLICY_CACHE = secs
    return secs


def policy_retrieve(question, k=3):
    q = _stems(question)
    scored = []
    for name, para in policy_sections():
        score = len(q & _stems(para))
        if score > 0:
            scored.append((score, name, para))
    scored.sort(reverse=True, key=lambda x: x[0])
    return scored[:k]


def policy_system(hits):
    context = "\n\n".join(f"From the {n}: {p}" for s, n, p in hits)
    return ("You answer using ONLY the company policy extracts below. Answer in one or two "
            "short sentences and name which policy the answer comes from. If the answer is "
            "not in the extracts, say the policy does not cover that.\n\n" + context
            + accessibility.lang_suffix())


def answer_policy(question, hits):
    return chat(policy_system(hits), question)


def suggest_related_doc(hits):
    """If the retrieve also matched a *different* policy document, return its
    name so the caller can offer to check it too."""
    if not hits:
        return None
    top_doc = hits[0][1]
    for _score, name, _para in hits[1:]:
        if name != top_doc:
            return name
    return None
