"""Persisted memory: typed, per-user memory items recalled by a hybrid of
semantic similarity and recency, plus a short-term context ring buffer that
survives sleep/wake, and periodic compaction of old items into summaries.

SQLiteMemoryStore is the concrete adapter for core.memory_store.MemoryStore
-- callers that want to depend on the abstraction (rather than this module)
can type-hint against that Protocol and receive `store`. The module-level
functions below are kept as thin wrappers so every existing call site
(router/intents, session.py, proactive triggers, cli selftest) needs no
changes.
"""
import json
import re
import sqlite3
import time

from .. import config
from ..llm.client import chat, embed

RECALL_HALF_LIFE_SECONDS = 14 * 24 * 3600   # a two-week-old note has half the recency weight
SIMILARITY_SUPERSEDE_THRESHOLD = 0.92       # near-duplicate => treat as an update, not a new fact
# recall candidate/result counts now live in config (RECALL_CANDIDATES, RECALL_TOP, ...)


def _connect():
    conn = sqlite3.connect(config.MEM_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _recency_weight(ts):
    age = max(0.0, time.time() - ts)
    return 0.5 ** (age / RECALL_HALF_LIFE_SECONDS)


def _canonical_slot(attribute):
    """Normalize a free-form attribute slot onto a canonical name so a later
    correction to the same fact supersedes it instead of stacking a duplicate
    under a different spelling ("city" vs "home_city")."""
    if not attribute:
        return None
    a = attribute.strip().lower().replace(" ", "_")
    return config.MEMORY_SLOT_ALIASES.get(a, a)


class SQLiteMemoryStore:
    """Implements core.memory_store.MemoryStore against the local SQLite
    memory.db. The storage engine could be swapped (e.g. Postgres+pgvector)
    by writing one new class with these same methods -- no caller changes."""

    def remember_note(self, text, user_id=None, due_date=None, item_type=None, source="explicit",
                      attribute=None, confidence=1.0, vec=None):
        """Store a memory item.

        If `attribute` is given, conflict detection is an indexed exact-slot
        lookup (O(log n) via idx_memory_items_user_attr) instead of scanning
        every stored vector -- used by the auto-capture path. Free-form
        callers (attribute=None, the existing "remember that..." command)
        keep the original embedding-similarity dedupe, unchanged.
        `vec` can be passed in to reuse an embedding already computed by the
        caller (e.g. during conflict detection) instead of recomputing it.
        """
        item_type = item_type or ("reminder" if due_date else "fact")
        vec = vec if vec is not None else embed(text)
        conn = _connect()
        if attribute:
            existing = conn.execute(
                "SELECT id FROM memory_items WHERE user_id IS ? AND attribute = ? "
                "AND superseded_by IS NULL", (user_id, attribute)).fetchone()
            if existing:
                conn.execute("UPDATE memory_items SET superseded_by = -1 WHERE id = ?", (existing["id"],))
        elif vec:
            for row in conn.execute(
                    "SELECT id, vec FROM memory_items WHERE user_id IS ? AND superseded_by IS NULL "
                    "AND attribute IS NULL", (user_id,)):
                if not row["vec"]:
                    continue
                try:
                    if _cosine(vec, json.loads(row["vec"])) >= SIMILARITY_SUPERSEDE_THRESHOLD:
                        conn.execute("UPDATE memory_items SET superseded_by = -1 WHERE id = ?", (row["id"],))
                except Exception:
                    continue
        cur = conn.execute(
            "INSERT INTO memory_items (user_id, type, content, vec, source, confidence, created_ts, "
            "due_date, attribute) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, item_type, text, json.dumps(vec) if vec else "", source, confidence,
             time.time(), due_date, attribute))
        conn.execute("UPDATE memory_items SET superseded_by = ? WHERE superseded_by = -1", (cur.lastrowid,))
        conn.commit()
        conn.close()
        return cur.lastrowid

    def find_same_attribute(self, user_id, attribute):
        """Indexed lookup: the current active fact for this user+slot, if any."""
        if not attribute:
            return None
        conn = _connect()
        row = conn.execute(
            "SELECT id, content FROM memory_items WHERE user_id IS ? AND attribute = ? "
            "AND superseded_by IS NULL", (user_id, attribute)).fetchone()
        conn.close()
        return dict(row) if row else None

    def log_conflict(self, user_id, attribute, old_text, new_text, resolution):
        conn = _connect()
        conn.execute(
            "INSERT INTO memory_conflicts (user_id, attribute, old_text, new_text, resolution, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, attribute, old_text, new_text, resolution, time.time()))
        conn.commit()
        conn.close()

    def conflict_history(self, user_id, limit=5):
        conn = _connect()
        rows = conn.execute(
            "SELECT attribute, old_text, new_text, resolution, created_ts FROM memory_conflicts "
            "WHERE user_id = ? ORDER BY created_ts DESC LIMIT ?", (user_id, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def facts_for_user(self, user_id, limit=20):
        conn = _connect()
        rows = conn.execute(
            "SELECT content FROM memory_items WHERE user_id = ? AND superseded_by IS NULL "
            "AND type != 'reminder' ORDER BY created_ts DESC LIMIT ?", (user_id, limit)).fetchall()
        conn.close()
        return [r["content"] for r in rows]

    def knowledge_snapshot(self, user_id):
        """Everything the agent has learned about a user, for the admin Insights
        view: all facts (active + superseded, with slot/source/confidence/when),
        the correction history, and the episode/compaction summaries. Read-only."""
        conn = _connect()
        facts = []
        for r in conn.execute(
                "SELECT id, content, attribute, source, confidence, created_ts, last_accessed_ts, "
                "superseded_by FROM memory_items WHERE user_id IS ? AND type != 'reminder' "
                "ORDER BY created_ts ASC", (user_id,)).fetchall():
            d = dict(r)
            d["active"] = d.pop("superseded_by") is None
            facts.append(d)
        conflicts = [dict(r) for r in conn.execute(
            "SELECT attribute, old_text, new_text, resolution, created_ts FROM memory_conflicts "
            "WHERE user_id IS ? ORDER BY created_ts ASC", (user_id,)).fetchall()]
        episodes = [dict(r) for r in conn.execute(
            "SELECT summary_text, kind, created_ts FROM memory_summaries WHERE user_id IS ? "
            "ORDER BY created_ts ASC", (user_id,)).fetchall()]
        conn.close()
        return {"facts": facts, "conflicts": conflicts, "episodes": episodes}

    def forget_fact(self, user_id, hint):
        """Retract the best lexical match among this user's active facts."""
        conn = _connect()
        rows = conn.execute(
            "SELECT id, content FROM memory_items WHERE user_id = ? AND superseded_by IS NULL",
            (user_id,)).fetchall()
        words = [w for w in re.findall(r"[a-z]+", hint.lower()) if len(w) > 3]
        matches = [r for r in rows if any(w in r["content"].lower() for w in words)] if words else list(rows)
        if not matches:
            conn.close()
            return None
        target = matches[0]
        conn.execute("UPDATE memory_items SET superseded_by = -2 WHERE id = ?", (target["id"],))   # -2 = forgotten
        conn.commit()
        conn.close()
        return target["content"]

    def maybe_capture_fact(self, user_id, question, answer):
        """Opportunistically capture a durable personal fact from a normal
        turn, no "remember that..." needed. A cheap keyword pre-filter runs
        first so the vast majority of turns never pay for an LLM call.

        Returns None (nothing worth capturing), {"action": "stored"}, or
        {"action": "confirm", "followup": {...}} when the change needs a
        yes/no before anything is written (nothing is persisted yet in that
        case -- see skills/macros.run_followup's confirm_memory_update).
        """
        if user_id is None or not config.FACT_CAPTURE_ENABLED:
            return None
        lowered = question.lower()
        if not any(cue in lowered for cue in config.FACT_CAPTURE_CUES):
            return None
        from ..llm.fact_extraction import extractor
        candidate = extractor.extract(question, answer)
        if candidate is None:
            return None
        candidate.attribute = _canonical_slot(candidate.attribute)   # so corrections supersede, not stack
        vec = embed(candidate.text)
        existing = self.find_same_attribute(user_id, candidate.attribute) if candidate.attribute else None
        if existing is None or candidate.update_mode == "append":
            self.remember_note(candidate.text, user_id=user_id, item_type="fact", source="auto",
                              attribute=candidate.attribute, confidence=candidate.confidence, vec=vec)
            return {"action": "stored"}
        if candidate.confidence >= config.FACT_CAPTURE_AUTO_CONFIDENCE:
            self.remember_note(candidate.text, user_id=user_id, item_type="fact", source="auto",
                              attribute=candidate.attribute, confidence=candidate.confidence, vec=vec)
            self.log_conflict(user_id, candidate.attribute, existing["content"], candidate.text, "auto")
            return {"action": "stored"}
        # Low confidence: ask before writing anything (nothing is persisted
        # unless/until the user confirms -- see macros.run_followup).
        followup = {
            "kind": "confirm_memory_update", "interactive": True,
            "prompt": (f"You told me {existing['content'].rstrip('.')} earlier -- did you mean "
                      f"to update that to: {candidate.text}?"),
            "user_id": user_id, "attribute": candidate.attribute,
            "old_content": existing["content"], "new_text": candidate.text, "vec": vec,
        }
        return {"action": "confirm", "followup": followup}

    @staticmethod
    def _sim(qv, vec_json):
        if not vec_json:
            return 0.0
        try:
            return _cosine(qv, json.loads(vec_json))
        except Exception:
            return 0.0

    def recall_notes(self, question, user_id=None):
        """Hybrid semantic + recency + reinforcement recall over this user's
        facts AND their compaction/episode summaries, scoped to this user (or
        guest notes for user_id=None) -- a guest never sees a recognized user's
        notes or vice versa.

        Summaries are included so compaction no longer drops older facts out of
        recall: previously the summary that replaced them was never read back.
        An item's score also leans slightly on how recently it was useful
        (last_accessed_ts) and whether it was stated explicitly vs auto-captured.
        """
        question = question or ""
        conn = _connect()
        item_rows = conn.execute(
            "SELECT id, content, vec, created_ts, last_accessed_ts, source FROM memory_items "
            "WHERE user_id IS ? AND superseded_by IS NULL AND type != 'reminder' "
            "ORDER BY created_ts DESC LIMIT ?",
            (user_id, config.RECALL_CANDIDATES)).fetchall()
        summary_rows = conn.execute(
            "SELECT id, summary_text AS content, vec, created_ts FROM memory_summaries "
            "WHERE user_id IS ? ORDER BY created_ts DESC LIMIT ?",
            (user_id, config.RECALL_SUMMARY_CANDIDATES)).fetchall()
        qv = embed(question)
        scored = []   # (score, is_item, row_id, content)
        if qv:
            for row in item_rows:
                sim = self._sim(qv, row["vec"])
                if sim <= 0.35:
                    continue
                recency = _recency_weight(row["created_ts"])
                access = _recency_weight(row["last_accessed_ts"]) if row["last_accessed_ts"] else 0.0
                src_weight = 1.0 if (row["source"] or "explicit") == "explicit" else 0.6
                score = 0.6 * sim + 0.22 * recency + 0.1 * access + 0.08 * src_weight
                scored.append((score, True, row["id"], row["content"]))
            for row in summary_rows:
                sim = self._sim(qv, row["vec"])
                if sim <= 0.30:   # summaries are broader, so a slightly looser gate
                    continue
                score = 0.6 * sim + 0.4 * _recency_weight(row["created_ts"])
                scored.append((score, False, row["id"], row["content"]))
        if scored:
            scored.sort(key=lambda t: t[0], reverse=True)
            top = scored[:config.RECALL_TOP]
            item_ids = [rid for _s, is_item, rid, _c in top if is_item]
            if item_ids:   # reinforce: reward facts that just proved useful
                conn.executemany("UPDATE memory_items SET last_accessed_ts = ? WHERE id = ?",
                                 [(time.time(), rid) for rid in item_ids])
                conn.commit()
            conn.close()
            return "\n".join(f"- {c}" for _s, _i, _rid, c in top)
        conn.close()
        # Lexical fallback when embeddings are unavailable -- include the latest
        # summary so compacted history isn't invisible here either.
        words = [w for w in re.findall(r"[a-z]+", question.lower()) if len(w) > 3]
        pool = [r["content"] for r in item_rows]
        if summary_rows:
            pool.append(summary_rows[0]["content"])
        hits = [c for c in pool if any(w in c.lower() for w in words)] if words else []
        return "\n".join(f"- {h}" for h in hits[:config.RECALL_TOP])

    def due_notes_for(self, user_id, date_str):
        """Notes this user asked to be followed up on for a given YYYY-MM-DD date."""
        conn = _connect()
        rows = conn.execute(
            "SELECT id, content FROM memory_items WHERE user_id = ? AND due_date = ? "
            "AND superseded_by IS NULL",
            (user_id, date_str)).fetchall()
        conn.close()
        return [(r["id"], r["content"]) for r in rows]

    def clear_due(self, item_id):
        """Mark a follow-through note as delivered so it isn't surfaced again."""
        conn = _connect()
        conn.execute("UPDATE memory_items SET due_date = NULL WHERE id = ?", (item_id,))
        conn.commit()
        conn.close()

    def append_turn(self, user_id, role, content):
        """Append to the short-term context ring buffer (survives sleep/wake)."""
        conn = _connect()
        conn.execute("INSERT INTO short_term_context (user_id, ts, role, content) VALUES (?, ?, ?, ?)",
                    (user_id, time.time(), role, content))
        conn.commit()
        conn.close()

    def recent_context(self, user_id, turns=3):
        """Last `turns` (question, answer) pairs for this user, oldest first."""
        conn = _connect()
        rows = conn.execute(
            "SELECT role, content FROM short_term_context WHERE user_id IS ? "
            "ORDER BY ts DESC LIMIT ?", (user_id, turns * 2)).fetchall()
        conn.close()
        pairs, pending_q = [], None
        for role, content in reversed([(r["role"], r["content"]) for r in rows]):
            if role == "user":
                pending_q = content
            elif role == "companion" and pending_q is not None:
                pairs.append((pending_q, content))
                pending_q = None
        return pairs

    def item_count(self, user_id):
        conn = _connect()
        n = conn.execute(
            "SELECT COUNT(*) FROM memory_items WHERE user_id = ? AND superseded_by IS NULL",
            (user_id,)).fetchone()[0]
        conn.close()
        return n

    def remember_episode(self, user_id, summary_text):
        """Persist an end-of-conversation episode summary, embedded so it is
        recalled later like a compaction summary ("what did we talk about
        yesterday"). Skipped for guests (user_id=None)."""
        if user_id is None or not summary_text:
            return None
        vec = embed(summary_text)
        now = time.time()
        conn = _connect()
        cur = conn.execute(
            "INSERT INTO memory_summaries (user_id, period_start, period_end, summary_text, created_ts, "
            "vec, kind) VALUES (?, ?, ?, ?, ?, ?, 'episode')",
            (user_id, now, now, summary_text, now, json.dumps(vec) if vec else ""))
        conn.commit()
        conn.close()
        return cur.lastrowid

    def compact_user_memory(self, user_id, keep_recent=None):
        """Summarize a user's oldest memory items into one memory_summaries
        row once they pass `keep_recent`, then mark the originals superseded
        so recall stays fast and relevant as history grows. The summary is
        embedded so it stays recallable -- the facts it absorbed are not lost."""
        keep_recent = config.MEMORY_COMPACT_KEEP_RECENT if keep_recent is None else keep_recent
        conn = _connect()
        rows = conn.execute(
            "SELECT id, content, created_ts FROM memory_items WHERE user_id = ? "
            "AND superseded_by IS NULL AND type != 'reminder' ORDER BY created_ts ASC",
            (user_id,)).fetchall()
        conn.close()
        if len(rows) <= keep_recent:
            return False
        to_compact = rows[:len(rows) - keep_recent]
        text = "\n".join(f"- {r['content']}" for r in to_compact)
        summary = chat("Summarize these personal notes into 2-3 short sentences capturing "
                       "lasting facts and preferences, dropping anything trivial or one-off.", text)
        svec = embed(summary)
        conn = _connect()
        conn.execute(
            "INSERT INTO memory_summaries (user_id, period_start, period_end, summary_text, created_ts, "
            "vec, kind) VALUES (?, ?, ?, ?, ?, ?, 'compaction')",
            (user_id, to_compact[0]["created_ts"], to_compact[-1]["created_ts"], summary, time.time(),
             json.dumps(svec) if svec else ""))
        summary_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.executemany("UPDATE memory_items SET superseded_by = ? WHERE id = ?",
                         [(summary_id, r["id"]) for r in to_compact])
        conn.commit()
        conn.close()
        return True


store = SQLiteMemoryStore()

# Module-level aliases: every existing caller keeps working unchanged.
remember_note = store.remember_note
recall_notes = store.recall_notes
due_notes_for = store.due_notes_for
clear_due = store.clear_due
append_turn = store.append_turn
recent_context = store.recent_context
item_count = store.item_count
compact_user_memory = store.compact_user_memory
remember_episode = store.remember_episode
find_same_attribute = store.find_same_attribute
log_conflict = store.log_conflict
conflict_history = store.conflict_history
facts_for_user = store.facts_for_user
forget_fact = store.forget_fact
maybe_capture_fact = store.maybe_capture_fact
knowledge_snapshot = store.knowledge_snapshot


def ranked_facts(user_id):
    """Active facts as plain sentences, most identity-defining first: slotted facts
    (name, city, job...) before free-form ones, things the user said before things
    learned automatically, newest first. Used to speak a long memory in short chunks."""
    active = [f for f in knowledge_snapshot(user_id)["facts"] if f["active"]]
    active.sort(key=lambda f: (f["attribute"] is None, f["source"] != "explicit",
                               -(f["created_ts"] or 0)))
    return [f["content"] for f in active]

