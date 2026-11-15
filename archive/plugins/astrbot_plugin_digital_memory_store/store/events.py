"""Store 的 events 领域方法（mixin，由 core.Store 组合）。"""
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

class EventMixin:
    @staticmethod
    def _character_pairs(text):
        return {text[i:i+2] for i in range(len(text)-1)
                if all('\u4e00'<=c<='\u9fff' for c in text[i:i+2]) and text[i:i+2] not in _GENERIC_GRAMS}

    def _event_by_title(self,scope,title):
        matches=[e for e in self.flow._events(scope) if e['title']==title]
        if len(matches)!=1:raise ValueError('event unavailable or ambiguous')
        snap=self.flow._snapshot(scope,matches[0]['id'])
        if not snap or snap['stale']:raise ValueError('event source unavailable')
        return matches[0]

    def event_alias(self,scope,title,alias,*,remove=False):
        if remove:
            matches=[e for e in self.flow._events(scope) if e['title']==title]
            if len(matches)!=1:raise ValueError('event unavailable or ambiguous')
            event=matches[0]
        else:event=self._event_by_title(scope,title)
        if (not isinstance(alias,str) or alias!=alias.strip() or not 2<=len(alias)<=30 or
            any(c in alias for c in '|\r\n\x00【】')):raise ValueError('invalid alias')
        if remove:
            with self.flow.db:
                result=self.flow.db.execute('DELETE FROM dl_event_alias WHERE scope=? AND event_id=? AND alias=?',(scope,event['id'],alias))
            return bool(result.rowcount)
        for other in self.flow._events(scope):
            if other['title']==alias and other['id']!=event['id']:raise ValueError('alias is another title')
        if alias==title:raise ValueError('alias already is event title')
        prior=self.flow.db.execute('SELECT event_id FROM dl_event_alias WHERE scope=? AND alias=?',(scope,alias)).fetchone()
        if prior:
            if prior['event_id']!=event['id']:raise ValueError('alias already in use')
            return False
        with self.flow.db:self.flow.db.execute('INSERT INTO dl_event_alias VALUES(?,?,?)',(scope,alias,event['id']))
        return True

    def event_priority(self,scope,title,level=None):
        """重要程度是操作者偏好，只用于排序，不表示事实可信度。"""
        event=self._event_by_title(scope,title)
        db=self.flow.db
        if level is not None:
            if level not in ('high','normal'):raise ValueError('invalid priority')
            with db:
                if level=='normal':db.execute('DELETE FROM dl_event_priority WHERE scope=? AND event_id=?',(scope,event['id']))
                else:db.execute('''INSERT OR REPLACE INTO dl_event_priority(scope,event_id,priority,updated_at)
                         VALUES(?,?,?,?)''',(scope,event['id'],level,datetime.now(timezone.utc).isoformat(timespec='seconds')))
        row=db.execute('SELECT priority FROM dl_event_priority WHERE scope=? AND event_id=?',(scope,event['id'])).fetchone()
        return row['priority'] if row else 'normal'

    def exclude_preference_rank(self,scope,title,excluded=True):
        """只控制规则加权，不删除原话，也不证明真实偏好。"""
        if type(excluded) is not bool:raise ValueError('invalid exclusion')
        event=self._event_by_title(scope,title)
        with self.flow.db:
            if excluded:
                self.flow.db.execute('INSERT OR IGNORE INTO dl_preference_exclusions VALUES(?,?,?)',
                    (scope,event['id'],datetime.now(timezone.utc).isoformat(timespec='seconds')))
            else:self.flow.db.execute('DELETE FROM dl_preference_exclusions WHERE scope=? AND event_id=?',(scope,event['id']))

    def _preference_signal(self,scope,snap):
        """只收集已关联原话中非常有限的自述线索；否定或修正时保守停用。"""
        rows=snap['rows']
        if not rows or len(rows)>1000:return []
        if self.flow.db.execute('SELECT 1 FROM dl_preference_exclusions WHERE scope=? AND event_id=?',
                                (scope,snap['id'])).fetchone():return []
        negative=re.compile(r'不(?:再|太|怎么|是)?(?:喜欢|爱|感兴趣)|不感兴趣|不再|不想|讨厌|厌倦|没兴趣|没有兴趣|放弃|戒掉|停止|取消|改成|改为')
        if any(negative.search(r['content']) for r in rows):return []
        relations=self.flow._event_rows(scope,snap['id'])[1]
        superseded={edge['older'] for edge in relations if edge['kind'] in ('corrects','supersedes','retracts')}
        positive=re.compile(r'(?:^|[。！？!?；;\n])\s*我(?:真的|很|特别|比较|一直|挺|还|最)?(?:喜欢|热爱|爱好|钟爱|对.{1,15}感兴趣)')
        days=set();texts=set();evidence=[]
        for row in rows:
            text=row['content']
            if row['id'] in superseded or any(x in text for x in '“”‘’「」『』"') or not positive.search(text):continue
            try:
                stamp=datetime.fromisoformat(row['recorded_at'])
                if stamp.utcoffset() is None:continue
                day=stamp.astimezone(timezone.utc).date().isoformat()
            except (TypeError,ValueError,OverflowError):continue
            if day in days or text in texts:continue
            days.add(day);texts.add(text)
            evidence.append({'id':row['id'],'content':text,'recorded_at':row['recorded_at']})
        return evidence

    def preference_clues(self,scope,title):
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        excluded=bool(self.flow.db.execute('SELECT 1 FROM dl_preference_exclusions WHERE scope=? AND event_id=?',
                                           (scope,event['id'])).fetchone())
        return {'excluded':excluded,'evidence':self._preference_signal(scope,snap)[:10]}

    def list_aliases(self,scope,title):
        event=self._event_by_title(scope,title)
        return [r['alias'] for r in self.flow.db.execute('SELECT alias FROM dl_event_alias WHERE scope=? AND event_id=? ORDER BY alias',(scope,event['id']))]

    def _named_events(self,scope,text,events):
        """Return all positive exact titles or explicitly configured aliases."""
        aliases={}
        for row in self.flow.db.execute('SELECT event_id,alias FROM dl_event_alias WHERE scope=?',(scope,)):
            aliases.setdefault(row['event_id'],[]).append(row['alias'])
        result=[]
        for event in events:
            for name in [event['title']]+aliases.get(event['id'],[]):
                if name in text and not re.search(r'(?:不是|并非|不指|别说|并不是)\s*'+re.escape(name),text):
                    result.append((event,'标题字面匹配' if name==event['title'] else '人工别名字面匹配'))
                    break
        return result

    def _alias_token(self,scope,event_id):
        names=[r['alias'] for r in self.flow.db.execute('SELECT alias FROM dl_event_alias WHERE scope=? AND event_id=? ORDER BY alias',(scope,event_id))]
        return hashlib.sha256(json.dumps(names,ensure_ascii=False).encode()).hexdigest()

    def _negated_events(self,scope,text,events):
        names=[(event['id'],event['title']) for event in events]
        names.extend((r['event_id'],r['alias']) for r in self.flow.db.execute('SELECT event_id,alias FROM dl_event_alias WHERE scope=?',(scope,)))
        return {eid for eid,name in names if re.search(r'(?:不是|并非|不指|别说|并不是)\s*'+re.escape(name),text)}
