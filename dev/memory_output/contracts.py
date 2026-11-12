"""与宿主、数据库无关的记忆输出协议。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

SCHEMA_VERSION = 'memory-output-v1'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class OutputRequest:
    request_id: str
    scope_id: str
    query: str
    budget_tokens: int = 1024

    def __post_init__(self):
        for name in ('request_id', 'scope_id', 'query'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError('required output field: ' + name)
        if type(self.budget_tokens) is not int or self.budget_tokens < 0:
            raise ValueError('budget_tokens must be a nonnegative integer')


@dataclass(frozen=True)
class SourceSpan:
    source_seq: int
    source_hash: str
    start: int
    end: int
    text: str
    recorded_at: str | None = None


@dataclass(frozen=True)
class MemoryItem:
    event_id: str
    revision: int
    snapshot_hash: str
    provenance: tuple[tuple[str, str | None], ...]
    spans: tuple[SourceSpan, ...]
    certificate_json: str


@dataclass(frozen=True)
class ContextOutput:
    request: OutputRequest
    scope_epoch: int
    items: tuple[MemoryItem, ...] = ()
    text: str = ''
    used_tokens: int = 0
    budget_tokens: int = 0
    tokenizer: str | None = None
    reasons: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION
    persist: bool = False

    def as_dict(self):
        # 返回独立 JSON 结构；调用方修改该字典不会改变已准备的输出。
        return json.loads(self.to_json())

    def to_json(self):
        return canonical(asdict(self))

    @property
    def output_id(self):
        return digest(self.to_json())
