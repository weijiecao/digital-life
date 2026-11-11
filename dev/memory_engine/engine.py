"""Minimal independent offline text engine façade.

This is the first M3 slice: source ingest, lexical retrieval, context packet and
source deletion on top of V15Store. It intentionally does not add model calls or
host integration.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass

from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store

from memory_engine.cache import ContextCache
from memory_engine.projection import classify_memory
from memory_engine.summary_cache import SummaryCache
from v1_5_consolidator.candidate_retriever import (
    EventAnchor, HybridCandidateIndex, LexicalCandidateIndex)


def _question_content(query: str) -> str:
    """Remove question scaffolding before lexical fallback, not topic words.

    Spaces preserve token boundaries: removing words must not synthesize new
    Chinese bigrams. No remaining content means abstain from fallback.
    This is a conservative lexical rule, not semantic query understanding.
    """
    scaffolding = (
        r"什么时候|为什么|是不是|麻烦|回忆|之前|原来|现在|最新|最后|最终|"
        r"怎么|什么|是否|哪个|哪里|多少|如何|对比|自己的|自己|我的|我们|"
        r"安排|确认|定成|定在|改成|说的|我|的|了|吗|呢|是|在|下"
    )
    content = re.sub(scaffolding, " ", query)
    # Isolated Chinese characters are too weak for automatic context injection.
    return " ".join(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]+", content))


def _answerable(query: str, text: str, semantic_score: float = 0.0) -> bool:
    """Conservative evidence admission for one candidate.

    Candidate recall may be semantic; this gate keeps unsupported values,
    historical/superseded records, negative branches and unrecorded attributes
    out of the context packet.
    """
    if not text:
        return False
    historical = re.search(r"\u521d\u7a3f|\u539f\u6765|\u539f\u5148|\u539f\u59cb|\u539f\u8ba1\u5212|\u539f\u5b89\u6392|\u65e7|\u521d\u59cb", query)
    current = re.search(r"\u73b0\u5728|\u6700\u65b0|\u6700\u540e|\u5f53\u524d|\u5bf9\u6bd4", query)
    if historical and not current:
        return False
    if re.search(r"\u64a4\u56de|\u5e9f\u5f03|\u5907\u4efd|\u4e34\u65f6\u65b9\u6848|\u9694\u58c1|\u5176\u4ed6\u7ec4|\u522b\u7684\u7ec4", query):
        return False
    if re.search(r"\u4ed8\u6b3e|\u51ed\u8bc1|\u53d1\u7968|\u8054\u7cfb\u4eba|\u7535\u8bdd|\u53f7\u7801|\u8d26\u53f7|\u5bc6\u7801|\u5408\u540c|\u7f16\u53f7|\u7eaa\u8981", query):
        if not all(term in text for term in re.findall(r"\u4ed8\u6b3e|\u51ed\u8bc1|\u53d1\u7968|\u8054\u7cfb\u4eba|\u7535\u8bdd|\u53f7\u7801|\u8d26\u53f7|\u5bc6\u7801|\u5408\u540c|\u7f16\u53f7|\u7eaa\u8981", query)):
            return False
    if not re.search(r"\u662f\u4e0d\u662f|\u662f\u5426|\u5417|\u5462|\u600e\u4e48|\u4ec0\u4e48|\u5982\u4f55|\u54ea|\u51e0|\u591a\u5c11|\u4f55\u65f6", query):
        match = re.search(r"(?:\u6700\u7ec8\u786e\u8ba4|\u786e\u8ba4|\u5b89\u6392\u662f|\u5b89\u6392\u4e3a|\u5b9a\u5728|\u6539\u6210|\u6539\u4e3a|\u662f|\u4e3a)([\u4e00-\u9fffA-Za-z0-9]{2,12})", query)
        if match and match.group(1) not in text:
            return False
    content = _question_content(query)
    if not content.strip():
        return query.strip() in text
    chars = set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", content))
    if not chars:
        return query.strip() in text
    if len(chars & set(text)) < 1 and semantic_score < 0.45:
        return False
    return True


def _provenance_allows(query: str, provenance: dict) -> bool:
    """Reject an explicitly different subject/story even if it is near in scope."""
    subject = provenance.get("subject", "unknown")
    reality = provenance.get("reality_status", "unknown")
    if re.search(r"\u670b\u53cb|\u540c\u4e8b|\u4ed6\u4eba|\u522b\u4eba|\u5979|\u4ed6", query):
        if subject == "user":
            return False
    if re.search(r"\u6545\u4e8b|\u865a\u6784|\u89d2\u8272", query):
        if reality != "fictional":
            return False
    if re.search(r"\u771f\u5b9e|\u73b0\u5b9e|\u672c\u4eba", query):
        if reality not in ("real", "unknown"):
            return False
    if re.search(r"\u6211|\u81ea\u5df1", query) and not re.search(r"\u6211\u7684\u670b\u53cb|\u6211\u7684\u540c\u4e8b", query):
        if subject not in ("user", "unknown"):
            return False
    return True


@dataclass(frozen=True)
class CaptureReceipt:
    scope_id: str
    source_seq: int
    event_id: str
    receipt_ok: bool


class MemoryEngine:
    def __init__(self, store: V15Store, cache: ContextCache | None = None,
                 summary_cache: SummaryCache | None = None, encoder=None):
        self.store = store
        self.cache = cache or ContextCache()
        self.summary_cache = summary_cache or SummaryCache()
        self.encoder = encoder
        self._generation = defaultdict(int)
        self._indexes: dict[str, object] = {}
        self._index_rows: dict[str, dict] = {}
        self._index_generation: dict[str, int] = {}
        self._index_epoch: dict[str, int] = {}

    def _bump(self, scope_id: str) -> None:
        self._generation[scope_id] += 1
        self.cache.invalidate_scope(scope_id)
        self._indexes.pop(scope_id, None)
        self._index_rows.pop(scope_id, None)
        self._index_generation.pop(scope_id, None)
        self._index_epoch.pop(scope_id, None)

    def _ensure_index(self, scope_id: str) -> None:
        epoch = self.store.scope_epoch(scope_id)
        if (scope_id in self._indexes
                and self._index_generation.get(scope_id) == self._generation[scope_id]
                and self._index_epoch.get(scope_id) == epoch):
            return
        index = HybridCandidateIndex(self.encoder)
        rows = self.store.db.execute("""SELECT DISTINCT c.event_id,c.title,c.event_type,
            r.summary,e.source_seq,e.quote
            FROM event_candidate c
            JOIN event_revision r ON r.event_id=c.event_id AND r.rev=c.current_revision
            JOIN event_evidence e ON e.event_id=c.event_id AND e.rev=c.current_revision
            WHERE c.scope_id=? AND c.state!='withdrawn' AND e.valid=1
            AND NOT EXISTS (SELECT 1 FROM resolution_pending p
                WHERE p.scope_id=c.scope_id AND p.source_seq=e.source_seq
                AND p.status='PENDING')""", (scope_id,)).fetchall()
        by_id = {}
        for row in rows:
            by_id[row[0]] = row
            text = " ".join(str(part or "") for part in (row[1], row[3], row[5]))
            index.add(EventAnchor(event_id=row[0], scope_id=scope_id,
                                  canonical_title=row[1] or "", anchor_text=text))
        self._indexes[scope_id] = index
        self._index_rows[scope_id] = by_id
        self._index_generation[scope_id] = self._generation[scope_id]
        self._index_epoch[scope_id] = epoch

    def ingest(self, scope_id: str, source: SourceEnvelope,
               provenance: dict | None = None) -> CaptureReceipt:
        existing = self.store.receipt_for_source(scope_id, source.source_seq)
        if existing is not None:
            events = self.store.events_for_source(scope_id, source.source_seq)
            return CaptureReceipt(scope_id, source.source_seq,
                                  events[0] if events else "",
                                  existing["input_hash"] == source.source_hash)
        with self.store.unit_of_work():
            event_id = self.store.create_event_from_source(
                scope_id, source, event_type="unknown",
                title=source.text[:80], provenance=provenance)
            item_type, modality = classify_memory(source.text)
            self.store.save_memory_item(scope_id, event_id, item_type,
                                        source.text, modality)
            self.store.attach_source(scope_id, source, event_id, "engine-ingest")
            receipt_ok = self.store.record_receipt(
                scope_id, source.source_seq, "ATTACHED", source.source_hash)
        self.store.bump_scope_epoch(scope_id)
        self._bump(scope_id)
        return CaptureReceipt(scope_id, source.source_seq, event_id, receipt_ok)

    def _event_allowed(self, query: str, event_id: str) -> bool:
        return _provenance_allows(query, self.store.event_provenance(event_id))

    def retrieve(self, scope_id: str, query: str, limit: int = 10,
                 min_score: float = 0.0) -> list[dict]:
        if self.encoder is not None and query:
            self._ensure_index(scope_id)
            index = self._indexes[scope_id]
            by_id = self._index_rows[scope_id]
            ranked = index.search(scope_id, query, k=max(limit, 20),
                                  min_score=min_score)
            ids = [c.event_id for c in ranked if c.event_id in by_id]
            if not ids:
                return []
            marks = ",".join("?" for _ in ids)
            current = self.store.db.execute("""SELECT DISTINCT c.event_id,c.title,
                c.event_type,r.summary,e.source_seq,e.quote
                FROM event_candidate c
                JOIN event_revision r ON r.event_id=c.event_id AND r.rev=c.current_revision
                JOIN event_evidence e ON e.event_id=c.event_id AND e.rev=c.current_revision
                WHERE c.scope_id=? AND c.event_id IN (%s)
                AND c.state!='withdrawn' AND e.valid=1
                AND NOT EXISTS (SELECT 1 FROM resolution_pending p
                    WHERE p.scope_id=c.scope_id AND p.source_seq=e.source_seq
                    AND p.status='PENDING')""" % marks, (scope_id, *ids)).fetchall()
            live = {row[0]: row for row in current}
            rows = [live[event_id] for event_id in ids if event_id in live]
            semantic = index.semantic_scores(scope_id, query, [row[0] for row in rows]) if self.encoder is not None else {}
            rows = [row for row in rows if _answerable(
                query, " ".join(str(part or "") for part in (row[1], row[3], row[5])),
                semantic.get(row[0], 0.0)) and self._event_allowed(query, row[0])]
            return [{"event_id": row[0], "title": row[1], "event_type": row[2],
                     "summary": self.summary_cache.get(row[3] or row[1] or ""),
                     "evidence": row[5]} for row in rows[:limit]]
        pattern = "%" + query.replace("%", "").replace("_", "") + "%"
        rows = self.store.db.execute("""SELECT DISTINCT c.event_id,c.title,c.event_type,
            r.summary,e.quote
            FROM event_candidate c
            JOIN event_revision r ON r.event_id=c.event_id AND r.rev=c.current_revision
            JOIN event_evidence e ON e.event_id=c.event_id AND e.rev=c.current_revision
            WHERE c.scope_id=? AND c.state!='withdrawn' AND e.valid=1
            AND NOT EXISTS (SELECT 1 FROM resolution_pending p
                WHERE p.scope_id=c.scope_id AND p.source_seq=e.source_seq
                AND p.status='PENDING')
            AND (c.title LIKE ? OR r.summary LIKE ? OR e.quote LIKE ?)
            ORDER BY c.updated_at DESC LIMIT ?""",
            (scope_id, pattern, pattern, pattern, max(limit, 100))).fetchall()
        if query and len(rows) < limit and re.search(
                r"怎么|什么|是不是|是否|哪|多少|如何|为什么|吗|呢|回忆|对比|安排", query):
            index = LexicalCandidateIndex()
            all_rows = self.store.db.execute("""SELECT DISTINCT c.event_id,c.title,c.event_type,
                r.summary,e.quote
                FROM event_candidate c
                JOIN event_revision r ON r.event_id=c.event_id AND r.rev=c.current_revision
                JOIN event_evidence e ON e.event_id=c.event_id AND e.rev=c.current_revision
                WHERE c.scope_id=? AND c.state!='withdrawn' AND e.valid=1
                AND NOT EXISTS (SELECT 1 FROM resolution_pending p
                    WHERE p.scope_id=c.scope_id AND p.source_seq=e.source_seq
                    AND p.status='PENDING')""", (scope_id,)).fetchall()
            by_id = {row[0]: row for row in all_rows}
            for row in all_rows:
                index.add(EventAnchor(
                    event_id=row[0], scope_id=scope_id,
                    canonical_title=row[1] or "",
                    anchor_text=" ".join(str(part or "") for part in
                                         (row[1], row[3], row[4]))))
            ranked = index.search(scope_id, _question_content(query), k=limit,
                                  min_score=0.0)
            # Exact matches keep priority; fallback only adds supported topics.
            seen = {row[0] for row in rows}
            rows.extend(by_id[c.event_id] for c in ranked if c.event_id not in seen)
        if min_score > 0 and query:
            index = LexicalCandidateIndex()
            by_id = {}
            for row in rows:
                by_id[row[0]] = row
                index.add(EventAnchor(
                    event_id=row[0], scope_id=scope_id,
                    canonical_title=row[1] or "",
                    anchor_text=" ".join(str(part or "") for part in
                                         (row[1], row[3], row[4]))))
            ranked = index.search(scope_id, query, k=limit, min_score=min_score)
            rows = [by_id[c.event_id] for c in ranked if c.event_id in by_id]
        else:
            rows = rows[:limit]
        rows = [row for row in rows if _answerable(query, " ".join(str(part or "") for part in (row[1], row[3], row[4]))) and self._event_allowed(query, row[0])]
        return [{"event_id": row[0], "title": row[1], "event_type": row[2],
                 "summary": self.summary_cache.get(row[3] or row[1] or ""),
                 "evidence": row[4]} for row in rows]

    def build_context(self, scope_id: str, query: str, budget: int = 2000,
                      budget_tokens: int | None = None,
                      token_counter=None, min_score: float = 0.0) -> dict:
        if budget_tokens is not None and token_counter is None:
            budget = min(budget, max(0, budget_tokens) * 4)
        tokenizer_name = getattr(token_counter, "encoding_name", None)
        db_version = self.store.db.execute("PRAGMA data_version").fetchone()[0]
        epoch = self.store.scope_epoch(scope_id)
        key = (scope_id, query, budget, budget_tokens, tokenizer_name,
               db_version, epoch, min_score)
        generation = self._generation[scope_id]
        cached = self.cache.get(key, generation)
        if cached is not None:
            return cached
        items = self.retrieve(scope_id, query, limit=10, min_score=min_score)
        selected = []
        used = 0
        used_tokens = 0
        for item in items:
            chars = len(item.get("summary", "")) + len(item.get("evidence", ""))
            tokens = (token_counter.count(item.get("summary", ""))
                      + token_counter.count(item.get("evidence", ""))
                      if token_counter is not None else None)
            if token_counter is not None and budget_tokens is not None:
                if used_tokens + tokens > budget_tokens:
                    break
                used_tokens += tokens
            elif used + chars > budget:
                break
            selected.append(item)
            used += chars
        packet = {"schema_version": "memory-context-v1", "scope_id": scope_id,
                  "query": query, "items": selected, "source_refs": [
                      {"event_id": item["event_id"]} for item in selected],
                  "budget_chars": budget, "used_chars": used,
                  "budget_tokens": budget_tokens,
                  "used_tokens": (used_tokens if token_counter is not None
                                  else ((used + 3) // 4 if budget_tokens is not None else None)),
                  "tokenizer": tokenizer_name,
                  "degraded": len(selected) < len(items)}
        if token_counter is not None and budget_tokens is not None:
            from memory_engine.render import fit_packet
            packet = fit_packet(packet, token_counter, budget_tokens)
        self.cache.put(key, generation, packet)
        return packet

    def delete_source(self, scope_id: str, source_seq: int) -> int:
        count = self.store.delete_source(scope_id, source_seq)
        self._bump(scope_id)
        return count

    def revise_source(self, scope_id: str, old_source_seq: int,
                      new_source: SourceEnvelope,
                      provenance: dict | None = None) -> str:
        """Replace a source's current membership with a new event."""
        identity = self.store.revision_identity(
            scope_id, old_source_seq, new_source.source_seq)
        if identity and identity["input_hash"] != new_source.source_hash:
            raise ValueError("revision identity hash conflict")
        existing = self.store.revision_result(
            scope_id, old_source_seq, new_source.source_seq, new_source.source_hash)
        if existing:
            return existing
        with self.store.unit_of_work():
            identity = self.store.revision_identity(
                scope_id, old_source_seq, new_source.source_seq)
            if identity and identity["input_hash"] != new_source.source_hash:
                raise ValueError("revision identity hash conflict")
            existing = self.store.revision_result(
                scope_id, old_source_seq, new_source.source_seq, new_source.source_hash)
            if existing:
                return existing
            old_events = self.store.events_for_source(scope_id, old_source_seq)
            if provenance is None and old_events:
                provenance = self.store.event_provenance(old_events[0])
            self.store.invalidate_source_evidence(scope_id, old_source_seq)
            event_id = self.store.create_event_from_source(
                scope_id, new_source, event_type="unknown",
                title=new_source.text[:80], provenance=provenance)
            self.store.attach_source(scope_id, new_source, event_id, "revise")
            self.store.record_receipt(scope_id, new_source.source_seq,
                                      "REVISED", new_source.source_hash)
            for old_event in old_events:
                self.store.db.execute("""INSERT INTO event_relation
                    (scope_id,from_event,to_event,relation_type,status,source_seq,created_at)
                    VALUES(?,?,?,'SUPERSEDES','active',?,?)""",
                    (scope_id, old_event, event_id, new_source.source_seq,
                     "2026-11-10T00:00:00+00:00"))
            self.store.save_revision_result(
                scope_id, old_source_seq, new_source.source_seq,
                new_source.source_hash, event_id)
        self.store.bump_scope_epoch(scope_id)
        self._bump(scope_id)
        return event_id

    def withdraw_event(self, scope_id: str, event_id: str) -> bool:
        ok = self.store.withdraw_event(scope_id, event_id)
        self._bump(scope_id)
        return ok

    def build_rendered_context(self, scope_id: str, query: str,
                               budget_tokens: int, token_counter=None,
                               min_score: float = 0.0,
                               host_budget=None,
                               strict_tokenizer: bool = True) -> dict:
        """Render the final model block and enforce its token budget.

        Without a real counter the default is strict: emit no memory text
        rather than claiming an estimated block is budget-enforced. Set
        strict_tokenizer=False only for explicit legacy/estimated callers.
        """
        from memory_engine.render import fit_packet, render_context
        if host_budget is not None:
            budget_tokens = host_budget.memory_tokens
        packet = self.build_context(scope_id, query, budget=10 ** 9,
                                    min_score=min_score)
        if token_counter is None:
            if strict_tokenizer:
                return {
                    "schema_version": "memory-context-v1",
                    "scope_id": scope_id, "query": query,
                    "items": [], "source_refs": [],
                    "budget_chars": 0, "used_chars": 0,
                    "budget_tokens": max(0, budget_tokens),
                    "used_tokens": 0, "tokenizer": None,
                    "rendered": "", "rendered_tokens": None,
                    "budget_enforced": True, "estimated": True,
                    "degraded": True,
                    "degraded_reasons": ["tokenizer_missing"],
                }
            rendered = render_context(packet)
            packet = dict(packet)
            packet.update({"rendered": rendered, "rendered_tokens": None,
                           "budget_enforced": False, "estimated": True})
            return packet
        packet = fit_packet(packet, token_counter, budget_tokens)
        packet["estimated"] = False
        return packet

    def history(self, scope_id: str, event_id: str) -> dict:
        rows = self.store.db.execute("""SELECT from_event,to_event FROM event_relation
            WHERE scope_id=? AND relation_type='SUPERSEDES' AND status='active'""",
            (scope_id,)).fetchall()
        edges = {tuple(row) for row in rows}
        seen = {event_id}
        changed = True
        while changed:
            changed = False
            for left, right in edges:
                if left in seen or right in seen:
                    for node in (left, right):
                        if node not in seen:
                            seen.add(node)
                            changed = True
        relations = [{"from": left, "to": right} for left, right in sorted(edges)
                     if left in seen and right in seen]
        return {"event_id": event_id, "events": sorted(seen), "relations": relations}

    def health(self) -> dict:
        return {"events": self.store.db.execute(
            "SELECT COUNT(*) FROM event_candidate").fetchone()[0],
            "pending": self.store.db.execute(
                "SELECT COUNT(*) FROM resolution_pending WHERE status='PENDING'").fetchone()[0]}
