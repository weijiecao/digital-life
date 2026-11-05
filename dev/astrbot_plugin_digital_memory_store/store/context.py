"""Store 的 context 领域：由三个子 mixin 组合（事件供给 / 上下文包 / 追问）。"""
from .context_sources import ContextSourceMixin
from .context_packet import ContextPacketMixin
from .context_followup import ContextFollowupMixin


class ContextMixin(ContextSourceMixin, ContextPacketMixin, ContextFollowupMixin):
    """上下文相关的全部方法；具体实现见三个子 mixin。"""
    pass
