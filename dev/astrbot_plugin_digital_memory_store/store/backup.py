"""Store 的 backup 领域方法（mixin，由 core.Store 组合）。"""
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
from .base import (MAX_CAPTURE_CHARS, _FOLLOWUP_UPDATE, _GENERIC_GRAMS, _RollbackBatch,
                   _UPDATE_CUES, atomic_write)

class BackupMixin:
    def verify_stored_backup(self,name):
        if not isinstance(name,str) or not re.fullmatch(r'memory_\d{8}T\d{6}_[0-9a-f]{8}\.sqlite3',name):
            raise ValueError('invalid backup name')
        path=self.root/'backups'/name
        if path.is_symlink() or not path.is_file():raise ValueError('backup missing')
        from ..recovery import verify_backup
        return verify_backup(path)

    def backup(self):
        folder=self.root/'backups';folder.mkdir(exist_ok=True)
        name='memory_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:8]+'.sqlite3'
        target=folder/name
        # 独占创建，失败时仅清除本次生成的未交付文件。
        with target.open('xb'):pass
        out=None
        try:
            # 显式关闭：`with sqlite3.connect()` 只提交，不释放句柄，Windows 下会锁住备份文件。
            out=sqlite3.connect(target)
            try:
                self.flow.db.backup(out)
                if out.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('backup integrity failed')
            finally:
                out.close()
            from ..recovery import verify_backup
            verification=verify_backup(target)
            if not verification['ok']:raise ValueError('backup verification failed: '+','.join(verification['errors']))
            # 备份产物只应是一个文件：校验用的只读连接会留下 -wal/-shm，
            # 它们不含未落盘数据，留在 backups 目录会被误当成备份或干扰轮转。
            for suffix in ('-wal','-shm','-journal'):
                Path(str(target)+suffix).unlink(missing_ok=True)
        except Exception:
            for suffix in ('','-wal','-shm','-journal'):
                Path(str(target)+suffix).unlink(missing_ok=True)
            raise
        return name
