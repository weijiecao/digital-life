"""记忆基座输出服务：检索、证据复验、预算组装、最终请求注入。"""
from __future__ import annotations

import json
from memory_output import (OutputRequest, SourceSpan, MemoryItem, ContextOutput,
                           AstrBotOutputAdapter, DeliveryReceipt, fit_output, render)
from memory_output.contracts import SCHEMA_VERSION, canonical
from memory_output.compose import count_tokens
from memory_engine import admission


class MemoryOutputService:
    def __init__(self, engine, token_counter=None, adapter=None):
        self.engine = engine
        self.store = engine.store
        self.counter = token_counter
        self.adapter = adapter or AstrBotOutputAdapter()

    @staticmethod
    def _budget(request, host_budget):
        if host_budget is None:
            return request.budget_tokens
        for name in ('total_tokens', 'system_tokens', 'history_tokens', 'output_tokens'):
            value = getattr(host_budget, name, None)
            if type(value) is not int or value < 0:
                raise ValueError('invalid host budget')
        return min(request.budget_tokens, host_budget.memory_tokens)

    def _item(self, scope, query, event_id, certificate):
        snapshot = self.engine._snapshot(scope, event_id)
        if snapshot is None or not admission.validate(certificate, query, snapshot):
            raise ValueError('invalid evidence certificate')
        spans = []
        for s in certificate['supporting_spans']:
            row = self.store.db.execute('''SELECT recorded_at FROM source_ref
                WHERE scope_id=? AND source_seq=? AND source_hash=? AND deleted_at IS NULL''',
                (scope, s['source_seq'], s['source_hash'])).fetchone()
            if row is None:
                raise ValueError('source changed')
            spans.append(SourceSpan(s['source_seq'], s['source_hash'], s['start'], s['end'],
                                    s['text'], row[0] or None))
        return MemoryItem(event_id, snapshot.revision, snapshot.fingerprint,
                          snapshot.provenance, tuple(spans),
                          canonical(certificate))

    def prepare(self, request: OutputRequest, host_budget=None):
        budget = self._budget(request, host_budget)
        epoch = self.store.scope_epoch(request.scope_id)
        if budget == 0 or self.counter is None:
            return fit_output(request, epoch, (), self.counter, budget)
        try:
            trace = self.engine.retrieve_with_audit(request.scope_id, request.query)
            if trace.get('abstained_reason'):
                return fit_output(request, epoch, (), self.counter, budget,
                                  (trace['abstained_reason'],))
            items = tuple(self._item(request.scope_id, request.query, item['event_id'], item['admission'])
                          for item in trace['items'])
        except Exception:
            return fit_output(request, epoch, (), self.counter, budget, ('retrieval_or_evidence_error',))
        output = fit_output(request, epoch, items, self.counter, budget)
        if not self.validate(output, request):
            return ContextOutput(request, self.store.scope_epoch(request.scope_id),
                                 budget_tokens=budget, reasons=('scope_or_evidence_changed',))
        return output

    def validate(self, output, expected):
        try:
            if (not isinstance(output, ContextOutput) or output.request != expected
                    or output.schema_version != SCHEMA_VERSION or output.persist is not False
                    or type(output.budget_tokens) is not int
                    or not 0 <= output.budget_tokens <= expected.budget_tokens
                    or self.store.scope_epoch(expected.scope_id) != output.scope_epoch):
                return False
            if output.text != render(output.items):
                return False
            if not output.items:
                return output.text == '' and output.used_tokens == 0
            if self.counter is None or output.tokenizer != self.counter.encoding_name:
                return False
            count = count_tokens(self.counter, output.text)
            if count != output.used_tokens or count > output.budget_tokens:
                return False
            ids = set()
            for item in output.items:
                if item.event_id in ids:
                    return False
                ids.add(item.event_id)
                current = self._item(expected.scope_id, expected.query, item.event_id,
                                     json.loads(item.certificate_json))
                if current != item:
                    return False
            return self.store.scope_epoch(expected.scope_id) == output.scope_epoch
        except Exception:
            return False

    def inject(self, host_request, output, expected, host_budget=None):
        # 提交瞬间检查、安装在一个短事务中完成；没有模型调用或网络等待。
        try:
            budget = self._budget(expected, host_budget)
            if output.used_tokens > budget:
                self.adapter.clear(host_request)
                return DeliveryReceipt('REJECTED', expected.request_id, reason='host_budget_changed')
            with self.store.unit_of_work():
                return self.adapter.apply(host_request, output, expected, self.validate)
        except Exception:
            self.adapter.clear(host_request)
            return DeliveryReceipt('REJECTED', expected.request_id, reason='output_unavailable')
