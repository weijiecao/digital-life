"""Local candidate retrieval for V1.5-B.

This module intentionally does not call an LLM. It provides an EventAnchor
projection and a lexical (BM25-like) candidate index so candidate recall can
be measured separately from relation judging.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _terms(text: str) -> list[str]:
    terms: list[str] = []
    for run in CJK_RE.findall(text or ""):
        if len(run) == 1:
            terms.append(run)
            continue
        terms.extend(run[i:i + 2] for i in range(len(run) - 1))
    # Keep ASCII words/numbers (dates, codes) as terms too.
    terms.extend(re.findall(r"[A-Za-z0-9_]+", text or ""))
    return terms


@dataclass
class EventAnchor:
    event_id: str
    scope_id: str
    event_type: str = "unknown"
    canonical_title: str = ""
    anchor_text: str = ""
    aliases: list[str] = field(default_factory=list)
    evidence_refs: list[int] = field(default_factory=list)
    version: int = 1

    def searchable_text(self) -> str:
        return " ".join([self.canonical_title, self.anchor_text, *self.aliases]).strip()


@dataclass
class Candidate:
    event_id: str
    score: float
    reasons: list[str]
    anchor_version: int = 1


class LexicalCandidateIndex:
    """Character-bigram / token BM25-like index, scoped by owner."""

    def __init__(self):
        self._anchors: dict[tuple[str, str], EventAnchor] = {}
        self._terms: dict[tuple[str, str], Counter] = {}
        self._doc_freq: dict[str, Counter] = defaultdict(Counter)
        self._lengths: dict[tuple[str, str], int] = {}

    def add(self, anchor: EventAnchor) -> None:
        key = (anchor.scope_id, anchor.event_id)
        old_terms = self._terms.get(key)
        if old_terms is not None:
            for term in old_terms:
                self._doc_freq[anchor.scope_id][term] -= 1
        terms = Counter(_terms(anchor.searchable_text()))
        self._terms[key] = terms
        self._lengths[key] = sum(terms.values()) or 1
        for term in terms:
            self._doc_freq[anchor.scope_id][term] += 1
        self._anchors[key] = anchor

    def search(self, scope_id: str, query: str, k: int = 10) -> list[Candidate]:
        query_terms = Counter(_terms(query))
        if not query_terms:
            return []
        candidates = []
        for (scope, event_id), terms in self._terms.items():
            if scope != scope_id:
                continue
            length = self._lengths[(scope, event_id)]
            score = 0.0
            shared = []
            for term, qtf in query_terms.items():
                tf = terms.get(term, 0)
                if not tf:
                    continue
                df = max(1, self._doc_freq[scope][term])
                idf = math.log((1 + len(self._anchors)) / (1 + df)) + 1
                score += idf * (tf / (tf + 1.2 * (0.25 + 0.75 * length / 20.0))) * (qtf > 0)
                shared.append(term)
            if score > 0:
                anchor = self._anchors[(scope, event_id)]
                candidates.append(Candidate(event_id=event_id, score=round(score, 6),
                                            reasons=["lexical:" + ",".join(shared[:8])],
                                            anchor_version=anchor.version))
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[:k]


class EmbeddingCandidateIndex:
    """Optional semantic candidate index; the encoder is injected."""

    def __init__(self, encoder):
        self.encoder = encoder
        self._anchors: dict[tuple[str, str], EventAnchor] = {}
        self._vectors: dict[tuple[str, str], list[float]] = {}

    @staticmethod
    def _normalize(vector):
        norm = math.sqrt(sum(float(x) * float(x) for x in vector))
        if norm <= 0:
            return [0.0 for _ in vector]
        return [float(x) / norm for x in vector]

    def _encode(self, texts: list[str], queries: bool = False) -> list[list[float]]:
        method = getattr(self.encoder, "encode_queries" if queries else "encode_documents", None)
        if method is None:
            method = getattr(self.encoder, "encode")
        result = method(texts)
        # sentence-transformers returns a 2-D array; custom encoders may return lists.
        return [self._normalize(list(row)) for row in result]

    def add(self, anchor: EventAnchor) -> None:
        key = (anchor.scope_id, anchor.event_id)
        old = self._anchors.get(key)
        if old is None or old.version != anchor.version or key not in self._vectors:
            self._vectors[key] = self._encode([anchor.searchable_text()])[0]
        self._anchors[key] = anchor

    def search(self, scope_id: str, query: str, k: int = 10) -> list[Candidate]:
        query_vector = self._encode([query], queries=True)[0]
        candidates = []
        for (scope, event_id), vector in self._vectors.items():
            if scope != scope_id:
                continue
            score = sum(a * b for a, b in zip(query_vector, vector))
            anchor = self._anchors[(scope, event_id)]
            candidates.append(Candidate(event_id=event_id, score=round(score, 6),
                                        reasons=["embedding"], anchor_version=anchor.version))
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[:k]


class HybridCandidateIndex:
    """Lexical + embedding candidate index; scores are rank-fused, not calibrated."""

    def __init__(self, encoder=None):
        self.lexical = LexicalCandidateIndex()
        self.embedding = EmbeddingCandidateIndex(encoder) if encoder is not None else None
        self._anchors: dict[tuple[str, str], EventAnchor] = {}

    def add(self, anchor: EventAnchor) -> None:
        self.lexical.add(anchor)
        if self.embedding is not None:
            self.embedding.add(anchor)
        self._anchors[(anchor.scope_id, anchor.event_id)] = anchor

    def search(self, scope_id: str, query: str, k: int = 10) -> list[Candidate]:
        # RRF fuses *ranks*, not uncalibrated BM25 and cosine scores.
        # Higher ranks (1 is best) must always receive a larger contribution.
        rrf_k = 60
        scores: dict[str, float] = defaultdict(float)
        reasons: dict[str, list[str]] = defaultdict(list)
        lexical_ranks: dict[str, int] = {}
        for rank, candidate in enumerate(self.lexical.search(scope_id, query, k=max(k, 10)), start=1):
            lexical_ranks[candidate.event_id] = rank
            scores[candidate.event_id] += 1.0 / (rrf_k + rank)
            reasons[candidate.event_id].extend(candidate.reasons)
        if self.embedding is not None:
            for rank, candidate in enumerate(self.embedding.search(scope_id, query, k=max(k, 10)), start=1):
                scores[candidate.event_id] += 1.0 / (rrf_k + rank)
                reasons[candidate.event_id].extend(candidate.reasons)
        # A perfectly opposed pair of ranks ties under equal-weight RRF.
        # Prefer the lexical rank as the deterministic tie-breaker.
        ranked = sorted(scores.items(),
                        key=lambda item: (-item[1], lexical_ranks.get(item[0], float("inf")), item[0]))[:k]
        return [Candidate(event_id=event_id, score=round(score, 6),
                          reasons=reasons[event_id][:8],
                          anchor_version=self._anchors[(scope_id, event_id)].version)
                for event_id, score in ranked]


def build_anchors_from_groups(groups: Iterable[dict], scope: str,
                              messages: dict[int, str]) -> list[EventAnchor]:
    """Evaluation helper: first message of each group becomes the anchor."""
    anchors = []
    for group in groups:
        seqs = group.get("seqs") or []
        if not seqs:
            continue
        first = seqs[0]
        anchors.append(EventAnchor(
            event_id=group["id"], scope_id=scope,
            canonical_title=messages.get(first, "")[:80],
            anchor_text=messages.get(first, ""),
            evidence_refs=[first],
        ))
    return anchors
