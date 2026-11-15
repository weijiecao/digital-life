"""Store 的 embedding 领域方法（mixin，由 core.Store 组合）。"""
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

class EmbeddingMixin:
    @property
    def embedder_ready(self):
        return self.embedder is not None and self.embedder_state=='ready'

    def prewarm_embedder(self):
        """在后台线程加载模型，返回当前状态（idle/loading/ready/failed）。

        只有 model_dir 是真实目录时才预热；测试用的替身路径（如 'fixture'）保持原懒加载行为，
        以免改变既有用例语义。加载失败只记录状态，不影响原话采集与整理。
        """
        if self.embedder_state in ('loading','ready'):return self.embedder_state
        if not self.model_dir or not Path(self.model_dir).is_dir():return self.embedder_state
        self.embedder_state='loading'
        def load():
            try:encoder=self.embedder_factory(self.model_dir)
            except Exception:
                self.embedder_state='failed'
                return
            self.embedder=encoder
            self.embedder_state='ready'
        self._prewarm_thread=threading.Thread(target=load,name='dlm-embedder-prewarm',daemon=True)
        self._prewarm_thread.start()
        return 'loading'

    def _get_embedder(self):
        if self.embedder is not None:return self.embedder
        # 预热进行中：快速失败，让语义路径本轮降级为字面，而不是把单线程池堵十几秒。
        if self.embedder_state=='loading':raise ValueError('embedder_loading')
        encoder=self.embedder_factory(self.model_dir)
        self.embedder=encoder
        self.embedder_state='ready'
        return encoder

    def _index(self):
        if self.index is None:
            if not self.model_dir or not str(self.model_dir).strip():
                raise ValueError('no_model_path')
            encoder=self._get_embedder()
            self.index=attach_dynamic_index(self.flow, encoder, min_cosine=0.55, limit=3)
        return self.index
