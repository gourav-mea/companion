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
RECALL_CANDIDATES = 150                      # rows pulled from SQL before the in-Python rerank


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

    def recall_notes(self, question, user_id=None):
        """Hybrid semantic + recency recall, scoped to this user (or guest
        notes for user_id=None) -- a guest never sees a recognized user's
        notes or vice versa."""
        conn = _connect()
        rows = conn.execute(
            "SELECT id, content, vec, created_ts FROM memory_items "
            "WHERE user_id IS ? AND superseded_by IS NULL AND type != 'reminder' "
            "ORDER BY created_ts DESC LIMIT ?",
            (user_id, RECALL_CANDIDATES)).fetchall()
        qv = embed(question)
        scored = []
        if qv:
            for row in rows:
                if not row["vec"]:
                    continue
                try:
                    sim = _cosine(qv, json.loads(row["vec"]))
                except Exception:
                    continue
                if sim > 0.35:
                    score = 0.7 * sim + 0.3 * _recency_weight(row["created_ts"])
                    scored.append((score, row["id"], row["content"]))
        if scored:
            scored.sort(reverse=True)
            top = scored[:4]
            conn.executemany("UPDATE memory_items SET last_accessed_ts = ? WHERE id = ?",
                             [(time.time(), rid) for _s, rid, _c in top])
            conn.commit()
            conn.close()
            return "\n".join(f"- {c}" for _s, _rid, c in top)
        conn.close()
        # Lexical fallback when embeddings are unavailable.
        words = [w for w in re.findall(r"[a-z]+", question.lower()) if len(w) > 3]
        hits = [r["content"] for r in rows if any(w in r["content"].lower() for w in words)]
        return "\n".join(f"- {h}" for h in hits[:4])

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

    def compact_user_memory(self, user_id, keep_recent=50):
        """Summarize a user's oldest memory items into one memory_summaries
        row once they pass `keep_recent`, then mark the originals superseded
        so recall stays fast and relevant as history grows."""
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
        conn = _connect()
        conn.execute(
            "INSERT INTO memory_summaries (user_id, period_start, period_end, summary_text, created_ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, to_compact[0]["created_ts"], to_compact[-1]["created_ts"], summary, time.time()))
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
find_same_attribute = store.find_same_attribute
log_conflict = store.log_conflict
conflict_history = store.conflict_history
facts_for_user = store.facts_for_user
forget_fact = store.forget_fact
maybe_capture_fact = store.maybe_capture_fact

