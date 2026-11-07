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
            "“之前那件事/后来呢/上次那个”等追问或回顾，如果指向本批次或候选中的事件，"
            "应 update/link 该事件，不要单独 create。",
            "重复或补充说明应放入同一个 create 的 source_refs，或对已有候选 update；"
            "不要把同一件事拆成多个事件。",
            "每条 operation 必须引用 source_seq，并给出原文 quote；quote 必须是原文子串。",
            "保留计划/可能/已完成/转述/故事等模态；不得把可能写成已完成。",
            "无法判断时使用 unknown/defer，不要编造时间或来源。",
            "输出结构：",
            '{"schema_version":"1.0","operations":[{"action":"create|update|link|defer",'
            '"event_type":"plan|experience|state_change|narrative","title":"...",'
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

    def __init__(self, base_url: str, model: str, api_key: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def generate(self, prepared: PreparedContext) -> ModelResult:
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
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        usage = payload.get("usage") or {}
        text = payload["choices"][0]["message"]["content"]
        return ModelResult(text=text, tokens_in=int(usage.get("prompt_tokens", 0)),
                           tokens_out=int(usage.get("completion_tokens", 0)), model=self.model)
