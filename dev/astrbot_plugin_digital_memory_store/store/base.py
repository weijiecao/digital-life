"""Store 的共享常量、批量回滚异常与写锁装饰器（不依赖其它 store 模块）。"""
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import hashlib
import sqlite3
import threading
import uuid
import json
import re
import base64
import math
from collections import Counter, OrderedDict
from functools import wraps
from ..engine.e418.engine import UnifiedMemoryEngine, _fingerprint
from ..engine.e419.dynamic_index import attach_dynamic_index
from ..engine.e419.dynamic_index import _unit
from ..engine.e32.cpu_model import CPUSentenceEmbedder
from ..model_drafts import validate_output,validate_contrast,validate_brief_output,make_brief_prompt
from ..model_groups import validate_group_output
from ..new_groups import validate_new_groups
from ..lexical_index import LexicalIndex
from ..pending_index import PendingIndex

# These are deliberately broad signals, not semantic conflict classification.
_UPDATE_CUES=re.compile(r'改成|改为|改到|改去|改铺|改期|改签|挪到|挪至|换到|调到|降到|延期|延后|调整为|推迟|提前|取消|不去了|不去|不做了|不办了|不打了|不弄了|去不了|先不|算了|换成|更正|说错|纠正|现在不|不再')
_FOLLOWUP_UPDATE=re.compile(r'^(?:那件事|这件事|那个计划|这个计划|那次|原计划)?(?:改成|改为|改到|改去|改铺|改期|改签|挪到|挪至|换到|调到|降到|延期|延后|调整为|推迟|提前|取消|不去了|不去|不做了|不办了|去不了|换成|更正|说错|纠正|现在不|不再)')
_GENERIC_GRAMS={'计划','准备','打算','现在','之后','后来','之前','那个','这个','什么','怎么','事情','已经','我们','你说','想要','一下','觉得'}
MAX_CAPTURE_CHARS=4000

class _RollbackBatch(Exception):
    def __init__(self,result):self.result=result

def atomic_write(method):
    """允许外层批次接管提交；独立调用则在写锁内统一提交或回滚。"""
    @wraps(method)
    def wrapper(self,*args,**kwargs):
        core=self.flow.core;db=self.flow.db
        def run():
            result=method(self,*args,**kwargs)
            if isinstance(result,dict) and result.get('failed') is not None:
                raise _RollbackBatch(result)
            return result
        if core._deferred_writes:return run()
        db.execute('BEGIN IMMEDIATE')
        try:
            with core.deferred_writes():result=run()
            db.commit();return result
        except _RollbackBatch as exc:
            db.rollback();result=dict(exc.result);result['completed']=[]
            if 'remaining' in result:
                selected=args[1] if len(args)>1 else kwargs['seqs']
                result['remaining']=sorted(selected)
            return result
        except BaseException:
            db.rollback();raise
    return wrapper
