"""独立的记忆基座输出协议及宿主适配。"""
from .contracts import OutputRequest, SourceSpan, MemoryItem, ContextOutput
from .compose import fit_output, render
from .astrbot import AstrBotOutputAdapter, DeliveryReceipt, OutputRejected

__all__ = ['OutputRequest', 'SourceSpan', 'MemoryItem', 'ContextOutput',
           'fit_output', 'render', 'AstrBotOutputAdapter', 'DeliveryReceipt', 'OutputRejected']
