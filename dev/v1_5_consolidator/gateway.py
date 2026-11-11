"""ContextGateway and model provider ports for V1.5."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from .contracts import SourceEnvelope


@dataclass
class PreparedContext:
    request_id: str
    scope_id: str
    prompt: str
    sources: list[SourceEnvelope]
    candidates: list[dict[str, Any]]
    policy_version: str


@dataclass
class PreparedRelationContext:
    request_id: str
    scope_id: str
    source: SourceEnvelope
    prompt: str
    candidates: list[dict[str, Any]]
    policy_version: str


class ContextGateway:
    def __init__(self, store, policy_version: str = "v1.5-consolidation-1",
                 max_chars: int = 6000, deny_external: bool = True):
        self.store = store
        self.policy_version = policy_version
        self.max_chars = int(max_chars)
        self.deny_external = bool(deny_external)

    def prepare(self, scope_id: str, sources: list[SourceEnvelope],
                candidates: list[dict[str, Any]], purpose: str = "event_consolidation") -> PreparedContext:
        lines = [
            "你是事件归纳候选生成器，不是事实裁决器。",
            "只输出 JSON；不要输出解释、SQL、工具调用。",
            "把输入原话划分为事件候选，并在给定候选事件中选择 update/link；没有合适候选时 create。",
            "一个事件可以由多条原话共同支持；同一主题/同一计划/同一经历的连续消息必须合并，"
            "不要每条消息创建一个事件。只有主体、主题或时间明显不同才 create 新事件。",
            "同一次外出/计划中的日期、星期、购买、餐饮、地点补充，即使句子本身是独立陈述，"
            "只要明显服务于同一计划，也应归入同一事件；不要把上下文句拆成单独事件。",
            "同一目的地的不同月份/日期是不同事件；不能仅因目的地相同就合并。"
            "如果原话中的明确日期/时间互相冲突，必须拆成不同事件。",
            "“之前那件事/后来呢/上次那个”等追问或回顾，如果指向本批次或候选中的事件，"
            "应 update/link 该事件，不要单独 create。",
            "重复或补充说明应放入同一个 create 的 source_refs，或对已有候选 update；"
            "不要把同一件事拆成多个事件。",
            "每条 operation 必须引用 source_seq，并给出原文 quote；quote 必须是原文子串。",
            "保留计划/可能/已完成/转述/故事等模态；不得把可能写成已完成。",
            "无法判断时使用 unknown/defer，不要编造时间或来源。",
            "输出结构：",
            '{"schema_version":"1.0","operations":[{"action":"create|update|link|defer",'
            '"event_type":"plan|experience|state_change|narrative|unknown","title":"...",'
            '"summary":"...","modality":"completed|planned|possible|quoted|narrative|unknown",'
            '"status_proposal":"planned|possibly_postponed|cancelled|completed|ongoing|uncertain|withdrawn|unknown",'
            '"candidate_event_id":null,"occurred_at":null,"time_expression_raw":null,'
            '"source_refs":[{"source_seq":1,"quote":"..."}],"uncertainties":[]}]}',
            "",
            "scope: " + scope_id,
            "候选事件（只能引用这些 ID）：",
        ]
        for item in candidates:
            lines.append("- %s | %s | %s" % (item["event_id"], item["event_type"], item["title"]))
        if not candidates:
            lines.append("- （无）")
        lines.append("新增原话：")
        for source in sources:
            lines.append("[%d] %s" % (source.source_seq, source.text))
        prompt = "\n".join(lines)
        if len(prompt) > self.max_chars:
            raise ValueError("输入超过 ContextGateway 字符预算：%d > %d" % (len(prompt), self.max_chars))
        request_id = "ctx_" + uuid.uuid4().hex
        refs = [s.ref() for s in sources]
        self.store.audit(request_id, purpose, scope_id, self.policy_version, "prepared", refs)
        return PreparedContext(request_id=request_id, scope_id=scope_id, prompt=prompt,
                               sources=sources, candidates=candidates,
                               policy_version=self.policy_version)

    def prepare_relation(self, scope_id: str, source: SourceEnvelope,
                         candidates: list[dict[str, Any]],
                         purpose: str = "relation_judge",
                         context: list[SourceEnvelope] | None = None) -> PreparedRelationContext:
        context = context or []
        lines = [
            "你是事件关系判别器，不是事实生成器。",
            "只判断当前原话与每个候选事件是否同一具体事件；只输出 JSON。",
            "标签只能用：SAME_EVENT（同一具体事件/持续计划）、DIFFERENT_EVENT（不同事件）、"
            "RELATED_CONTEXT（同主题/相关但不足以同一事件）、UNCERTAIN（证据不足）。",
            "同目的地但不同日期/不同次出行不能标 SAME_EVENT；叙述/转述不能当作本人经历。",
            "同一旅行/计划的酒店、预算、日期、地点、行程补充属于 SAME_EVENT；"
            "只有同主题但不同次出行/不同目标才用 RELATED_CONTEXT 或 DIFFERENT_EVENT。"
            "不要因为原话简短或只是补充细节就降级为 RELATED_CONTEXT。",
            "必须为每个候选返回一条 relation；不要新增候选 ID；reason_codes 只是理由声明。",
            "同时判断当前原话的叙事来源：narrator=user|friend|story_character|unknown，"
            "subject=user|other|story_character|unknown，reality_status=real|reported|fictional|unknown；"
            "第一人称虚构不能因为出现“我”就当作用户真实经历。"
            "如果候选带有 story_id，且当前原话明显属于同一故事，source_provenance.story_id 应填同一 ID；"
            "否则不要复制候选的 story_id。",
            "输出结构：",
            '{"schema_version":"v1.5b.1","source_provenance":{"narrator":"user|friend|story_character|unknown",'
            '"subject":"user|other|story_character|unknown","reality_status":"real|reported|fictional|unknown",'
            '"story_id":null},"relations":[{"candidate_event_id":"evt_x",'
            '"label":"SAME_EVENT|DIFFERENT_EVENT|RELATED_CONTEXT|UNCERTAIN",'
            '"reason_codes":["..."]}],"suggested_action":"ATTACH_EXISTING|NEW_EVENT|DEFER",'
            '"target_event_id":null}',
            "",
            "scope: " + scope_id,
            "当前原话 [%d]: %s" % (source.source_seq, source.text),
            "最近上下文（仅用于判断指代，不得当作新事实）：",
        ]
        if context:
            for item in context:
                lines.append("[%d] %s" % (item.source_seq, item.text))
        else:
            lines.append("（无）")
        lines.append("候选事件：")
        for item in candidates:
            lines.append("- %s | %s | %s" % (
                item["event_id"], item.get("event_type", "unknown"),
                item.get("title") or item.get("canonical_title") or ""))
            anchor = item.get("anchor_text") or ""
            if anchor:
                lines.append("  anchor: " + anchor[:500])
            provenance = item.get("provenance") or {}
            if provenance:
                lines.append("  provenance: narrator=%s subject=%s reality=%s story_id=%s" % (
                    provenance.get("narrator", "unknown"),
                    provenance.get("subject", "unknown"),
                    provenance.get("reality_status", "unknown"),
                    provenance.get("story_id")))
        prompt = "\n".join(lines)
        if len(prompt) > self.max_chars:
            raise ValueError("关系判别超过 ContextGateway 字符预算：%d > %d" % (len(prompt), self.max_chars))
        request_id = "ctx_rel_" + uuid.uuid4().hex
        self.store.audit(request_id, purpose, scope_id, self.policy_version, "prepared",
                         [source.ref()] + [item.ref() for item in context])
        return PreparedRelationContext(request_id=request_id, scope_id=scope_id,
                                       source=source, prompt=prompt,
                                       candidates=candidates,
                                       policy_version=self.policy_version)


@dataclass
class ModelResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""


class MockProvider:
    external = False

    def __init__(self, response: str | dict[str, Any] | None = None):
        self.response = response

    def generate(self, prepared: PreparedContext) -> ModelResult:
        if self.response is not None:
            text = (json.dumps(self.response, ensure_ascii=False)
                    if not isinstance(self.response, str) else self.response)
            return ModelResult(text=text, model="mock")
        sources = prepared.sources
        refs = [{"source_seq": s.source_seq, "quote": s.text} for s in sources]
        proposal = {"schema_version": "1.0", "operations": [{
            "action": "create", "event_type": "experience",
            "title": sources[0].text[:30] if sources else "候选事件",
            "summary": "；".join(s.text for s in sources)[:300],
            "modality": "unknown", "status_proposal": "unknown",
            "candidate_event_id": None, "occurred_at": None,
            "time_expression_raw": None, "source_refs": refs, "uncertainties": [],
        }]}
        return ModelResult(text=json.dumps(proposal, ensure_ascii=False), model="mock")


class OpenAIProvider:
    external = True

    def __init__(self, base_url: str, model: str, api_key: str, timeout: int = 60,
                 retries: int = 2, retry_backoff: float = 0.2):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = max(0, retries)
        self.retry_backoff = retry_backoff

    def generate(self, prepared: PreparedContext) -> ModelResult:
        import http.client
        import time
        import urllib.error
        import urllib.request
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prepared.prompt}],
            "temperature": 0,
            "max_tokens": 4000,
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key}, method="POST")
        transient = (urllib.error.URLError, TimeoutError, ConnectionError,
                     http.client.IncompleteRead)
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError:
                raise
            except transient:
                if attempt >= self.retries:
                    raise
                time.sleep(self.retry_backoff * (2 ** attempt))
        usage = payload.get("usage") or {}
        text = payload["choices"][0]["message"]["content"]
        return ModelResult(text=text, tokens_in=int(usage.get("prompt_tokens", 0)),
                           tokens_out=int(usage.get("completion_tokens", 0)), model=self.model)
