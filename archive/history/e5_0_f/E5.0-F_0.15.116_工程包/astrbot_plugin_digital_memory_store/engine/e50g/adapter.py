"""Offline fixture adapter. Transport identity and delivery are simulated.

All storage is in-memory; no connection to AstrBot or user data. This adapter
does not declare any candidate or user quotation to be an objective fact.
"""
from __future__ import annotations
import hashlib
import secrets
import time
from pathlib import Path
from ..e418.engine import UnifiedMemoryEngine, _CONFIRM
from ..e419.dynamic_index import attach_dynamic_index
from ..e421.progressive import ProgressiveRecall
from ..e50b.snapshot import load_snapshot, import_snapshot
from ..e50b.evaluate import _FixtureEncoder


class AdapterError(ValueError): pass


class FixtureAdapter:
    """One in-memory engine; the caller supplies isolated simulated actor/session keys."""
    def __init__(self, clock=time.monotonic, ttl=300):
        self.clock, self.ttl = clock, ttl
        self.flow = UnifiedMemoryEngine(':memory:')
        path = Path(__file__).resolve().parents[1] / 'e50b/fixture/reviewed_fixture.json'
        if hashlib.sha256(path.read_bytes()).hexdigest() != 'a7d9e231771269f290c88d179aeec78852d9fe54f99966ddf2f02b2807e1e6a1':
            self.flow.close()
            raise AdapterError('fixture changed')
        snapshot = load_snapshot(path, authorized=True)
        self.scope, self.mapping, self.sources = import_snapshot(self.flow, snapshot)
        self.idx = attach_dynamic_index(self.flow, _FixtureEncoder(), min_cosine=0.55, limit=3)
        self.recall = ProgressiveRecall(self.flow, self.idx.search)
        self.offers = {}

    def close(self):
        self.offers.clear()
        self.flow.close()

    def _key(self, actor, session):
        for value in (actor, session):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise AdapterError('invalid simulated identity')
        # Length-prefixing avoids ambiguous string concatenations.
        return hashlib.sha256((str(len(actor))+':'+actor+session).encode()).hexdigest()

    def clear(self, actor, session):
        key = self._key(actor, session)
        self.offers.pop(key, None)
        self.recall.clear(self.scope, key)

    def turn(self, actor, session, text):
        key = self._key(actor, session)
        if not isinstance(text, str) or not text.strip() or len(text) > 400:
            raise AdapterError('invalid clue')
        text = text.strip()
        prior = self.offers.pop(key, None)
        eligible = bool(prior and prior['presented'] and self.clock() < prior['expires'])
        confirmation = bool(_CONFIRM.fullmatch(text))
        if confirmation and not eligible:
            # Avoid accidentally confirming E4.18 state after a missing/expired receipt.
            self.recall.clear(self.scope, key)
            return {'status': 'UNCONFIRMED', 'candidates': [], 'evidence': []}
        result = self.recall.turn(scope=self.scope, session=key, question=text)
        packet = result['packet']
        # Only return source data when confirmation refers to an actual acknowledged
        # candidate. Explicit title queries alone do not bypass this adapter gate.
        if confirmation and eligible:
            evidence = packet.get('fact_evidence', [])
            return {'status': packet['status'], 'candidates': [],
                    'evidence': [{'source_id': r['message_id'], 'verbatim': r['verbatim']} for r in evidence],
                    'asserts_world_fact': False}
        associations = packet.get('associations', [])
        out = {'status': packet['status'], 'candidates': [x['title'] for x in associations], 'evidence': []}
        if len(associations) == 1:
            ticket = secrets.token_urlsafe(24)
            self.offers[key] = {'token': ticket, 'event_id': associations[0]['event_id'],
                                'expires': self.clock()+self.ttl, 'presented': False}
            out['presentation_ticket'] = ticket
        return out

    def acknowledge_presentation(self, actor, session, ticket):
        """Test harness only: call after successful simulated delivery, never on queueing."""
        key = self._key(actor, session)
        offer = self.offers.get(key)
        if not offer or not isinstance(ticket, str) or not secrets.compare_digest(ticket, offer['token']):
            raise AdapterError('no matching pending offer')
        if self.clock() >= offer['expires'] or offer['presented']:
            self.offers.pop(key, None)
            raise AdapterError('offer expired or already acknowledged')
        try:
            self.recall.mark_presented(self.scope, key, offer['event_id'])
        except Exception:
            self.offers.pop(key, None)
            raise AdapterError('offer stale or invalid') from None
        offer['presented'] = True
        return {'status': 'PRESENTED_AWAITING_CONFIRMATION'}
