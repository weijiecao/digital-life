"""Unified single-plugin configuration.

Precedence is explicit plugin config > compatibility environment > default.
The mode is independent from lifecycle: OFF / COLLECT / ASSIST.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

MODES = ("OFF", "COLLECT", "ASSIST")


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
    return default


def _as_int(value, default: int, low: int, high: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


def _allowed(value) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value]
    else:
        items = str(value or "").replace("，", ",").split(",")
    return tuple(dict.fromkeys(item for item in (part.strip() for part in items)
                               if item))


@dataclass(frozen=True)
class UnifiedConfig:
    mode: str = "COLLECT"
    allowed_senders: tuple[str, ...] = field(default_factory=tuple)
    judge_kind: str = "none"
    judge_provider_id: str = ""
    judge_source: str = "default"
    judge_policy: str = "native-input-0.2"
    judge_budget_chars: int = 6000
    judge_timeout_seconds: int = 20
    tick_seconds: int = 5
    retry_every: int = 12
    tick_limit: int = 10
    memory_budget_tokens: int = 512
    host_total_tokens: int = 8192
    host_total_explicit: bool = False
    console_read_only: bool = False
    output_mode: str = "off"

    @property
    def capture_enabled(self) -> bool:
        return self.mode != "OFF"

    @property
    def injection_enabled(self) -> bool:
        return self.mode == "ASSIST"


def resolve(config=None, environ=None) -> UnifiedConfig:
    """Build the effective configuration from plugin config and env.

    Args:
        config: AstrBot plugin configuration mapping (highest precedence).
        environ: Environment mapping; defaults to ``os.environ``.

    Returns:
        A validated immutable configuration.

    Raises:
        ValueError: For an unknown mode.
    """
    config = dict(config or {})
    environ = dict(os.environ if environ is None else environ)
    raw_mode = config.get("mode", environ.get("DIGITAL_LIFE_MODE", "COLLECT"))
    mode = str(raw_mode or "COLLECT").strip().upper()
    if mode not in MODES:
        raise ValueError("mode must be one of OFF/COLLECT/ASSIST")

    if "allowed_sender_ids" in config:
        allowed_value = config.get("allowed_sender_ids")
    else:
        allowed_value = environ.get("DIGITAL_LIFE_ALLOWED_SENDERS",
                                    environ.get("SHADOW_ALLOWED_SENDERS", ""))

    explicit_provider = "judge_provider_id" in config
    env_provider_id = str(environ.get("SHADOW_JUDGE_PROVIDER_ID", "") or "").strip()
    raw_provider = config.get("judge_provider_id")
    if explicit_provider and (raw_provider is None
                              or str(raw_provider).strip().lower() == "auto"):
        # The schema default is "auto": it may use compatibility env. Only an
        # explicit non-auto empty value is an instruction to disable.
        explicit_provider = False
    if explicit_provider:
        provider_id = str(config.get("judge_provider_id") or "").strip()
        judge_source = "plugin"
    else:
        provider_id = env_provider_id
        judge_source = ("legacy-env"
                        if provider_id or environ.get("SHADOW_JUDGE_PROVIDER")
                        else "default")
    env_kind = str(environ.get("SHADOW_JUDGE_PROVIDER", "none") or "none").strip().lower()
    if explicit_provider:
        # An explicit empty value is an instruction to disable the judge, not
        # permission to fall back to an old environment variable.
        judge_kind = "astrbot" if provider_id else "none"
    elif provider_id:
        judge_kind = "astrbot"
    elif env_kind in ("astrbot", "mock", "openai", "none"):
        judge_kind = env_kind
    else:
        judge_kind = "none"

    memory_budget = _as_int(
        config.get("memory_budget_tokens",
                   environ.get("SHADOW_MEMORY_BUDGET", 512)), 512, 0, 8192)
    judge_timeout = _as_int(
        config.get("judge_timeout_seconds",
                   environ.get("SHADOW_JUDGE_TIMEOUT", 20)), 20, 1, 120)
    tick_seconds = _as_int(
        config.get("tick_seconds", environ.get("SHADOW_TICK_SECONDS", 5)),
        5, 1, 3600)
    tick_limit = _as_int(
        config.get("tick_limit", environ.get("SHADOW_TICK_LIMIT", 10)),
        10, 1, 100)
    retry_every = _as_int(
        config.get("retry_every", environ.get("SHADOW_RETRY_EVERY", 12)),
        12, 0, 1000)
    judge_budget = _as_int(
        config.get("judge_budget_chars",
                   environ.get("SHADOW_JUDGE_BUDGET_CHARS", 6000)),
        6000, 500, 20000)
    host_total = 8192
    host_explicit = False
    if "host_total_tokens" in config:
        raw_host = config.get("host_total_tokens")
        try:
            candidate = int(raw_host)
        except (TypeError, ValueError):
            candidate = 0
        if candidate > 0:
            host_total = min(max(candidate, 512), 2000000)
            host_explicit = True
    elif str(environ.get("SHADOW_HOST_TOTAL_TOKENS", "")).strip():
        raw_host = environ.get("SHADOW_HOST_TOTAL_TOKENS")
        try:
            candidate = int(raw_host)
        except (TypeError, ValueError):
            candidate = 0
        if candidate > 0:
            host_total = min(max(candidate, 512), 2000000)
            host_explicit = True
    read_only = _as_bool(
        config.get("console_read_only",
                   environ.get("SHADOW_CONSOLE_READONLY", False)), False)
    policy = str(config.get("judge_policy",
                            environ.get("SHADOW_JUDGE_POLICY",
                                        "native-input-0.2")) or "native-input-0.2")
    return UnifiedConfig(
        mode=mode, allowed_senders=_allowed(allowed_value),
        judge_kind=judge_kind, judge_provider_id=provider_id,
        judge_source=judge_source,
        judge_policy=policy, judge_budget_chars=judge_budget,
        judge_timeout_seconds=judge_timeout, tick_seconds=tick_seconds,
        retry_every=retry_every, tick_limit=tick_limit,
        memory_budget_tokens=memory_budget, host_total_tokens=host_total,
        host_total_explicit=host_explicit,
        console_read_only=read_only,
        output_mode="inject" if mode == "ASSIST" else "off")
