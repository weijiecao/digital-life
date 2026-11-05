"""Store 的组合入口：把各领域 mixin 与核心生命周期拼成一个类。"""
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

from .capture import CaptureMixin
from .drafts import DraftMixin
from .events import EventMixin
from .organize import OrganizeMixin
from .relations import RelationMixin
from .briefs import BriefMixin
from .context import ContextMixin
from .backup import BackupMixin
from .embedding import EmbeddingMixin


class Store(CaptureMixin, DraftMixin, EventMixin, OrganizeMixin, RelationMixin, BriefMixin, ContextMixin, BackupMixin, EmbeddingMixin):
    def __init__(self, data_dir, model_dir, embedder_factory=CPUSentenceEmbedder):
        root=Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.root=root
        self.path=root/'manual_test_memory.sqlite3'
        self.flow=UnifiedMemoryEngine(self.path)
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_capture (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
            event_hash TEXT NOT NULL, content TEXT NOT NULL, content_sha TEXT NOT NULL,
            recorded_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
            linked_source TEXT, linked_title TEXT,
            UNIQUE(scope,event_hash))''')
        # 只记录已发现的漏采次数，不写入消息 ID 或正文。
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_capture_gaps (
            scope TEXT PRIMARY KEY, unresolved INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0, last_at TEXT NOT NULL)''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_import_provenance (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, source_label TEXT NOT NULL,
            original_timestamp TEXT NOT NULL, imported_at TEXT NOT NULL,
            PRIMARY KEY(scope,seq))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_preference_exclusions (
            scope TEXT NOT NULL, event_id TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(scope,event_id), FOREIGN KEY(scope,event_id) REFERENCES events(scope,id) ON DELETE CASCADE)''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_event_priority (
            scope TEXT NOT NULL, event_id TEXT NOT NULL, priority TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(scope,event_id),
            FOREIGN KEY(scope,event_id) REFERENCES events(scope,id) ON DELETE CASCADE)''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_new_group_drafts (
            scope TEXT NOT NULL, draft_id TEXT NOT NULL, manifest_json TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(scope,draft_id))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_new_group_events (
            scope TEXT NOT NULL, draft_id TEXT NOT NULL, title TEXT NOT NULL, event_id TEXT NOT NULL,
            PRIMARY KEY(scope,draft_id,title), UNIQUE(scope,event_id))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_group_drafts (
            scope TEXT NOT NULL, draft_id TEXT NOT NULL, manifest_json TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(scope,draft_id))''')
        group_columns={r['name'] for r in self.flow.db.execute('PRAGMA table_info(dl_group_drafts)')}
        if 'origin' not in group_columns:
            self.flow.db.execute("ALTER TABLE dl_group_drafts ADD COLUMN origin TEXT NOT NULL DEFAULT 'model'")
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_source_briefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, event_id TEXT NOT NULL,
            source_id TEXT NOT NULL, source_sha TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
            date_token TEXT NOT NULL, excerpt TEXT NOT NULL, brief TEXT NOT NULL, created_at TEXT NOT NULL)''')
        self.flow.db.execute('CREATE INDEX IF NOT EXISTS dl_source_brief_lookup ON dl_source_briefs(scope,event_id,id DESC)')
        # 旧数据库没有摘要生成方式；只对能关联到模型草稿的记录回填，其余保留未知。
        columns={r['name'] for r in self.flow.db.execute('PRAGMA table_info(dl_source_briefs)')}
        if 'origin' not in columns:
            self.flow.db.execute("ALTER TABLE dl_source_briefs ADD COLUMN origin TEXT NOT NULL DEFAULT 'legacy_unknown'")
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_brief_model_drafts (
            scope TEXT NOT NULL, draft_id TEXT NOT NULL, snapshot_json TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at TEXT NOT NULL, accepted_brief_id INTEGER,
            PRIMARY KEY(scope,draft_id))''')
        self.flow.db.execute('''UPDATE dl_source_briefs SET origin='model_reviewed'
            WHERE origin='legacy_unknown' AND EXISTS (SELECT 1 FROM dl_brief_model_drafts d
            WHERE d.scope=dl_source_briefs.scope AND d.accepted_brief_id=dl_source_briefs.id)''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_brief_segment_results (
            scope TEXT NOT NULL, draft_id TEXT NOT NULL, part_no INTEGER NOT NULL, result_json TEXT NOT NULL,
            PRIMARY KEY(scope,draft_id,part_no))''')
        columns={r['name'] for r in self.flow.db.execute('PRAGMA table_info(dl_brief_segment_results)')}
        if 'cache_reused' not in columns:
            self.flow.db.execute('ALTER TABLE dl_brief_segment_results ADD COLUMN cache_reused INTEGER NOT NULL DEFAULT 0')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_segment_cache (
            scope TEXT NOT NULL, cache_key TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(scope,cache_key))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_update_dismissals (
            scope TEXT NOT NULL, message_id TEXT NOT NULL, event_id TEXT NOT NULL,
            source_sha TEXT NOT NULL, recorded_at TEXT NOT NULL, reviewed_at TEXT NOT NULL,
            PRIMARY KEY(scope,message_id))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_suggestions (
            scope TEXT NOT NULL, capture_seq INTEGER NOT NULL, event_id TEXT NOT NULL,
            capture_sha TEXT NOT NULL, event_fingerprint TEXT NOT NULL,
            method TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(scope,capture_seq,event_id))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_new_title_suggestions (
            scope TEXT NOT NULL, capture_seq INTEGER NOT NULL, capture_sha TEXT NOT NULL,
            title TEXT NOT NULL, method TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(scope,capture_seq))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_extract_drafts (
            scope TEXT NOT NULL, draft_id TEXT NOT NULL, event_id TEXT NOT NULL,
            event_fingerprint TEXT NOT NULL, records_json TEXT NOT NULL,
            source_total INTEGER NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(scope,draft_id))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_model_drafts (
            scope TEXT NOT NULL, draft_id TEXT NOT NULL, source_manifest TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(scope,draft_id))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_contrast_drafts (
            scope TEXT NOT NULL, draft_id TEXT NOT NULL, pending_seq INTEGER NOT NULL,
            event_title TEXT NOT NULL, snapshot_json TEXT NOT NULL, result_json TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(scope,draft_id))''')
        contrast_columns={r['name'] for r in self.flow.db.execute('PRAGMA table_info(dl_contrast_drafts)')}
        if 'origin' not in contrast_columns:
            self.flow.db.execute("ALTER TABLE dl_contrast_drafts ADD COLUMN origin TEXT NOT NULL DEFAULT 'model'")
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_date_annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, message_id TEXT NOT NULL,
            occurred_date TEXT, source_sha TEXT NOT NULL, annotated_at TEXT NOT NULL,
            FOREIGN KEY(scope,message_id) REFERENCES messages(scope,id) ON DELETE CASCADE)''')
        self.flow.db.execute('CREATE INDEX IF NOT EXISTS dl_date_by_message ON dl_date_annotations(scope,message_id,id DESC)')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_event_alias (
            scope TEXT NOT NULL, alias TEXT NOT NULL, event_id TEXT NOT NULL,
            PRIMARY KEY(scope,alias), FOREIGN KEY(scope,event_id) REFERENCES events(scope,id) ON DELETE CASCADE)''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_localization (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, source_sha TEXT NOT NULL,
            state TEXT NOT NULL, candidates_json TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(scope,seq))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_jobs (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(scope,seq))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_groups (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, peer_seq INTEGER NOT NULL,
            source_sha TEXT NOT NULL, peer_sha TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(scope,seq))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_chains (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, anchor_seq INTEGER NOT NULL,
            manifest_json TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(scope,seq))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_model_jobs (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0, policy TEXT NOT NULL DEFAULT '', PRIMARY KEY(scope,seq))''')
        model_job_columns={r['name'] for r in self.flow.db.execute('PRAGMA table_info(dl_auto_model_jobs)')}
        if 'policy' not in model_job_columns:
            self.flow.db.execute("ALTER TABLE dl_auto_model_jobs ADD COLUMN policy TEXT NOT NULL DEFAULT ''")
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_model_pairs (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, peer_seq INTEGER NOT NULL,
            manifest_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(scope,seq))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_relation_hints (
            scope TEXT NOT NULL, newer_seq INTEGER NOT NULL, older_seq INTEGER NOT NULL,
            kind TEXT NOT NULL, manifest_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(scope,newer_seq,older_seq))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_hint_rechecks (
            scope TEXT NOT NULL, model_key TEXT NOT NULL, newer_seq INTEGER NOT NULL,
            older_seq INTEGER NOT NULL, PRIMARY KEY(scope,model_key,newer_seq,older_seq))''')
        self.flow.db.execute('CREATE INDEX IF NOT EXISTS dl_auto_hint_rechecks_older ON dl_auto_hint_rechecks(scope,model_key,older_seq)')
        self.flow.db.execute('CREATE INDEX IF NOT EXISTS dl_auto_relation_hints_older ON dl_auto_relation_hints(scope,older_seq)')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_separations (
            scope TEXT NOT NULL, newer_seq INTEGER NOT NULL, older_seq INTEGER NOT NULL,
            manifest_json TEXT NOT NULL, created_at TEXT NOT NULL, verdict_version INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(scope,newer_seq,older_seq))''')
        separation_columns={r['name'] for r in self.flow.db.execute('PRAGMA table_info(dl_auto_separations)')}
        if 'verdict_version' not in separation_columns:
            self.flow.db.execute('ALTER TABLE dl_auto_separations ADD COLUMN verdict_version INTEGER NOT NULL DEFAULT 0')
        self.flow.db.execute('CREATE INDEX IF NOT EXISTS dl_auto_separations_older ON dl_auto_separations(scope,older_seq,verdict_version,newer_seq)')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_model_limit (
            id INTEGER PRIMARY KEY CHECK(id=1), next_at TEXT NOT NULL)''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_source_vectors (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, model_key TEXT NOT NULL,
            content_sha TEXT NOT NULL, vector_json TEXT NOT NULL,
            PRIMARY KEY(scope,seq,model_key))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_text_index (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, content_sha TEXT NOT NULL, is_change INTEGER NOT NULL,
            PRIMARY KEY(scope,seq))''')
        self.flow.db.execute('CREATE INDEX IF NOT EXISTS dl_auto_text_change_lookup ON dl_auto_text_index(scope,is_change,seq)')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_text_grams (
            scope TEXT NOT NULL, gram TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY(scope,gram,seq))''')
        self.flow.db.execute('CREATE INDEX IF NOT EXISTS dl_auto_text_grams_source ON dl_auto_text_grams(scope,seq)')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_text_backfill (
            id INTEGER PRIMARY KEY CHECK(id=1), last_seq INTEGER NOT NULL)''')
        self.flow.db.execute('INSERT OR IGNORE INTO dl_auto_text_backfill VALUES(1,0)')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_text_dirty (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY(scope,seq))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_vector_failures (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, model_key TEXT NOT NULL,
            content_sha TEXT NOT NULL, attempts INTEGER NOT NULL, next_at TEXT NOT NULL,
            PRIMARY KEY(scope,seq,model_key))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_vector_chunks (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, model_key TEXT NOT NULL,
            content_sha TEXT NOT NULL, part_count INTEGER NOT NULL,
            PRIMARY KEY(scope,seq,model_key))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_topics (
            scope TEXT NOT NULL, topic_id TEXT NOT NULL, model_key TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(scope,topic_id))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_topic_members (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, model_key TEXT NOT NULL,
            topic_id TEXT NOT NULL, content_sha TEXT NOT NULL, recorded_at TEXT NOT NULL,
            basis TEXT NOT NULL, PRIMARY KEY(scope,seq,model_key))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_topic_moves (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, model_key TEXT NOT NULL,
            evidence_json TEXT NOT NULL, moves_json TEXT NOT NULL,
            state TEXT NOT NULL, created_at TEXT NOT NULL)''')
        self.flow.db.execute('CREATE INDEX IF NOT EXISTS dl_auto_topic_moves_pending ON dl_auto_topic_moves(state,scope)')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_move_topics (
            move_id TEXT NOT NULL, scope TEXT NOT NULL, topic_id TEXT NOT NULL,
            PRIMARY KEY(move_id,topic_id))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_move_dependents (
            move_id TEXT NOT NULL, scope TEXT NOT NULL, seq INTEGER NOT NULL, model_key TEXT NOT NULL,
            topic_id TEXT NOT NULL, content_sha TEXT NOT NULL, recorded_at TEXT NOT NULL,
            PRIMARY KEY(move_id,scope,seq,model_key))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_topology_cursor (
            model_key TEXT PRIMARY KEY, last_id TEXT NOT NULL)''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_rebuild_cursor (
            model_key TEXT PRIMARY KEY, last_rowid INTEGER NOT NULL)''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_reconcile_cursor (
            model_key TEXT PRIMARY KEY, last_seq INTEGER NOT NULL, last_scope TEXT NOT NULL)''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_hint_cursor (
            model_key TEXT PRIMARY KEY, last_rowid INTEGER NOT NULL)''')
        self.flow.db.execute('CREATE INDEX IF NOT EXISTS dl_auto_topic_members_topic ON dl_auto_topic_members(scope,topic_id,seq)')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_topic_skips (
            scope TEXT NOT NULL, seq INTEGER NOT NULL, model_key TEXT NOT NULL,
            PRIMARY KEY(scope,seq,model_key))''')
        self.flow.db.execute('''CREATE TABLE IF NOT EXISTS dl_auto_schema_migrations (
            migration TEXT PRIMARY KEY, applied_at TEXT NOT NULL)''')
        migration=self.flow.db.execute("INSERT OR IGNORE INTO dl_auto_schema_migrations VALUES('model_separation_tristate_v1',?)",
                                       (datetime.now(timezone.utc).isoformat(timespec='seconds'),))
        if migration.rowcount:
            # 旧版 false 包含“不确定”，无法作为拆分证据；让受影响的成员重新整理。
            self.flow.db.execute("DELETE FROM dl_auto_topic_members WHERE basis='模型推测需分开'")
            self.flow.db.execute('DELETE FROM dl_auto_topic_skips')
        migration=self.flow.db.execute("INSERT OR IGNORE INTO dl_auto_schema_migrations VALUES('topic_candidate_limit_5000_v1',?)",
                                       (datetime.now(timezone.utc).isoformat(timespec='seconds'),))
        if migration.rowcount:self.flow.db.execute('DELETE FROM dl_auto_topic_skips')
        migration=self.flow.db.execute("INSERT OR IGNORE INTO dl_auto_schema_migrations VALUES('topic_candidate_stream_v1',?)",
                                       (datetime.now(timezone.utc).isoformat(timespec='seconds'),))
        if migration.rowcount:self.flow.db.execute('DELETE FROM dl_auto_topic_skips')
        migration=self.flow.db.execute("INSERT OR IGNORE INTO dl_auto_schema_migrations VALUES('topic_retry_changed_clues_v1',?)",
                                       (datetime.now(timezone.utc).isoformat(timespec='seconds'),))
        if migration.rowcount:self.flow.db.execute('DELETE FROM dl_auto_topic_skips')
        migration=self.flow.db.execute("INSERT OR IGNORE INTO dl_auto_schema_migrations VALUES('retry_indexed_update_jobs_v1',?)",
                                       (datetime.now(timezone.utc).isoformat(timespec='seconds'),))
        if migration.rowcount:
            words=('改成','改为','改到','调整为','推迟','提前','取消','不去了','不去','换成','更正','说错','纠正','不再')
            condition=' OR '.join('instr(c.content,?)>0' for _ in words)
            self.flow.db.execute('''UPDATE dl_auto_model_jobs SET status='pending'
                WHERE status='skipped' AND attempts<3 AND EXISTS (
                    SELECT 1 FROM dl_capture c JOIN dl_auto_topic_members m ON m.scope=c.scope AND m.seq=c.seq
                    WHERE c.scope=dl_auto_model_jobs.scope AND c.seq=dl_auto_model_jobs.seq
                    AND c.state='pending' AND ('''+condition+'))',words)
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_topic_capture_insert
            AFTER INSERT ON dl_capture BEGIN DELETE FROM dl_auto_topic_skips WHERE scope=NEW.scope; END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_text_capture_update
            AFTER UPDATE OF content,content_sha,state,scope,seq ON dl_capture BEGIN
            DELETE FROM dl_auto_text_index WHERE scope=OLD.scope AND seq=OLD.seq;
            DELETE FROM dl_auto_text_grams WHERE scope=OLD.scope AND seq=OLD.seq; END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_text_capture_delete
            AFTER DELETE ON dl_capture BEGIN
            DELETE FROM dl_auto_text_index WHERE scope=OLD.scope AND seq=OLD.seq;
            DELETE FROM dl_auto_text_grams WHERE scope=OLD.scope AND seq=OLD.seq; END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_text_dirty_insert
            AFTER INSERT ON dl_capture BEGIN
            INSERT OR REPLACE INTO dl_auto_text_dirty VALUES(NEW.scope,NEW.seq); END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_text_dirty_update
            AFTER UPDATE OF content,content_sha,state,scope,seq ON dl_capture BEGIN
            DELETE FROM dl_auto_text_dirty WHERE scope=OLD.scope AND seq=OLD.seq;
            INSERT OR REPLACE INTO dl_auto_text_dirty VALUES(NEW.scope,NEW.seq); END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_text_dirty_delete
            AFTER DELETE ON dl_capture BEGIN DELETE FROM dl_auto_text_dirty WHERE scope=OLD.scope AND seq=OLD.seq; END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_topic_capture_update
            AFTER UPDATE OF content,content_sha,state ON dl_capture BEGIN
            DELETE FROM dl_auto_topic_members WHERE scope=OLD.scope AND seq=OLD.seq;
            DELETE FROM dl_auto_topic_skips WHERE scope=OLD.scope; END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_topic_capture_delete
            AFTER DELETE ON dl_capture BEGIN
            DELETE FROM dl_auto_topic_members WHERE scope=OLD.scope AND seq=OLD.seq;
            DELETE FROM dl_auto_topic_skips WHERE scope=OLD.scope; END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_source_reorganize_update
            AFTER UPDATE OF content,content_sha,recorded_at,state ON dl_capture WHEN NEW.state='pending' BEGIN
            INSERT INTO dl_auto_jobs(scope,seq,status,attempts,next_at) VALUES(NEW.scope,NEW.seq,'pending',0,'')
            ON CONFLICT(scope,seq) DO UPDATE SET status='pending',attempts=0,next_at='';
            DELETE FROM dl_auto_topic_members WHERE scope=OLD.scope AND seq=OLD.seq;
            DELETE FROM dl_auto_topic_skips WHERE scope=OLD.scope; END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_vector_capture_delete
            AFTER DELETE ON dl_capture BEGIN
            DELETE FROM dl_auto_source_vectors WHERE scope=OLD.scope AND seq=OLD.seq;
            DELETE FROM dl_auto_vector_failures WHERE scope=OLD.scope AND seq=OLD.seq;
            DELETE FROM dl_auto_topic_members WHERE scope=OLD.scope AND seq=OLD.seq;
            DELETE FROM dl_auto_topic_skips WHERE scope=OLD.scope; END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_vector_capture_update
            AFTER UPDATE OF content,content_sha,state ON dl_capture BEGIN
            DELETE FROM dl_auto_source_vectors WHERE scope=OLD.scope AND seq=OLD.seq;
            DELETE FROM dl_auto_vector_failures WHERE scope=OLD.scope AND seq=OLD.seq;
            DELETE FROM dl_auto_topic_members WHERE scope=OLD.scope AND seq=OLD.seq;
            DELETE FROM dl_auto_topic_skips WHERE scope=OLD.scope; END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_vector_chunks_update
            AFTER UPDATE OF content,content_sha,state ON dl_capture BEGIN
            DELETE FROM dl_auto_vector_chunks WHERE scope=OLD.scope AND seq=OLD.seq; END''')
        self.flow.db.execute('''CREATE TRIGGER IF NOT EXISTS dl_auto_vector_chunks_delete
            AFTER DELETE ON dl_capture BEGIN
            DELETE FROM dl_auto_vector_chunks WHERE scope=OLD.scope AND seq=OLD.seq; END''')
        self.flow.db.execute('CREATE INDEX IF NOT EXISTS dl_auto_job_ready ON dl_auto_jobs(status,next_at,seq)')
        self.flow.db.execute('CREATE INDEX IF NOT EXISTS dl_auto_job_scope_order ON dl_auto_jobs(scope,status,seq)')
        self.flow.db.commit()
        self.embedder=None
        self.index=None
        self.model_dir=model_dir
        self.embedder_factory=embedder_factory
        # 模型加载很慢（实测 11–13 s）；放到独立线程预热，避免占住采集/整理用的单线程池。
        self.embedder_state='idle'  # idle / loading / ready / failed
        self._prewarm_thread=None
        self.weak_association_enabled=False
        self.association_context_enabled=False
        self.semantic_source_context_enabled=False
        # 每轮上下文供给：级联检索无结果时仍给一个有界背景包（默认由配置开启）。
        self.always_context_enabled=False
        # 原话级语义门槛：自造语料实测 0.75→0.60 使 R@1 41→64/100 且误注入仍 0/20；
        # 0.55 可达 71/100，作为可配置实验值。真实语料仍待复核。
        self.source_min_cosine=0.60
        self.source_min_lead=0.10
        # 仅内存保存源向量，受作用域、模型、原话 ID 与内容哈希约束。
        self.semantic_source_vectors=OrderedDict()
        self.multi_event_context_enabled=False
        self.pending_context_enabled=False
        self.automatic_memory_enabled=False
        self.auto_model_enabled=False
        self.auto_model_policy=''
        self.auto_group_context_enabled=False
        self.auto_group_semantic_enabled=False
        self.detail_priority_enabled=False
        self.compact_context_enabled=False
        self.event_priority_enabled=False
        self.recency_rank_enabled=False
        self.preference_rank_enabled=False
        self.occurred_date_context_enabled=False
        self.reviewed_brief_context_enabled=False
        self.brief_overview_priority_enabled=False
        self.lexical_index=LexicalIndex(self.flow,self._character_pairs)
        self.pending_index=PendingIndex(self.flow.db,self._character_pairs)

    @atomic_write
    def save(self, scope, title, text):
        if not 1<=len(title)<=80 or not 1<=len(text)<=400 or any(c in title for c in '\n\r【】'):
            raise ValueError('invalid title or text')
        matches=self.flow.writer._event_by_title(scope,title)
        if len(matches)>1: raise ValueError('ambiguous title')
        event_id=matches[0]['id'] if matches else self.flow.core.create_event(scope,title)
        source='explicit-manual:'+str(uuid.uuid4())
        r=self.flow.ingest(scope=scope,origin=source,speaker='user',kind='user_message',
            content=text,recorded_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        self.flow.review(scope=scope,origin=source,decision='approve',reviewer='explicit-user-command',
            event_id=event_id,phase='unknown',reason='operator explicitly provided event title and source text')
        return r['message_id']

    def search(self,scope,query):
        if not 1<=len(query)<=400: raise ValueError('invalid query')
        idx=self._index()
        ids=idx.search(scope,query)
        titles={e['id']:e['title'] for e in self.flow._events(scope)}
        return [titles[x] for x in ids if x in titles]

    def show(self,scope,title):
        rows=self.flow.writer._event_by_title(scope,title)
        if len(rows)!=1: return []
        snap=self.flow._snapshot(scope,rows[0]['id'])
        if not snap or snap['stale']: return []
        relations=self.flow.db.execute('''SELECT newer,older,kind FROM relations WHERE scope=?
            AND newer IN (SELECT message_id FROM e417_phase WHERE scope=? AND event_id=?)
            AND older IN (SELECT message_id FROM e417_phase WHERE scope=? AND event_id=?)''',
            (scope,scope,rows[0]['id'],scope,rows[0]['id'])).fetchall()
        imported={r['linked_source'] for r in self.flow.db.execute('''SELECT c.linked_source FROM dl_capture c
                    JOIN dl_import_provenance p ON p.scope=c.scope AND p.seq=c.seq
                    WHERE c.scope=? AND c.linked_source IS NOT NULL''',(scope,))}
        return [{'id':x['id'],'content':x['content'],'recorded_at':x['recorded_at'],'imported':x['id'] in imported,
                 'relations':[dict(r) for r in relations if r['newer']==x['id'] or r['older']==x['id']]}
                for x in snap['rows']]

    def event_catalog(self,scope,page=1):
        if type(page) is not int or not 1<=page<=10000:raise ValueError('invalid page')
        events=self.flow._events(scope);selected=events[(page-1)*10:page*10];items=[]
        for event in selected:
            snap=self.flow._snapshot(scope,event['id'])
            if snap:
                items.append({'title':event['title'],'records':len(snap['rows']),
                              'last_recorded_at':snap['rows'][-1]['recorded_at'],'stale':snap['stale']})
        return {'events':items,'has_more':len(events)>page*10}

    def _approved_source(self,scope,message_id):
        if not isinstance(message_id,str) or not 1<=len(message_id)<=128:raise ValueError('invalid source')
        row=self.flow.db.execute('''SELECT m.content,i.source_sha256 FROM messages m
            JOIN e417_ingest i ON i.scope=m.scope AND i.message_id=m.id
            WHERE m.scope=? AND m.id=? AND m.speaker='user' AND i.state='approved_link' ''',(scope,message_id)).fetchone()
        if not row or hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['source_sha256']:
            raise ValueError('source missing or altered')
        return row

    def close(self):
        self.semantic_source_vectors.clear()
        self.flow.close()
