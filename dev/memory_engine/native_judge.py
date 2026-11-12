"""引擎既有关系判别器的原生输入适配。

职责边界：
- 只把原生输入工作者给的（当前原话、候选事件、带角色的上下文）整理成引擎判别器
  已定义的关系判别请求，并把 ``JudgmentResult`` 原样返回；
- 不创建事件、不写归属、不修改事实；建档与归属仍由 ``NativeInputWorker`` 和基座事务完成；
- 任何解析失败、候选覆盖不全或不确定都留在 ``issues`` 里，由工作者暂缓，
  不做「猜一个」的降级。

因此本模块只增加「接线」，不改变记忆基座的质量门槛。
"""

from __future__ import annotations

import time
from typing import Any

from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.gateway import ContextGateway, MockProvider, OpenAIProvider
from v1_5_consolidator.relation_judge import JudgmentResult, RelationJudge


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


class NativeRelationJudge:
    """把 ``RelationJudge`` 适配成 ``judge(source, candidates, record, context)``。

    工作者传入的候选是原生输入侧整理过的 dict（``event_id`` / ``title`` / ``evidence``），
    上下文是同一输入流中首次接收顺序在前的 ``Record.as_dict()`` 列表，保留角色。
    本类把它们转换成引擎判别器要求的提示词结构，再由既有解析器严格校验。

    Args:
        store: 已打开的 ``V15Store``；判别器的审计记录写入该库。
        provider: 具备 ``generate(prepared) -> ModelResult`` 的 provider 端口。
        policy_version: 写入审计与判别策略版本，参与暂缓项的复判键。
        max_chars: 关系判别提示词字符上限；超出则本次暂缓而不是截断。
        context_limit: 随请求发送的先前记录条数上限（不改变工作者的 12 条上限）。
        failure_threshold: 连续失败达到该次数后进入冷却，避免持续外发与刷屏。
        cooldown_seconds: 冷却时长；冷却期间直接抛错，由工作者计为可重试错误。
    """

    def __init__(self, store, provider, *, policy_version: str = "native-input-0.2",
                 max_chars: int = 6000, context_limit: int = 12,
                 failure_threshold: int = 3, cooldown_seconds: float = 60.0):
        if provider is None:
            raise ValueError("provider is required")
        if not callable(getattr(provider, "generate", None)):
            raise ValueError("provider must implement generate(prepared)")
        self.store = store
        self.provider = provider
        self.policy_version = str(policy_version)
        # 外部 provider 必须显式解除网关的「禁止外发」，与既有判别器保持一致。
        self.gateway = ContextGateway(
            store, policy_version=self.policy_version, max_chars=int(max_chars),
            deny_external=not bool(getattr(provider, "external", False)))
        self.judge = RelationJudge(self.gateway, provider)
        self.context_limit = max(0, int(context_limit))
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._failures = 0
        self._cooldown_until = 0.0

    @property
    def revision(self) -> str:
        """判别策略版本；provider/模型变化会让暂缓项重新判别。"""
        model = _text(getattr(self.provider, "model", "")) or type(self.provider).__name__
        return "%s|%s|%s" % (self.policy_version, model, self.context_limit)

    def prepare(self, source, candidates, record, context):
        """构造判别请求（落审计，不等待模型）。

        返回 ``PreparedRelationContext``，或在提示词超预算时直接返回带 issues 的
        ``JudgmentResult``（本次暂缓，不截断候选或原话）。
        """
        if not isinstance(source, SourceEnvelope):
            raise TypeError("source must be SourceEnvelope")
        prepared = [self._candidate(item) for item in candidates or []]
        envelopes = self._context(record, context or [])
        try:
            return self.judge.prepare(record.scope_id, source, prepared, context=envelopes)
        except ValueError as exc:
            # 提示词超过字符预算：暂缓，不截断候选或原话。
            return JudgmentResult(source_seq=source.source_seq, issues=["prompt:" + str(exc)])

    def generate(self, prepared):
        """独立模型线程：只有网络等待与失败冷却，不碰数据库。"""
        if self._cooldown_until > time.monotonic():
            raise RuntimeError("relation judge cooling down after repeated failures")
        try:
            result = self.provider.generate(prepared)
        except Exception:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._failures = 0
                self._cooldown_until = time.monotonic() + self.cooldown_seconds
            raise
        return result

    def complete(self, prepared, result):
        """纯解析与校验；不碰数据库，可在模型线程执行。"""
        judgment = self.judge.complete(prepared, result)
        self._failures = 0
        return judgment

    def __call__(self, source, candidates, record, context) -> JudgmentResult:
        prepared = self.prepare(source, candidates, record, context)
        if isinstance(prepared, JudgmentResult):
            return prepared
        return self.complete(prepared, self.generate(prepared))

    @staticmethod
    def _candidate(item: dict) -> dict:
        if not isinstance(item, dict) or not _text(item.get("event_id")):
            raise ValueError("candidate requires event_id")
        evidence = item.get("evidence")
        anchor = item.get("anchor_text")
        if not isinstance(anchor, str) or not anchor:
            if isinstance(evidence, (list, tuple)):
                anchor = "\n".join(_text(x) for x in evidence if _text(x))
            else:
                anchor = _text(evidence)
        candidate = {"event_id": _text(item["event_id"]),
                     "event_type": _text(item.get("event_type")) or "unknown",
                     "title": _text(item.get("title")),
                     "anchor_text": anchor}
        provenance = item.get("provenance")
        if isinstance(provenance, dict) and provenance:
            candidate["provenance"] = provenance
        return candidate

    def _context(self, record, context: list[dict]) -> list[SourceEnvelope]:
        """带角色的先前记录；只用于判断指代，不作为事实。"""
        envelopes = []
        for item in list(context)[-self.context_limit:] if self.context_limit else []:
            if not isinstance(item, dict):
                continue
            text = _text(item.get("text")).strip()
            if not text:
                continue
            role = _text(item.get("role")) or "unknown"
            seq = item.get("ordinal")
            envelopes.append(SourceEnvelope(
                scope_id=record.scope_id,
                source_seq=seq if isinstance(seq, int) and seq >= 0 else 0,
                source_hash=_text(item.get("content_hash")),
                recorded_at=_text(item.get("recorded_at")),
                text=role + ": " + text))
        return envelopes


def build_provider(kind: str, *, model: str = "", api_key: str = "",
                   base_url: str = "", timeout: int = 60):
    """按显式配置构造 provider；返回 ``None`` 表示没有判别器。

    没有 provider 时不应启动判别线程：调用方必须把「没有模型」与「模型说不知道」
    区分开，前者不会产生任何事件。

    Args:
        kind: ``none`` / ``mock`` / ``openai``（OpenAI 兼容的 chat/completions）。
        model: openai 模式必填的模型名。
        api_key: openai 模式必填的密钥。
        base_url: openai 模式必填的服务地址（不含 /chat/completions）。
        timeout: 单次请求超时秒数。

    Returns:
        provider 实例或 ``None``。

    Raises:
        ValueError: 未知类型或 openai 参数不完整。
    """
    name = (kind or "none").strip().lower()
    if name in ("", "none", "off"):
        return None
    if name == "mock":
        return MockProvider()
    if name == "openai":
        missing = [key for key, value in (("model", model), ("api_key", api_key),
                                          ("base_url", base_url)) if not value]
        if missing:
            raise ValueError("openai provider missing: " + ",".join(missing))
        return OpenAIProvider(base_url, model, api_key, timeout=int(timeout))
    raise ValueError("unknown judge provider: " + name)
