"""AstrBot 临时请求块适配；不修改 system_prompt、prompt 或历史。"""
from dataclasses import dataclass
from .contracts import SCHEMA_VERSION
from .compose import render

_STATE = '_digital_life_memory_output_v1'
STATE_ATTR = _STATE


def own_output_part(host_request):
    """本插件上一次安装的临时内容块；预算复算时排除，避免把自己算两遍。"""
    binding = getattr(host_request, STATE_ATTR, None)
    return getattr(binding, 'part', None)


class OutputRejected(ValueError):
    pass


@dataclass(frozen=True)
class DeliveryReceipt:
    status: str
    request_id: str
    output_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _Binding:
    part: object
    output_id: str


def _text_part(text):
    # 仅实际请求适配时导入 AstrBot；标准输出与离线安装无需该依赖。
    from astrbot.core.agent.message import TextPart
    return TextPart(text=text)


class AstrBotOutputAdapter:
    def __init__(self, part_factory=None):
        self.part_factory = part_factory or _text_part

    def clear(self, host_request):
        binding = getattr(host_request, _STATE, None)
        if not isinstance(binding, _Binding):
            return
        parts = getattr(host_request, 'extra_user_content_parts', None)
        if isinstance(parts, list):
            parts[:] = [part for part in parts if part is not binding.part]
        delattr(host_request, _STATE)

    def apply(self, host_request, output, expected, validate):
        """validate 由记忆引擎提供，必须检查当前证据与请求绑定。"""
        try:
            if output.schema_version != SCHEMA_VERSION or output.persist is not False:
                raise OutputRejected('unsupported_output_contract')
            if output.request != expected:
                raise OutputRejected('request_mismatch')
            parts = getattr(host_request, 'extra_user_content_parts', None)
            if not isinstance(parts, list) or not hasattr(host_request, '__dict__'):
                raise OutputRejected('unsupported_host_request')
            if getattr(host_request, 'prompt', None) != expected.query:
                raise OutputRejected('host_query_changed')
            if output.text != render(output.items):
                raise OutputRejected('render_mismatch')
            if validate(output, expected) is not True:
                raise OutputRejected('stale_or_invalid_output')
            if not output.text:
                self.clear(host_request)
                return DeliveryReceipt('EMPTY', expected.request_id, output.output_id)
            old = getattr(host_request, _STATE, None)
            if (isinstance(old, _Binding) and old.output_id == output.output_id
                    and any(p is old.part for p in parts)
                    and getattr(old.part, 'text', None) == output.text
                    and getattr(old.part, '_no_save', False) is True):
                return DeliveryReceipt('UNCHANGED', expected.request_id, output.output_id)
            part = self.part_factory(output.text)
            if not callable(getattr(part, 'mark_as_temp', None)):
                raise OutputRejected('temporary_content_unsupported')
            part = part.mark_as_temp()
            if (getattr(part, '_no_save', False) is not True
                    or getattr(part, 'text', None) != output.text):
                raise OutputRejected('temporary_content_invalid')
            # 工厂执行后再次验证，避免在准备内容块时证据已经失效。
            if validate(output, expected) is not True:
                raise OutputRejected('stale_or_invalid_output')
            updated = [p for p in parts if not isinstance(old, _Binding) or p is not old.part]
            updated.append(part)
            setattr(host_request, _STATE, _Binding(part, output.output_id))
            parts[:] = updated
            return DeliveryReceipt('INJECTED', expected.request_id, output.output_id)
        except Exception as exc:
            # 同一请求重试失败时，移除自己之前写入的旧记忆，保留其他插件内容。
            self.clear(host_request)
            reason = str(exc) if isinstance(exc, OutputRejected) else 'adapter_error'
            return DeliveryReceipt('REJECTED', expected.request_id, reason=reason)
