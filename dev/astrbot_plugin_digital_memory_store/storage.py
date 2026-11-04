"""Independent explicitly operated E5.0-F memory store; not an automatic listener."""
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
from .engine.e418.engine import UnifiedMemoryEngine, _fingerprint
from .engine.e419.dynamic_index import attach_dynamic_index
from .engine.e419.dynamic_index import _unit
from .engine.e32.cpu_model import CPUSentenceEmbedder
from .model_drafts import validate_output,validate_contrast,validate_brief_output,make_brief_prompt
from .model_groups import validate_group_output
from .new_groups import validate_new_groups
from .lexical_index import LexicalIndex
from .pending_index import PendingIndex

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

class Store:
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

    def scope(self, sender, origin):
        data=(str(len(sender))+':'+sender+origin).encode('utf-8')
        return 'manual-test:'+hashlib.sha256(data).hexdigest()

    def record_capture_gap(self,scope,detected_at=None):
        with self.flow.db:
            self.flow.db.execute('''INSERT INTO dl_capture_gaps(scope,unresolved,total,last_at) VALUES(?,1,1,?)
                ON CONFLICT(scope) DO UPDATE SET unresolved=unresolved+1,total=total+1,
                last_at=MAX(last_at,excluded.last_at)''',
                (scope,detected_at or datetime.now(timezone.utc).isoformat(timespec='seconds')))

    def capture_gap_boundary(self,scope):
        row=self.flow.db.execute('SELECT total,last_at FROM dl_capture_gaps WHERE scope=?',(scope,)).fetchone()
        return row['last_at'] if row and row['total'] else None

    def capture_gap_count(self,scope):
        row=self.flow.db.execute('SELECT unresolved FROM dl_capture_gaps WHERE scope=?',(scope,)).fetchone()
        return row['unresolved'] if row else 0

    def acknowledge_capture_gap(self,scope):
        with self.flow.db:
            self.flow.db.execute('UPDATE dl_capture_gaps SET unresolved=0 WHERE scope=?',(scope,))

    def capture(self,scope,message_id,text,*,recorded_at=None):
        if not isinstance(message_id,str) or not 1<=len(message_id)<=256:
            raise ValueError('invalid message id')
        if not isinstance(text,str) or not text.strip() or len(text)>MAX_CAPTURE_CHARS or '\x00' in text:
            raise ValueError('invalid message text')
        if recorded_at is None:
            recorded_at=datetime.now(timezone.utc)
        else:
            recorded_at=datetime.fromisoformat(recorded_at)
            if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:raise ValueError('invalid recorded time')
            recorded_at=recorded_at.astimezone(timezone.utc)
        recorded_at=recorded_at.isoformat(timespec='seconds')
        event_hash=hashlib.sha256((scope+'\0'+message_id).encode('utf-8')).hexdigest()
        digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
        db=self.flow.db
        with db:
            row=db.execute('SELECT content_sha FROM dl_capture WHERE scope=? AND event_hash=?',
                           (scope,event_hash)).fetchone()
            if row:
                if row['content_sha']!=digest:raise ValueError('duplicate id with different content')
                return False
            cursor=db.execute('INSERT INTO dl_capture(scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?)',
                (scope,event_hash,text,digest,recorded_at))
            # 与原话同事务落盘；关闭自动整理时也保存任务，稍后可继续。
            db.execute('INSERT INTO dl_auto_jobs(scope,seq) VALUES(?,?)',(scope,cursor.lastrowid))
            if self.auto_model_enabled:
                db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,policy) VALUES(?,?,?)',(scope,cursor.lastrowid,self.auto_model_policy))
        # This is a cheap, rule-based suggestion only; failure never drops the raw record.
        row=db.execute('SELECT seq FROM dl_capture WHERE scope=? AND event_hash=?',(scope,event_hash)).fetchone()
        try:self.propose(scope,row['seq'],semantic=False)
        except Exception:pass
        return True

    def memory_inventory(self,scope):
        """只统计当前私聊作用域，不返回原话或调用模型。"""
        db=self.flow.db
        captures=db.execute('''SELECT COUNT(*) AS total,
            SUM(CASE WHEN c.state='pending' THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN c.state='linked' THEN 1 ELSE 0 END) AS linked,
            SUM(CASE WHEN p.seq IS NOT NULL THEN 1 ELSE 0 END) AS imported
            FROM dl_capture c LEFT JOIN dl_import_provenance p ON p.scope=c.scope AND p.seq=c.seq
            WHERE c.scope=?''',(scope,)).fetchone()
        approved=db.execute('''SELECT COUNT(*) AS records,COUNT(DISTINCT p.event_id) AS events
            FROM e417_phase p JOIN e417_ingest i ON i.scope=p.scope AND i.message_id=p.message_id
            JOIN messages m ON m.scope=i.scope AND m.id=i.message_id
            WHERE p.scope=? AND i.state='approved_link' AND m.speaker='user' ''',(scope,)).fetchone()
        briefs=db.execute('SELECT COUNT(*) FROM dl_source_briefs WHERE scope=?',(scope,)).fetchone()[0]
        return {'captured':captures['total'],'pending':captures['pending'] or 0,
                'linked_captures':captures['linked'] or 0,'imported':captures['imported'] or 0,
                'reviewed_sources':approved['records'],'events':approved['events'],'brief_versions':briefs}

    def auto_job_health(self,scope):
        """统计后台整理任务；dead 表示已停止重试，原话仍保留、仍可人工查看。"""
        rows=self.flow.db.execute('SELECT status,COUNT(*) FROM dl_auto_jobs WHERE scope=? GROUP BY status',(scope,)).fetchall()
        counts={row[0]:row[1] for row in rows}
        return {'pending':counts.get('pending',0),'done':counts.get('done',0),'dead':counts.get('dead',0)}

    def judge_candidates(self,scope,query,limit=5):
        """给 LLM 候选筛选器准备有限候选：按字面重合排序，无重合时退回最近记录。"""
        if type(limit) is not int or not 1<=limit<=5:raise ValueError('invalid limit')
        pairs=self._character_pairs(query) if isinstance(query,str) else set()
        rows=self.flow.db.execute('''SELECT seq,content,content_sha,recorded_at FROM dl_capture
            WHERE scope=? AND state='pending' ORDER BY seq DESC LIMIT 50''',(scope,)).fetchall()
        scored=[]
        for row in rows:
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:continue
            overlap=sum(1 for pair in pairs if pair in row['content'])
            scored.append((overlap,row['seq'],row))
        scored.sort(key=lambda item:(-item[0],-item[1]))
        chosen=[row for _,_,row in scored[:limit]]
        chosen.sort(key=lambda row:row['seq'])
        return [{'seq':row['seq'],'content':row['content'],'recorded_at':row['recorded_at']}
                for row in chosen]

    def pending(self,scope,after=0):
        if type(after) is not int or after<0:raise ValueError('invalid cursor')
        return [dict(r) for r in self.flow.db.execute('''SELECT c.seq,c.content,c.recorded_at,
                  CASE WHEN p.seq IS NULL THEN 0 ELSE 1 END AS imported
                  FROM dl_capture c LEFT JOIN dl_import_provenance p ON p.scope=c.scope AND p.seq=c.seq
                  WHERE c.scope=? AND c.state='pending' AND c.seq>? ORDER BY c.seq ASC LIMIT 10''',(scope,after))]

    def import_provenance(self,scope,seq):
        if type(seq) is not int or seq<1:raise ValueError('invalid sequence')
        row=self.flow.db.execute('''SELECT p.source_label,p.original_timestamp,p.imported_at,c.recorded_at,c.state
            FROM dl_import_provenance p JOIN dl_capture c ON c.scope=p.scope AND c.seq=p.seq
            WHERE p.scope=? AND p.seq=?''',(scope,seq)).fetchone()
        return dict(row) if row else None

    def model_sources(self,scope,seqs):
        if (not isinstance(seqs,list) or not 1<=len(seqs)<=5 or
            any(type(x) is not int or x<1 for x in seqs) or len(set(seqs))!=len(seqs)):
            raise ValueError('invalid source selection')
        out=[]
        for seq in seqs:
            row=self.flow.db.execute("SELECT seq,content,content_sha,recorded_at,state FROM dl_capture WHERE scope=? AND seq=?",(scope,seq)).fetchone()
            if not row or row['state']!='pending':raise ValueError('pending source missing')
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
                raise ValueError('source changed')
            out.append(dict(row))
        return out

    def new_group_sources(self,scope,seqs):
        if (not isinstance(seqs,list) or not 1<=len(seqs)<=20 or
            any(type(x) is not int or x<1 for x in seqs) or len(set(seqs))!=len(seqs)):
            raise ValueError('invalid selection')
        out=[];cutoff=datetime.now(timezone.utc)
        for seq in seqs:
            row=self.flow.db.execute("SELECT seq,content,content_sha,recorded_at,state FROM dl_capture WHERE scope=? AND seq=?",(scope,seq)).fetchone()
            if not row or row['state']!='pending':raise ValueError('source missing')
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:raise ValueError('changed')
            stamp=datetime.fromisoformat(row['recorded_at'])
            if stamp.utcoffset() is None or stamp>=cutoff:raise ValueError('source time')
            out.append(dict(row))
        return out

    def next_new_group_sources(self,scope,after=0):
        """读取一个有界待审批次，并复用原有来源完整性检查。"""
        if type(after) is not int or not 0<=after<=9223372036854775807:raise ValueError('invalid cursor')
        rows=self.flow.db.execute('''SELECT seq FROM dl_capture WHERE scope=? AND state='pending'
            AND seq>? ORDER BY seq LIMIT 20''',(scope,after)).fetchall()
        if not rows:return []
        return self.new_group_sources(scope,[r['seq'] for r in rows])

    def _new_group_title_available(self,scope,title):
        return (not self.flow.writer._event_by_title(scope,title) and not self.flow.db.execute(
            'SELECT 1 FROM dl_event_alias WHERE scope=? AND alias=?',(scope,title)).fetchone())

    def save_new_group_draft(self,scope,sources,raw):
        current=self.new_group_sources(scope,[x['seq'] for x in sources])
        if current!=sources:raise ValueError('sources changed')
        result=validate_new_groups(raw,current)
        if any(not self._new_group_title_available(scope,g['title']) for g in result['groups']):
            raise ValueError('title conflicts with existing event or alias')
        manifest=[{'seq':x['seq'],'sha':x['content_sha'],'time':x['recorded_at']} for x in current]
        draft='NG-'+uuid.uuid4().hex[:16]
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_new_group_drafts VALUES(?,?,?,?,?)',
                (scope,draft,json.dumps(manifest,ensure_ascii=False),json.dumps(result,ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return self.read_new_group_draft(scope,draft)

    def read_new_group_draft(self,scope,draft_id):
        db=self.flow.db
        draft=db.execute('SELECT * FROM dl_new_group_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not draft:raise ValueError('draft missing')
        manifest=json.loads(draft['manifest_json']);result=json.loads(draft['result_json'])
        assignments={x['seq']:g['title'] for g in result['groups'] for x in g['items']}
        owned={r['title']:r['event_id'] for r in db.execute(
            'SELECT title,event_id FROM dl_new_group_events WHERE scope=? AND draft_id=?',(scope,draft_id))}
        if not set(owned)<={g['title'] for g in result['groups']}:raise ValueError('invalid event mapping')
        sources=[];approved={title:set() for title in owned}
        for entry in manifest:
            row=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,entry['seq'])).fetchone()
            if (not row or row['state'] not in ('pending','linked') or row['content_sha']!=entry['sha'] or
                row['recorded_at']!=entry['time'] or hashlib.sha256(row['content'].encode()).hexdigest()!=entry['sha']):
                raise ValueError('source changed')
            title=assignments.get(row['seq']);prior=self.flow.writer.status(scope,'captured:'+row['event_hash'])
            if row['state']=='linked' and (not title or row['linked_title']!=title):raise ValueError('link changed')
            if prior and prior['state']=='approved_link':
                if (title not in owned or prior['candidate_event_id']!=owned[title] or prior['source_sha256']!=entry['sha'] or
                    (row['state']=='linked' and row['linked_source']!=prior['message_id'])):raise ValueError('approval changed')
                approved[title].add(prior['message_id'])
            elif row['state']=='linked':raise ValueError('missing approved source')
            sources.append({'seq':row['seq'],'content':row['content'],'state':row['state']})
        validate_new_groups(json.dumps(result,ensure_ascii=False),sources)
        for group in result['groups']:
            title=group['title'];matches=self.flow.writer._event_by_title(scope,title)
            alias=db.execute('SELECT 1 FROM dl_event_alias WHERE scope=? AND alias=?',(scope,title)).fetchone()
            if alias:raise ValueError('alias conflict')
            if title not in owned:
                if matches:raise ValueError('title conflict')
                continue
            if len(matches)!=1 or matches[0]['id']!=owned[title]:raise ValueError('event changed')
            rows,relations=self.flow._event_rows(scope,owned[title])
            if relations or {x['id'] for x in rows}!=approved[title]:raise ValueError('event history changed')
            if any(hashlib.sha256(x['content'].encode()).hexdigest()!=x['source_sha256'] for x in rows):
                raise ValueError('event source changed')
        return {'draft_id':draft_id,'result':result,'sources':sources,'owned':owned}

    @atomic_write
    def confirm_new_group_draft(self,scope,draft_id,seqs=None):
        db=self.flow.db
        draft=self.read_new_group_draft(scope,draft_id)
        allowed={x['seq'] for g in draft['result']['groups'] for x in g['items']}
        if seqs is None:chosen=allowed
        else:
            if (not isinstance(seqs,list) or not 1<=len(seqs)<=20 or
                any(type(x) is not int for x in seqs) or len(set(seqs))!=len(seqs) or not set(seqs)<=allowed):
                raise ValueError('invalid selection')
            chosen=set(seqs)
        for group in draft['result']['groups']:
            title=group['title']
            if title not in draft['owned'] and any(x['seq'] in chosen for x in group['items']):
                eid='E-'+uuid.uuid4().hex[:16]
                db.execute('INSERT INTO events(scope,id,title) VALUES(?,?,?)',(scope,eid,title))
                db.execute('INSERT INTO dl_new_group_events VALUES(?,?,?,?)',(scope,draft_id,title,eid))
        completed=[]
        for group in draft['result']['groups']:
            for item in group['items']:
                if item['seq'] not in chosen:continue
                try:self.link_capture(scope,item['seq'],group['title'])
                except Exception:return {'completed':completed,'failed':item['seq']}
                completed.append(item['seq'])
        return {'completed':completed,'failed':None}

    def model_group_snapshot(self,scope,seqs,titles,*,rule_batch=False):
        """准备有限的待审原文和事件，留待模型结果生成后重新校验。"""
        if rule_batch and len(seqs)>10:raise ValueError('too many sources')
        sources=self.new_group_sources(scope,seqs) if rule_batch else self.model_sources(scope,seqs)
        if not isinstance(titles,list) or not 1<=len(titles)<=4 or len(set(titles))!=len(titles):
            raise ValueError('invalid event selection')
        times=[datetime.fromisoformat(x['recorded_at']) for x in sources]
        if any(t.utcoffset() is None for t in times):raise ValueError('invalid capture time')
        earliest=min(times);events=[]
        for title in titles:
            event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
            if len(snap['rows'])>1000:raise ValueError('event too large')
            old_times=[datetime.fromisoformat(r['recorded_at']) for r in snap['rows']]
            if any(t.utcoffset() is None or t>=earliest for t in old_times):
                raise ValueError('event is not earlier than selected messages')
            events.append({'title':title,'id':event['id'],'fingerprint':snap['digest'],
                           'history':[r['content'] for r in snap['rows'][-2:]]})
            if rule_batch:events[-1]['alias_token']=self._alias_token(scope,event['id'])
        return {'sources':sources,'events':events}

    def save_group_draft(self,scope,snapshot,raw,*,origin='model'):
        if origin not in ('model','rule'):raise ValueError('invalid draft origin')
        current=self.model_group_snapshot(scope,[x['seq'] for x in snapshot['sources']],
                                          [x['title'] for x in snapshot['events']],rule_batch=origin=='rule')
        if current!=snapshot:raise ValueError('sources changed')
        result=validate_group_output(raw,current)
        manifest={'sources':[{'seq':x['seq'],'sha':x['content_sha'],'time':x['recorded_at']} for x in current['sources']],
                  'events':[{'title':x['title'],'id':x['id'],'fingerprint':x['fingerprint']} for x in current['events']]}
        if origin=='rule':
            for item,event in zip(manifest['events'],current['events']):item['alias_token']=event['alias_token']
        draft='FG-'+uuid.uuid4().hex[:16]
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_group_drafts(scope,draft_id,manifest_json,result_json,created_at,origin) VALUES(?,?,?,?,?,?)',
                (scope,draft,json.dumps(manifest,ensure_ascii=False),json.dumps(result,ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec='seconds'),origin))
        return {'draft_id':draft,'result':result,'manifest':manifest,'origin':origin,
                'sources':[{'seq':x['seq'],'content':x['content'],'state':x['state']} for x in current['sources']]}

    def _validate_group_draft(self,scope,draft_id):
        db=self.flow.db
        draft=db.execute('SELECT * FROM dl_group_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not draft:raise ValueError('draft missing')
        manifest=json.loads(draft['manifest_json']);result=json.loads(draft['result_json'])
        assignments={item['seq']:group['title'] for group in result['groups'] for item in group['items']}
        events={x['title']:x for x in manifest['events']};sources=[];linked_by_event={x['id']:set() for x in manifest['events']}
        for item in manifest['sources']:
            row=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,item['seq'])).fetchone()
            if (not row or row['state'] not in ('pending','linked') or row['content_sha']!=item['sha'] or
                row['recorded_at']!=item['time'] or hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=item['sha']):
                raise ValueError('source changed')
            title=assignments.get(row['seq'])
            if title is None and row['state']!='pending':raise ValueError('unassigned source changed')
            if title and row['state']=='linked' and row['linked_title']!=title:
                raise ValueError('source cannot be linked')
            prior=self.flow.writer.status(scope,'captured:'+row['event_hash'])
            if prior and prior['state']=='approved_link':
                if (not title or title not in events or prior['candidate_event_id']!=events[title]['id'] or
                    prior['source_sha256']!=item['sha']):
                    raise ValueError('event changed')
                if row['state']=='linked' and row['linked_source']!=prior['message_id']:
                    raise ValueError('linked source mismatch')
                linked_by_event[events[title]['id']].add(prior['message_id'])
            elif row['state']=='linked':raise ValueError('missing approved source')
            sources.append({'seq':row['seq'],'content':row['content'],'state':row['state']})
        validate_group_output(json.dumps(result,ensure_ascii=False),{'sources':sources,'events':manifest['events']})
        for event in manifest['events']:
            matches=self.flow.writer._event_by_title(scope,event['title'])
            if len(matches)!=1 or matches[0]['id']!=event['id']:raise ValueError('event changed')
            if draft['origin']=='rule' and event.get('alias_token')!=self._alias_token(scope,event['id']):
                raise ValueError('event aliases changed')
            snap=self.flow._snapshot(scope,event['id'])
            if not snap or snap['stale']:raise ValueError('event source changed')
            rows,edges=self.flow._event_rows(scope,event['id']);ours=linked_by_event[event['id']]
            old_rows=[r for r in rows if r['id'] not in ours]
            old_edges=[r for r in edges if r['newer'] not in ours and r['older'] not in ours]
            # 确认期间出现额外的关系不能被视为本次关联的副作用。
            if len(old_edges)!=len(edges) or _fingerprint(old_rows,old_edges)!=event['fingerprint']:
                raise ValueError('event history changed')
        return {'draft_id':draft_id,'result':result,'manifest':manifest,'sources':sources,'origin':draft['origin']}

    def read_group_draft(self,scope,draft_id):
        return self._validate_group_draft(scope,draft_id)

    @atomic_write
    def confirm_group_draft(self,scope,draft_id,seqs=None):
        draft=self._validate_group_draft(scope,draft_id)
        allowed={item['seq'] for group in draft['result']['groups'] for item in group['items']}
        if seqs is None:chosen=allowed
        else:
            if (not isinstance(seqs,list) or not 1<=len(seqs)<=10 or
                any(type(x) is not int for x in seqs) or len(set(seqs))!=len(seqs) or not set(seqs)<=allowed):
                raise ValueError('invalid selection')
            chosen=set(seqs)
        completed=[]
        for group in draft['result']['groups']:
            for item in group['items']:
                if item['seq'] not in chosen:continue
                try:self.link_capture(scope,item['seq'],group['title'])
                except Exception:return {'completed':completed,'failed':item['seq']}
                completed.append(item['seq'])
        return {'completed':completed,'failed':None}

    def multi_suggest(self,scope,seqs,*,semantic=False):
        """Find unconfirmed historical candidates for manually selected pending lines."""
        if (not isinstance(seqs,list) or not 2<=len(seqs)<=4 or
            any(type(x) is not int or x<1 for x in seqs) or seqs!=sorted(seqs)):
            raise ValueError('select two to four records in chronological sequence')
        sources=self.model_sources(scope,seqs)
        times=[datetime.fromisoformat(x['recorded_at']) for x in sources]
        if any(t.tzinfo is None for t in times) or times!=sorted(times):
            raise ValueError('invalid source chronology')
        query='\n'.join(x['content'] for x in sources)
        eligible={}
        for event in self.flow._events(scope):
            snap=self.flow._snapshot(scope,event['id'])
            if not snap or snap['stale']:continue
            try:
                timestamps=[datetime.fromisoformat(row['recorded_at']) for row in snap['rows']]
                if any(t.tzinfo is None or t>=times[0] for t in timestamps):continue
            except (TypeError,ValueError):continue
            eligible[event['id']]=event
        matches=[]
        for event in eligible.values():
            title=event['title']
            if any(title in line['content'] and not re.search(
                r'(?:不是|并非|不指|别说|并不是)\s*'+re.escape(title),line['content']) for line in sources):
                matches.append({'title':title,'method':'标题字面匹配'})
        semantic_error=False
        if semantic and eligible:
            try:
                ids=self._index().search(scope,query)
                existing={x['title'] for x in matches}
                for eid in ids:
                    event=eligible.get(eid)
                    if event and event['title'] not in existing:
                        matches.append({'title':event['title'],'method':'多轮语义相似'})
                        existing.add(event['title'])
            except Exception:semantic_error=True
        return {'candidates':matches[:3],'semantic_error':semantic_error,'status':'UNCONFIRMED'}

    def save_model_draft(self,scope,sources,raw):
        current=self.model_sources(scope,[x['seq'] for x in sources])
        if current!=sources:raise ValueError('sources changed while generating')
        candidate=validate_output(raw,current)
        manifest=[{'seq':x['seq'],'sha256':x['content_sha'],'recorded_at':x['recorded_at']} for x in current]
        draft='G-'+uuid.uuid4().hex[:16]
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_model_drafts VALUES(?,?,?,?,?)',
                (scope,draft,json.dumps(manifest,ensure_ascii=False),json.dumps(candidate,ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return self.read_model_draft(scope,draft)

    def contrast_snapshot(self,scope,seq,title):
        pending=self.model_sources(scope,[seq])[0]
        events=self.flow.writer._event_by_title(scope,title)
        if len(events)!=1:raise ValueError('event not unique')
        snap=self.flow._snapshot(scope,events[0]['id'])
        if not snap or snap['stale']:raise ValueError('source unavailable')
        new_time=datetime.fromisoformat(pending['recorded_at'])
        # Only already recorded sources may serve as old history; no new-line leakage.
        older=[r for r in snap['rows'] if datetime.fromisoformat(r['recorded_at'])<new_time]
        if not older:raise ValueError('no older reviewed source')
        return {'pending':pending,'event_id':events[0]['id'],'event_title':title,
                'fingerprint':snap['digest'],
                'history':[{'id':r['id'],'content':r['content'],'recorded_at':r['recorded_at']} for r in older[-3:]]}

    def save_contrast_draft(self,scope,snapshot,raw,*,origin='model'):
        if origin not in ('model','rule'):raise ValueError('invalid draft origin')
        current=self.contrast_snapshot(scope,snapshot['pending']['seq'],snapshot['event_title'])
        if current!=snapshot:raise ValueError('source changed during generation')
        result=validate_contrast(raw,current)
        draft='C-'+uuid.uuid4().hex[:16]
        # Persist fingerprints and source IDs; do not duplicate the entire original text.
        manifest={'pending_sha':snapshot['pending']['content_sha'],'pending_time':snapshot['pending']['recorded_at'],
                  'event_id':snapshot['event_id'],'fingerprint':snapshot['fingerprint'],
                  'old_ids':[x['id'] for x in snapshot['history']]}
        with self.flow.db:
            self.flow.db.execute('''INSERT INTO dl_contrast_drafts
                (scope,draft_id,pending_seq,event_title,snapshot_json,result_json,created_at,origin)
                VALUES(?,?,?,?,?,?,?,?)''',
                (scope,draft,snapshot['pending']['seq'],snapshot['event_title'],json.dumps(manifest),
                 json.dumps(result,ensure_ascii=False),datetime.now(timezone.utc).isoformat(timespec='seconds'),origin))
        return self.read_contrast_draft(scope,draft)

    def read_contrast_draft(self,scope,draft_id):
        row=self.flow.db.execute('SELECT * FROM dl_contrast_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not row:raise ValueError('draft missing')
        manifest=json.loads(row['snapshot_json']);result=json.loads(row['result_json'])
        try:
            snap=self.contrast_snapshot(scope,row['pending_seq'],row['event_title'])
            current={'pending_sha':snap['pending']['content_sha'],'pending_time':snap['pending']['recorded_at'],
                     'event_id':snap['event_id'],'fingerprint':snap['fingerprint'],
                     'old_ids':[x['id'] for x in snap['history']]}
            if current!=manifest:raise ValueError('source changed')
            validate_contrast(json.dumps(result,ensure_ascii=False),snap)
            status='UNREVIEWED'
        except Exception:status='STALE'
        return {'draft_id':draft_id,'status':status,'result':result,'event_title':row['event_title'],
                'pending_seq':row['pending_seq'],'origin':row['origin']}

    @atomic_write
    def confirm_contrast(self,scope,draft_id,kind,*,validate_only=False):
        """Explicitly link a contrast source and record the operator-selected relation.

        The writer commits in stages. Retrying this operation is safe after an
        interrupted link; historical provenance is checked again before each stage.
        """
        if kind not in ('corrects','supersedes','related'):raise ValueError('invalid relation')
        db=self.flow.db
        draft=db.execute('SELECT * FROM dl_contrast_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not draft:raise ValueError('draft missing')
        manifest=json.loads(draft['snapshot_json']);result=json.loads(draft['result_json'])
        capture=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,draft['pending_seq'])).fetchone()
        if not capture or capture['state'] not in ('pending','linked'):raise ValueError('source unavailable')
        if (hashlib.sha256(capture['content'].encode('utf-8')).hexdigest()!=manifest['pending_sha'] or
            capture['recorded_at']!=manifest['pending_time']):raise ValueError('source changed')
        if capture['state']=='linked' and capture['linked_title']!=draft['event_title']:
            raise ValueError('source linked to a different event')
        origin='captured:'+capture['event_hash']
        prior=self.flow.writer.status(scope,origin)
        linked_id=None
        if prior and prior['state']=='approved_link':
            if prior['candidate_event_id']!=manifest['event_id']:raise ValueError('linked event mismatch')
            linked_id=prior['message_id']
        snap=self.flow._snapshot(scope,manifest['event_id'])
        if not snap or snap['stale'] or snap['title']!=draft['event_title']:raise ValueError('event changed')
        rows,edges=self.flow._event_rows(scope,manifest['event_id'])
        original=[r for r in rows if r['id']!=linked_id]
        old_edges=[r for r in edges if linked_id not in (r['newer'],r['older'])]
        if _fingerprint(original,old_edges)!=manifest['fingerprint']:raise ValueError('history changed')
        history=[{'id':r['id'],'content':r['content'],'recorded_at':r['recorded_at']} for r in original
                 if r['id'] in manifest['old_ids']]
        if [r['id'] for r in history]!=manifest['old_ids']:raise ValueError('history missing')
        validate_contrast(json.dumps(result,ensure_ascii=False),{'history':history,'pending':dict(capture)})
        if linked_id and any(r['newer']==linked_id and r['older']==result['old_id'] and r['kind']!=kind for r in edges):
            raise ValueError('different relation already exists')
        if linked_id and any(linked_id in (r['newer'],r['older']) and
                             (r['newer'],r['older'],r['kind'])!=(linked_id,result['old_id'],kind) for r in edges):
            raise ValueError('linked source relations changed')
        if validate_only:return {'event_id':manifest['event_id']}
        if capture['state']=='pending':linked_id=self.link_capture(scope,capture['seq'],draft['event_title'])
        elif linked_id!=capture['linked_source']:raise ValueError('linked source mismatch')
        self.relate_sources(scope,linked_id,result['old_id'],kind)
        return {'newer_id':linked_id,'older_id':result['old_id'],'kind':kind}

    def read_model_draft(self,scope,draft_id):
        row=self.flow.db.execute('SELECT * FROM dl_model_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not row:raise ValueError('draft missing')
        manifest=json.loads(row['source_manifest']);result=json.loads(row['result_json'])
        try:
            current=self.model_sources(scope,[x['seq'] for x in manifest])
            now=[{'seq':x['seq'],'sha256':x['content_sha'],'recorded_at':x['recorded_at']} for x in current]
            if now!=manifest:raise ValueError('source changed')
            validate_output(json.dumps(result,ensure_ascii=False),current)
            state='UNREVIEWED'
        except Exception:state='STALE'
        return {'draft_id':draft_id,'status':state,'result':result,'created_at':row['created_at']}

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

    def _weak_candidate_details(self,scope,row,events):
        """Rank lexical candidates and preserve a short path back to original lines."""
        grams=self._character_pairs(row['content'])
        if len(grams)<2:return []
        try:
            cutoff=datetime.fromisoformat(row['recorded_at'])
            if cutoff.tzinfo is None:return []
        except (TypeError,ValueError):return []
        documents=[]
        denied=self._negated_events(scope,row['content'],events)
        allowed={event['id']:event for event in events if event['id'] not in denied}
        postings=self.lexical_index.candidates(scope,grams)
        for eid,item in postings.items():
            event=allowed.get(eid)
            if not event or len(item['terms'])<2:continue
            snap=self.flow._snapshot(scope,eid)
            if not snap or snap['stale']:continue
            if self.lexical_index.fingerprint(snap)!=item['fingerprint']:
                with self.flow.db:self.flow.db.execute('INSERT OR IGNORE INTO dl_lex_dirty VALUES(?,?)',(scope,eid))
                continue
            event={'id':snap['id'],'title':snap['title']}
            try:
                times=[datetime.fromisoformat(x['recorded_at']) for x in snap['rows']]
                if any(t.tzinfo is None or t>=cutoff for t in times):continue
            except (TypeError,ValueError):continue
            documents.append((event,snap,item['terms'],max(times)))
        frequencies=Counter(g for _,_,shared,_ in documents for g in shared)
        results=[]
        historical=bool(re.search(r'最早|最初|起初|原本|原来|以前|之前|过去|当时|上次|去年|历史|曾经|当年|那时候|不喜欢|不再|讨厌|\d{4}年|\d{4}[-/]\d{1,2}',row['content']))
        for event,snap,shared,latest in documents:
            if len(shared)<2:continue
            score=sum(1.0+math.log((len(documents)+1)/(frequencies[g]+1)) for g in shared)
            # Longest exact substrings retain their source ID; they are not entity labels.
            sources=[(r['id'],r['content']) for r in reversed(snap['rows'])]+[(None,event['title'])]
            clues=[];covered=set();query=row['content']
            for size in range(min(8,len(query)),1,-1):
                for start in range(len(query)-size+1):
                    term=query[start:start+size]
                    if any(i in covered for i in range(start,start+size)) or not all('\u4e00'<=c<='\u9fff' for c in term):continue
                    if size==2 and term in _GENERIC_GRAMS:continue
                    source=next(((mid,text) for mid,text in sources if term in text),None)
                    if source:
                        clues.append({'text':term,'source_id':source[0]})
                        covered.update(range(start,start+size))
                        if len(clues)>=3:break
                if len(clues)>=3:break
            result={'event_id':event['id'],'title':event['title'],'score':score,
                    'fingerprint':snap['digest'],'clues':clues}
            if self.preference_rank_enabled:
                # 两个 UTC 日期的不同自述才产生微小排序加分，不创建事实或候选。
                signals=self._preference_signal(scope,snap)
                result['preference_boost']=0.15 if len(signals)>=2 and not historical else 0.0
                result['preference_source_ids']=[x['id'] for x in signals[:2]] if result['preference_boost'] else []
            if self.recency_rank_enabled:
                # 不修改来源时间；近期只是排序线索，并非新颖度或事实有效性。
                age_days=max(0.0,(cutoff-latest).total_seconds()/86400.0)
                result['recency_boost']=0.0 if historical else 0.25*max(0.0,1.0-age_days/30.0)
            results.append(result)
        important=set()
        if self.event_priority_enabled:
            important={r['event_id'] for r in self.flow.db.execute(
                "SELECT event_id FROM dl_event_priority WHERE scope=? AND priority='high'",(scope,))}
            for item in results:item['priority_boost']=0.25 if item['event_id'] in important else 0.0
        return sorted(results,key=lambda x:(-(x['score']+x.get('priority_boost',0)+x.get('recency_boost',0)+x.get('preference_boost',0)),x['event_id']))[:3]

    def _weak_candidates(self,scope,row,events):
        return [x['event_id'] for x in self._weak_candidate_details(scope,row,events)]

    def explain_weak_candidates(self,scope,seq):
        source=self.model_sources(scope,[seq])[0]
        return self._weak_candidate_details(scope,source,self.flow._events(scope))

    def propose(self,scope,seq,*,semantic=False,weak=None):
        """Generate traceable, unconfirmed links. Does not approve or modify source."""
        if type(seq) is not int or seq<1:raise ValueError('invalid sequence')
        db=self.flow.db
        row=db.execute("SELECT * FROM dl_capture WHERE scope=? AND seq=? AND state='pending'",(scope,seq)).fetchone()
        if not row:raise ValueError('pending capture unavailable')
        if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
            raise ValueError('capture integrity failed')
        text=row['content'];events=self.flow._events(scope)
        matches=[(event['id'],method) for event,method in self._named_events(scope,text,events)]
        if weak is None:weak=self.weak_association_enabled
        if weak:
            existing={x[0] for x in matches}
            matches.extend((eid,'原文词组弱匹配') for eid in self._weak_candidates(scope,row,events) if eid not in existing)
        semantic_error=False
        if semantic:
            try:
                semantic_ids=self._index().search(scope,text)
                existing={x[0] for x in matches}
                denied=self._negated_events(scope,text,events)
                matches.extend((eid,'语义相似') for eid in semantic_ids if eid not in existing and eid not in denied)
            except Exception:semantic_error=True
        titles={e['id']:e['title'] for e in events};valid=[]
        for eid,method in matches[:6]:
            if eid not in titles:continue
            snap=self.flow._snapshot(scope,eid)
            if not snap or snap['stale']:continue
            valid.append({'event_id':eid,'title':titles[eid],'method':method,'fingerprint':snap['digest']})
        # Propose a new event only from an explicit author-supplied event marker.
        # Avoid inventing titles from free text or treating it as a fact.
        marker=re.match(r'^【事件[：:]([^【】\r\n]{1,80})】',text)
        new_title=None
        if marker:
            title=marker.group(1).strip()
            if title and title not in titles.values():
                new_title={'title':title,'method':'原文明确事件标记','status':'UNCONFIRMED'}
        elif not valid:
            # A whole original sentence may serve as a rough label; no rewriting,
            # inference or slicing. Skip questions and generic short utterances.
            raw=text.strip()
            if (raw==text and 5<=len(raw)<=36 and '\n' not in raw and
                not any(c in raw for c in '?？|【】') and
                any(word in raw for word in ('打算','计划','准备','想去','想学')) and
                raw not in titles.values()):
                new_title={'title':raw,'method':'原句作为临时标题（未概括）','status':'UNCONFIRMED'}
        now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        with db:
            db.execute('DELETE FROM dl_suggestions WHERE scope=? AND capture_seq=?',(scope,seq))
            for item in valid:
                db.execute('INSERT INTO dl_suggestions VALUES(?,?,?,?,?,?,?)',
                    (scope,seq,item['event_id'],row['content_sha'],item['fingerprint'],item['method'],now))
            db.execute('DELETE FROM dl_new_title_suggestions WHERE scope=? AND capture_seq=?',(scope,seq))
            if new_title:
                db.execute('INSERT INTO dl_new_title_suggestions VALUES(?,?,?,?,?,?)',
                    (scope,seq,row['content_sha'],new_title['title'],new_title['method'],now))
        return {'seq':seq,'content':text,'candidates':valid,'semantic_attempted':semantic,
                'semantic_error':semantic_error,'new_event_candidate':new_title,'status':'UNCONFIRMED'}

    def organize_batch(self,scope,limit=10,after=0):
        """Refresh rule-based suggestions for a bounded batch; no implicit BGE calls."""
        if type(limit) is not int or not 1<=limit<=10:raise ValueError('invalid batch size')
        if type(after) is not int or after<0:raise ValueError('invalid cursor')
        rows=self.flow.db.execute("SELECT seq FROM dl_capture WHERE scope=? AND state='pending' AND seq>? ORDER BY seq LIMIT ?",(scope,after,limit)).fetchall()
        outcomes=[]
        for row in rows:
            try:
                result=self.propose(scope,row['seq'],semantic=False)
                outcomes.append({'seq':row['seq'],'candidates':result['candidates'],
                                 'new_event_candidate':result['new_event_candidate'],'status':'UNCONFIRMED'})
            except Exception:
                outcomes.append({'seq':row['seq'],'candidates':[],'new_event_candidate':None,'status':'ERROR'})
        return outcomes

    def group_pending_suggestions(self,scope,after=0):
        """按连续采集记录生成可核对候选，不写入关联或模型调用。"""
        if type(after) is not int or not 0<=after<=9223372036854775807:
            raise ValueError('invalid cursor')
        records=self.flow.db.execute('''SELECT seq,content,content_sha,recorded_at,state FROM dl_capture
            WHERE scope=? AND seq>? ORDER BY seq LIMIT 10''',(scope,after)).fetchall()
        events=self.flow._events(scope);eligible={}
        def latest_time(event):
            if event['id'] in eligible:return eligible[event['id']]
            try:
                snap=self.flow._snapshot(scope,event['id'])
                times=[datetime.fromisoformat(r['recorded_at']) for r in snap['rows']] if snap and not snap['stale'] else []
                latest=max(times) if times and all(t.utcoffset() is not None for t in times) else None
            except (TypeError,ValueError,OverflowError):latest=None
            eligible[event['id']]=latest
            return latest
        groups=[];current=[];current_event=None;previous_time=None
        def finish():
            nonlocal current,current_event,previous_time
            if len(current)>=2:groups.append({'title':current_event['title'],'items':current})
            current=[];current_event=None;previous_time=None
        for row in records:
            try:
                if row['state']!='pending' or hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
                    finish();continue
                stamp=datetime.fromisoformat(row['recorded_at'])
                if stamp.utcoffset() is None or re.search(r'不是|并非|不指|别说|不对|记错|认错|错了',row['content']):
                    finish();continue
                named=self._named_events(scope,row['content'],events)
                if len(named)>1:finish();continue
                event=named[0][0] if named else None
                method=named[0][1] if named else ''
                within=previous_time is not None and 0<=(stamp-previous_time).total_seconds()<=180
                if (event is None and current and within and _FOLLOWUP_UPDATE.match(row['content'].strip())):
                    event=current_event;method='相邻可能更新'
                if not event:finish();continue
                latest=latest_time(event)
                if latest is None or latest>=stamp:finish();continue
                if current and (event['id']!=current_event['id'] or not within):finish()
                current_event=event
                current.append({'seq':row['seq'],'content':row['content'],'recorded_at':row['recorded_at'],'method':method})
                previous_time=stamp
            except (TypeError,ValueError,OverflowError):finish()
        finish()
        return {'groups':groups,'next_cursor':records[-1]['seq'] if records else None,'scanned':len(records)}

    def rule_group_draft(self,scope,after=0):
        """把一页规则候选整理为可复核草稿，不调用模型或写入原话关联。"""
        db=self.flow.db
        db.execute('BEGIN IMMEDIATE')
        try:
            result=self._rule_group_draft_locked(scope,after)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise

    def _rule_group_draft_locked(self,scope,after):
        suggestions=self.group_pending_suggestions(scope,after)
        selected=[];titles=[]
        for group in suggestions['groups']:
            title=group['title']
            if title not in titles and len(titles)>=4:break
            items=[x for x in group['items'] if len(x['content'])>=2]
            if not items:continue
            if title not in titles:titles.append(title)
            selected.append({'title':title,'items':items})
        if not selected:return {'draft':None,'next_cursor':suggestions['next_cursor'],'scanned':suggestions['scanned']}
        def snapshot_for(groups):
            seqs=[x['seq'] for group in groups for x in group['items']]
            names=list(dict.fromkeys(group['title'] for group in groups))
            return self.model_group_snapshot(scope,seqs,names,rule_batch=True)
        try:snapshot=snapshot_for(selected)
        except ValueError:
            # 不同事件的时间边界可能无法合并；保留第一组并从其后继续。
            selected=selected[:1];snapshot=snapshot_for(selected)
        merged={}
        for group in selected:
            for item in group['items']:
                merged.setdefault(group['title'],[]).append({'seq':item['seq'],'quote':item['content'][:80]})
        raw=json.dumps({'groups':[{'title':title,'items':items} for title,items in merged.items()]},ensure_ascii=False)
        draft=self.save_group_draft(scope,snapshot,raw,origin='rule')
        return {'draft':draft,'next_cursor':max(x['seq'] for g in selected for x in g['items']),
                'scanned':suggestions['scanned']}

    def _update_candidates(self,scope,source,events,*,include_weak=True):
        """Find possible event labels, preserving ambiguity as an unconfirmed clue."""
        content=source['content']
        if not _UPDATE_CUES.search(content):return []
        def named(text):return [event for event,_ in self._named_events(scope,text,events)]
        direct=self._named_events(scope,content,events)
        if direct:return [(event,'完整标题' if method=='标题字面匹配' else '人工别名') for event,method in direct]
        aliases=[r['alias'] for r in self.flow.db.execute('SELECT alias FROM dl_event_alias WHERE scope=?',(scope,))]
        if any(x['title'] in content for x in events) or any(x in content for x in aliases):return []
        def weak_match(item):
            if not include_weak or not self.weak_association_enabled or re.search(r'不是|并非|不指|别说',item['content']):return []
            details=self._weak_candidate_details(scope,item,events)
            if len(details)!=1:return []
            detail=details[0]
            source_id=next((x['source_id'] for x in detail['clues'] if x['source_id'] and len(x['text'])>=4),None)
            event=next((x for x in events if x['id']==detail['event_id']),None)
            return [({**event,'clue_source_id':source_id},'历史原文词组匹配')] if event and source_id else []
        matches=weak_match(source)
        if matches:return matches
        if not _FOLLOWUP_UPDATE.match(content.strip()):return []
        cursor=source
        for distance in range(1,5):
            previous=self.flow.db.execute('''SELECT seq,content,content_sha,recorded_at FROM dl_capture
                WHERE scope=? AND seq<? ORDER BY seq DESC LIMIT 1''',(scope,cursor['seq'])).fetchone()
            if not previous or hashlib.sha256(previous['content'].encode('utf-8')).hexdigest()!=previous['content_sha']:return []
            try:
                start=datetime.fromisoformat(previous['recorded_at']);end=datetime.fromisoformat(cursor['recorded_at'])
                if start.tzinfo is None or end.tzinfo is None or not 0<=(end-start).total_seconds()<=180:return []
            except (ValueError,TypeError):return []
            candidates=named(previous['content'])
            if candidates:
                method='前一条采集记录的标题' if distance==1 else '前面采集记录的标题（连续更新线索）'
                return [(candidates[0],method)] if len(candidates)==1 else []
            if any(x['title'] in previous['content'] for x in events) or any(x in previous['content'] for x in aliases):return []
            matches=weak_match(previous)
            if matches:return [(matches[0][0],'前文'+matches[0][1])]
            if not _FOLLOWUP_UPDATE.match(previous['content'].strip()):return []
            cursor=previous
        return []

    def update_clues(self,scope,after=0,limit=10):
        """Review a bounded page of pending records without promoting any clue."""
        if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=10:
            raise ValueError('invalid cursor')
        records=self.pending(scope,after=after)[:limit]
        events=self.flow._events(scope);out=[]
        for row in records:
            source=self.model_sources(scope,[row['seq']])[0]
            matches=[]
            if _UPDATE_CUES.search(source['content']):
                for event,method in self._update_candidates(scope,source,events):
                    title=event['title']
                    snap=self.flow._snapshot(scope,event['id'])
                    if not snap or snap['stale']:continue
                    try:
                        timestamp=datetime.fromisoformat(source['recorded_at'])
                        old=[r for r in snap['rows'] if datetime.fromisoformat(r['recorded_at'])<timestamp]
                    except (TypeError,ValueError):continue
                    reference=next((r for r in old if r['id']==event.get('clue_source_id')),None) if event.get('clue_source_id') else (old[-1] if old else None)
                    if reference:
                        matches.append({'title':title,'method':method,'old_id':reference['id'],'old_content':reference['content'],
                                        'old_time':reference['recorded_at']})
            out.append({'seq':source['seq'],'content':source['content'],'recorded_at':source['recorded_at'],
                        'matches':matches[:3]})
        return out

    def rule_update_drafts(self,scope,after=0):
        """生成至多五条有完整新旧原话的规则对照，不自动确认关系。"""
        rows=self.update_clues(scope,after=after);drafts=[];last=None
        for row in rows:
            last=row['seq']
            if len(row['matches'])!=1:continue
            match=row['matches'][0]
            try:
                snapshot=self.contrast_snapshot(scope,row['seq'],match['title'])
                old=next((x for x in snapshot['history'] if x['id']==match['old_id']),None)
                if not old or not 2<=len(old['content'])<=400 or not 2<=len(row['content'])<=400:continue
                result={'relation':'uncertain','explanation':'字面更新线索；较早原话未必相关，请人工核对。',
                        'old_id':old['id'],'old_quote':old['content'],'new_quote':row['content']}
                drafts.append(self.save_contrast_draft(scope,snapshot,json.dumps(result,ensure_ascii=False),origin='rule'))
            except (ValueError,TypeError,OverflowError):continue
            if len(drafts)>=5:break
        return {'drafts':drafts,'next_cursor':last,'scanned':len(rows)}

    def confirm_contrast_batch(self,scope,draft_ids,kind):
        """同一事务内校验和写入；失败时回滚本次批次。"""
        if (not isinstance(draft_ids,list) or not 1<=len(draft_ids)<=5 or
            any(not isinstance(x,str) or not re.fullmatch(r'C-[0-9a-f]{16}',x) for x in draft_ids) or
            len(set(draft_ids))!=len(draft_ids) or kind not in ('corrects','supersedes','related')):
            raise ValueError('invalid selection')
        db=self.flow.db;failed=None
        db.execute('BEGIN IMMEDIATE')
        try:
            with self.flow.core.deferred_writes():
                self._check_contrast_batch(scope,draft_ids,kind)
                for did in draft_ids:
                    failed=did
                    items=self._check_contrast_batch(scope,draft_ids,kind)
                    item=next(x for x in items if x['draft_id']==did)
                    self.link_capture_relation(scope,item['seq'],item['title'],item['old_id'],kind)
            db.commit()
        except Exception:
            db.rollback()
            if failed is None:raise
            return {'completed':[],'failed':failed}
        return {'completed':list(draft_ids),'failed':None}

    def _check_contrast_batch(self,scope,draft_ids,kind):
        db=self.flow.db;items=[];groups={};seen=set()
        for did in draft_ids:
            draft=db.execute('SELECT * FROM dl_contrast_drafts WHERE scope=? AND draft_id=?',(scope,did)).fetchone()
            if not draft:raise ValueError('draft missing')
            manifest=json.loads(draft['snapshot_json']);result=json.loads(draft['result_json'])
            seq=draft['pending_seq']
            if seq in seen:raise ValueError('duplicate source')
            seen.add(seq)
            capture=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
            if not capture or capture['state'] not in ('pending','linked'):raise ValueError('capture unavailable')
            if (hashlib.sha256(capture['content'].encode('utf-8')).hexdigest()!=manifest['pending_sha'] or
                capture['content_sha']!=manifest['pending_sha'] or capture['recorded_at']!=manifest['pending_time']):
                raise ValueError('capture changed')
            prior=self.flow.writer.status(scope,'captured:'+capture['event_hash']);linked=None
            if prior:
                if prior['state']=='approved_link':
                    if prior['candidate_event_id']!=manifest['event_id'] or prior['source_sha256']!=manifest['pending_sha']:
                        raise ValueError('approval changed')
                    linked=prior['message_id']
                elif prior['state']!='pending_review':raise ValueError('approval unavailable')
            if capture['state']=='linked' and (not linked or capture['linked_source']!=linked or capture['linked_title']!=draft['event_title']):
                raise ValueError('linked capture changed')
            item={'draft_id':did,'seq':seq,'title':draft['event_title'],'manifest':manifest,
                  'result':result,'capture':dict(capture),'linked':linked,'old_id':result['old_id']}
            items.append(item);groups.setdefault(manifest['event_id'],[]).append(item)
        for eid,group in groups.items():
            snap=self.flow._snapshot(scope,eid)
            if not snap or snap['stale'] or any(x['title']!=snap['title'] for x in group):raise ValueError('event changed')
            linked={x['linked'] for x in group if x['linked']}
            if len(linked)!=sum(x['linked'] is not None for x in group):raise ValueError('duplicate linked source')
            rows,edges=self.flow._event_rows(scope,eid)
            original=[r for r in rows if r['id'] not in linked]
            old_edges=[r for r in edges if r['newer'] not in linked and r['older'] not in linked]
            expected={(x['linked'],x['old_id'],kind) for x in group if x['linked']}
            if any((r['newer'],r['older'],r['kind']) not in expected for r in edges
                   if r['newer'] in linked or r['older'] in linked):raise ValueError('new relation changed')
            digest=_fingerprint(original,old_edges)
            for item in group:
                manifest=item['manifest']
                if digest!=manifest['fingerprint']:raise ValueError('history changed')
                history=[{'id':r['id'],'content':r['content'],'recorded_at':r['recorded_at']}
                         for r in original if r['id'] in manifest['old_ids']]
                if [r['id'] for r in history]!=manifest['old_ids']:raise ValueError('history missing')
                validate_contrast(json.dumps(item['result'],ensure_ascii=False),{'history':history,'pending':item['capture']})
                old=next((r for r in history if r['id']==item['old_id']),None)
                if not old:raise ValueError('old source missing')
                before=datetime.fromisoformat(old['recorded_at']);after=datetime.fromisoformat(item['capture']['recorded_at'])
                if before.utcoffset() is None or after.utcoffset() is None or before>=after:
                    raise ValueError('source chronology invalid')
        return items

    def _pending_update_hint(self,scope,title,*,snapshot=None):
        """Bounded hint scan; no unreviewed text enters the context."""
        rows=self.flow.db.execute("SELECT seq,content,content_sha,recorded_at FROM dl_capture WHERE scope=? AND state='pending' ORDER BY seq DESC LIMIT 101",(scope,)).fetchall()
        events=self.flow._events(scope)
        known=None
        def history_terms():
            nonlocal known
            if known is not None:return known
            known={}
            for old in snapshot['rows']:
                at=datetime.fromisoformat(old['recorded_at'])
                if at.tzinfo is None:raise ValueError('unknown historical time')
                text=old['content']
                for i in range(len(text)-3):
                    phrase=text[i:i+4]
                    if (all('\u4e00'<=c<='\u9fff' for c in phrase) and
                        len(self._character_pairs(phrase))>=2 and
                        (phrase not in known or at<known[phrase])):known[phrase]=at
            return known
        for row in rows:
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
                return True
            if any(event['title']==title for event,_ in self._update_candidates(scope,row,events,include_weak=False)):
                return True
            if snapshot and _UPDATE_CUES.search(row['content']) and not re.search(r'不是|并非|不指|别说',row['content']):
                cursor=row
                for distance in range(5):
                    text=cursor['content']
                    phrases={text[i:i+4] for i in range(len(text)-3)
                             if all('\u4e00'<=c<='\u9fff' for c in text[i:i+4])
                             and len(self._character_pairs(text[i:i+4]))>=2}
                    try:
                        at=datetime.fromisoformat(cursor['recorded_at'])
                        if at.tzinfo is None:return True
                        terms=history_terms()
                        if any(phrase in terms and terms[phrase]<at for phrase in phrases):return True
                    except (TypeError,ValueError):return True
                    if distance==4 or not _FOLLOWUP_UPDATE.match(text.strip()):break
                    previous=self.flow.db.execute('''SELECT seq,content,content_sha,recorded_at FROM dl_capture
                        WHERE scope=? AND seq<? ORDER BY seq DESC LIMIT 1''',(scope,cursor['seq'])).fetchone()
                    if not previous:break
                    if hashlib.sha256(previous['content'].encode('utf-8')).hexdigest()!=previous['content_sha']:return True
                    try:
                        start=datetime.fromisoformat(previous['recorded_at']);end=datetime.fromisoformat(cursor['recorded_at'])
                        if start.tzinfo is None or end.tzinfo is None or not 0<=(end-start).total_seconds()<=180:break
                    except (TypeError,ValueError):break
                    cursor=previous
        return len(rows)>100

    @atomic_write
    def link_capture_relation(self,scope,seq,title,old_id,kind):
        """Explicitly attach a captured line and relate it to one reviewed source."""
        if type(seq) is not int or not 1<=seq<=9223372036854775807:
            raise ValueError('invalid sequence')
        if kind not in ('corrects','supersedes','related') or not isinstance(old_id,str) or not old_id:
            raise ValueError('invalid relation')
        db=self.flow.db
        capture=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
        if not capture or capture['state'] not in ('pending','linked'):
            raise ValueError('capture unavailable')
        if hashlib.sha256(capture['content'].encode('utf-8')).hexdigest()!=capture['content_sha']:
            raise ValueError('cannot preserve source')
        if capture['state']=='linked' and capture['linked_title']!=title:
            raise ValueError('capture linked elsewhere')
        event=self._event_by_title(scope,title)
        snap=self.flow._snapshot(scope,event['id'])
        old=next((r for r in snap['rows'] if r['id']==old_id),None)
        if not old:raise ValueError('old source missing')
        new_time=datetime.fromisoformat(capture['recorded_at']);old_time=datetime.fromisoformat(old['recorded_at'])
        if new_time.utcoffset() is None or old_time.utcoffset() is None or old_time>=new_time:
            raise ValueError('source chronology unknown')
        prior=self.flow.writer.status(scope,'captured:'+capture['event_hash'])
        linked_id=None
        if prior and prior['state']=='approved_link':
            if prior['candidate_event_id']!=event['id']:raise ValueError('event mismatch')
            linked_id=prior['message_id']
        if capture['state']=='linked' and linked_id!=capture['linked_source']:
            raise ValueError('linked source mismatch')
        if linked_id and db.execute('SELECT 1 FROM relations WHERE scope=? AND newer=? AND older=? AND kind<>?',
                                    (scope,linked_id,old_id,kind)).fetchone():
            raise ValueError('different relation already exists')
        linked_id=self.link_capture(scope,seq,title)
        self.relate_sources(scope,linked_id,old_id,kind)
        return linked_id

    @atomic_write
    def link_capture_batch(self,scope,seqs,title):
        """Validate every selected source before linking; report partial writes for safe retry."""
        if (not isinstance(seqs,list) or not 1<=len(seqs)<=10 or
            any(type(x) is not int or not 1<=x<=9223372036854775807 for x in seqs) or
            len(set(seqs))!=len(seqs)):
            raise ValueError('invalid selection')
        if not isinstance(title,str) or not 1<=len(title)<=80 or any(c in title for c in '\n\r【】'):
            raise ValueError('invalid event')
        seqs=sorted(seqs)
        db=self.flow.db
        for seq in seqs:
            row=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
            if not row or row['state'] not in ('pending','linked'):
                raise ValueError('source unavailable')
            if row['state']=='linked' and row['linked_title']!=title:
                raise ValueError('source already linked elsewhere')
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
                raise ValueError('source cannot be preserved')
        completed=[]
        for seq in seqs:
            try:self.link_capture(scope,seq,title)
            except Exception:
                return {'completed':completed,'failed':seq,'remaining':[x for x in seqs if x>seq]}
            completed.append(seq)
        return {'completed':completed,'failed':None,'remaining':[]}

    @atomic_write
    def link_capture(self,scope,seq,title):
        if type(seq) is not int or seq<1 or not 1<=len(title)<=80 or any(c in title for c in '\n\r【】'):
            raise ValueError('invalid event or capture id')
        db=self.flow.db
        row=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
        if not row:raise ValueError('capture unavailable')
        if row['state']=='linked':
            if row['linked_title']!=title:raise ValueError('already linked to another event')
            return row['linked_source']
        if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
            raise ValueError('capture integrity failed')
        source='captured:'+row['event_hash']
        prior=self.flow.writer.status(scope,source)
        if prior and prior['state']=='approved_link':
            event=db.execute('SELECT title FROM events WHERE scope=? AND id=?',
                             (scope,prior['candidate_event_id'])).fetchone()
            if not event or event['title']!=title:raise ValueError('link recovery mismatch')
            message_id=prior['message_id']
        else:
            matches=self.flow.writer._event_by_title(scope,title)
            if len(matches)>1:raise ValueError('ambiguous title')
            event_id=matches[0]['id'] if matches else self.flow.core.create_event(scope,title)
            result=self.flow.ingest(scope=scope,origin=source,speaker='user',kind='user_message',
                                    content=row['content'],recorded_at=row['recorded_at'])
            self.flow.review(scope=scope,origin=source,decision='approve',reviewer='explicit-user-command',
                             event_id=event_id,phase='unknown',reason='explicit user event link')
            message_id=result['message_id']
        with self.flow.core.write_scope():
            db.execute("UPDATE dl_capture SET state='linked',linked_source=?,linked_title=? WHERE scope=? AND seq=?",
                       (message_id,title,scope,seq))
            db.execute('DELETE FROM dl_suggestions WHERE scope=? AND capture_seq=?',(scope,seq))
            db.execute('DELETE FROM dl_new_title_suggestions WHERE scope=? AND capture_seq=?',(scope,seq))
        return message_id

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

    def annotate_date(self,scope,message_id,value):
        """Append the operator's calendar-date assertion; do not edit source."""
        source=self._approved_source(scope,message_id)
        if value=='未知':value=None
        else:
            if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('invalid date')
            try:
                if date.fromisoformat(value).isoformat()!=value:raise ValueError()
            except ValueError:raise ValueError('invalid date') from None
        prior=self.flow.db.execute('SELECT occurred_date,source_sha FROM dl_date_annotations WHERE scope=? AND message_id=? ORDER BY id DESC LIMIT 1',(scope,message_id)).fetchone()
        if prior and prior['occurred_date']==value and prior['source_sha']==source['source_sha256']:return False
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_date_annotations(scope,message_id,occurred_date,source_sha,annotated_at) VALUES(?,?,?,?,?)',
                (scope,message_id,value,source['source_sha256'],datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return True

    def date_history(self,scope,message_id,limit=10):
        source=self._approved_source(scope,message_id)
        return [{'occurred_date':r['occurred_date'],'annotated_at':r['annotated_at'],
                 'source_matches':r['source_sha']==source['source_sha256']} for r in self.flow.db.execute('''SELECT occurred_date,annotated_at,source_sha FROM dl_date_annotations
             WHERE scope=? AND message_id=? ORDER BY id DESC LIMIT ?''',(scope,message_id,limit))]

    def timeline(self,scope,mode='记录',cursor='',limit=10):
        """Page reviewed user sources with a stable key; never infer occurred time."""
        if mode not in ('记录','发生') or type(limit) is not int or not 1<=limit<=10:
            raise ValueError('invalid timeline mode')
        occurred="CASE WHEN a.id IS NOT NULL THEN CASE WHEN a.source_sha=i.source_sha256 THEN a.occurred_date ELSE NULL END ELSE substr(m.occurred_at,1,10) END"
        column='m.recorded_at' if mode=='记录' else occurred
        params=[scope];where=''
        if cursor:
            if not isinstance(cursor,str) or len(cursor)>350:raise ValueError('invalid cursor')
            try:
                raw=base64.urlsafe_b64decode(cursor+'='*((-len(cursor))%4))
                key=json.loads(raw.decode('utf-8'))
                if (not isinstance(key,list) or len(key)!=3 or key[0]!=mode or
                    not all(isinstance(x,str) and 1<=len(x)<=100 for x in key[1:]) or
                    (mode=='发生' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}',key[1]))):raise ValueError()
            except Exception:raise ValueError('invalid cursor') from None
            where=f' AND ({column}<? OR ({column}=? AND m.id<?))'
            params.extend([key[1],key[1],key[2]])
        params.append(limit+1)
        rows=self.flow.db.execute(f'''SELECT m.id,m.content,m.recorded_at,{occurred} AS occurred_at,
             i.source_sha256,e.title,{column} AS sort_time
             FROM messages m JOIN e417_ingest i ON m.scope=i.scope AND m.id=i.message_id
             JOIN e417_phase p ON p.scope=m.scope AND p.message_id=m.id
             JOIN events e ON e.scope=p.scope AND e.id=p.event_id
             LEFT JOIN dl_date_annotations a ON a.id=(SELECT max(d.id) FROM dl_date_annotations d
                 WHERE d.scope=m.scope AND d.message_id=m.id)
             WHERE m.scope=? AND m.speaker='user' AND i.state='approved_link'
               AND {column} IS NOT NULL {where}
             ORDER BY {column} DESC,m.id DESC LIMIT ?''',params).fetchall()
        page=rows[:limit];result=[]
        for row in page:
            valid=hashlib.sha256(row['content'].encode('utf-8')).hexdigest()==row['source_sha256']
            edges=[dict(r) for r in self.flow.db.execute('''SELECT newer,older,kind FROM relations
                WHERE scope=? AND (newer=? OR older=?) ORDER BY newer,older,kind''',(scope,row['id'],row['id']))]
            result.append({'id':row['id'],'title':row['title'],'content':row['content'] if valid else None,
                           'recorded_at':row['recorded_at'],'occurred_at':row['occurred_at'],
                           'integrity':valid,'relations':edges})
        next_cursor=''
        if len(rows)>limit and page:
            key=[mode,page[-1]['sort_time'],page[-1]['id']]
            next_cursor=base64.urlsafe_b64encode(json.dumps(key,ensure_ascii=False,separators=(',',':')).encode()).decode().rstrip('=')
        return {'records':result,'next_cursor':next_cursor,'mode':mode}

    def relate_sources(self,scope,newer_id,older_id,kind):
        """Link two separately reviewed records; neither record is overwritten."""
        if kind not in ('corrects','supersedes','related'):raise ValueError('invalid relation')
        if not isinstance(newer_id,str) or not isinstance(older_id,str) or newer_id==older_id:
            raise ValueError('invalid source ids')
        db=self.flow.db
        def source(mid):
            return db.execute('''SELECT i.origin,i.state,i.candidate_event_id,m.recorded_at
                FROM e417_ingest i JOIN messages m ON m.scope=i.scope AND m.id=i.message_id
                WHERE i.scope=? AND i.message_id=?''',(scope,mid)).fetchone()
        newer,older=source(newer_id),source(older_id)
        if not newer or not older or newer['state']!='approved_link' or older['state']!='approved_link':
            raise ValueError('source missing or not approved')
        if newer['candidate_event_id']!=older['candidate_event_id']:
            raise ValueError('cross-event relation not supported')
        if db.execute('SELECT 1 FROM relations WHERE scope=? AND newer=? AND older=? AND kind=?',
                      (scope,newer_id,older_id,kind)).fetchone():return False
        self.flow.relate(scope=scope,newer_origin=newer['origin'],older_origin=older['origin'],kind=kind,
                         reviewer='explicit-user-command',reason='user explicitly linked existing records')
        return True

    def create_extract_draft(self,scope,title):
        """Store verbatim excerpts, versioned by the event's source fingerprint."""
        found=self.flow.writer._event_by_title(scope,title)
        if len(found)!=1:raise ValueError('event not uniquely found')
        event_id=found[0]['id'];snap=self.flow._snapshot(scope,event_id)
        if not snap or snap['stale']:raise ValueError('stale or missing source')
        pending=self.flow.db.execute("SELECT count(*) FROM e417_ingest WHERE scope=? AND candidate_event_id=? AND state='pending_review'",(scope,event_id)).fetchone()[0]
        if pending:raise ValueError('pending review on event')
        rows=snap['rows'];chosen=rows[-5:]
        records=[{'id':r['id'],'recorded_at':r['recorded_at'],'content':r['content'],
                  'sha256':hashlib.sha256(r['content'].encode('utf-8')).hexdigest()} for r in chosen]
        draft='D-'+uuid.uuid4().hex[:16]
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_extract_drafts VALUES(?,?,?,?,?,?,?)',
                (scope,draft,event_id,snap['digest'],json.dumps(records,ensure_ascii=False),len(rows),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return self.read_extract_draft(scope,draft)

    def read_extract_draft(self,scope,draft_id):
        db=self.flow.db
        row=db.execute('SELECT * FROM dl_extract_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not row:raise ValueError('draft missing')
        snap=self.flow._snapshot(scope,row['event_id'])
        state='CURRENT' if snap and not snap['stale'] and snap['digest']==row['event_fingerprint'] else 'STALE'
        pending=db.execute("SELECT count(*) FROM e417_ingest WHERE scope=? AND candidate_event_id=? AND state='pending_review'",(scope,row['event_id'])).fetchone()[0]
        if pending:state='PENDING_UPDATE'
        records=json.loads(row['records_json'])
        if any(hashlib.sha256(x['content'].encode('utf-8')).hexdigest()!=x['sha256'] for x in records):state='STALE'
        return {'draft_id':draft_id,'status':state,'created_at':row['created_at'],
                'records':records,'source_total':row['source_total'],
                'partial':len(records)<row['source_total'],'kind':'VERBATIM_EXTRACT_NOT_INFERENCE'}

    def _unresolved_linked_updates(self,scope,snap,relations):
        """提示含变更词但尚无修正类关系的已关联原话，不判定真实语义。"""
        rows=snap['rows']
        if len(rows)>1000:raise ValueError('event too large')
        resolved={edge['newer'] for edge in relations if edge['kind'] in ('corrects','supersedes','retracts')}
        dismissed={r['message_id']:(r['source_sha'],r['recorded_at']) for r in self.flow.db.execute(
            'SELECT message_id,source_sha,recorded_at FROM dl_update_dismissals WHERE scope=? AND event_id=?',
            (scope,snap['id']))}
        result=[];older=None;previous_time=None;previous=None
        for row in rows:
            stamp=datetime.fromisoformat(row['recorded_at'])
            if stamp.utcoffset() is None:raise ValueError('invalid time')
            if previous_time is not None and stamp>previous_time:older=previous
            if (older and _UPDATE_CUES.search(row['content']) and row['id'] not in resolved and
                dismissed.get(row['id'])!=(row['source_sha256'],row['recorded_at'])):
                result.append({'new_id':row['id'],'content':row['content'],'recorded_at':row['recorded_at'],
                               'old_id':older['id'],'old_content':older['content']})
            previous=row;previous_time=stamp
        return result

    def linked_update_clues(self,scope,title,page=1):
        if type(page) is not int or not 1<=page<=10000:raise ValueError('invalid page')
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        relations=self.flow._event_rows(scope,event['id'])[1]
        clues=self._unresolved_linked_updates(scope,snap,relations)
        start=(page-1)*10
        return {'items':clues[start:start+10],'has_more':len(clues)>start+10}

    def dismiss_linked_update(self,scope,title,message_id):
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        relations=self.flow._event_rows(scope,event['id'])[1]
        if not any(x['new_id']==message_id for x in self._unresolved_linked_updates(scope,snap,relations)):
            raise ValueError('unresolved clue not found')
        row=next(r for r in snap['rows'] if r['id']==message_id)
        with self.flow.db:
            self.flow.db.execute('''INSERT OR REPLACE INTO dl_update_dismissals
              (scope,message_id,event_id,source_sha,recorded_at,reviewed_at) VALUES(?,?,?,?,?,?)''',
              (scope,message_id,event['id'],row['source_sha256'],row['recorded_at'],
               datetime.now(timezone.utc).isoformat(timespec='seconds')))

    def _event_date_token(self,scope,event_id):
        """摘要同时绑定相关来源的最新人工日期标注。"""
        rows=[dict(r) for r in self.flow.db.execute('''SELECT a.message_id,a.id,a.occurred_date,a.source_sha,a.annotated_at
            FROM dl_date_annotations a JOIN e417_phase p ON p.scope=a.scope AND p.message_id=a.message_id
            WHERE a.scope=? AND p.event_id=? AND a.id=(SELECT max(d.id) FROM dl_date_annotations d
            WHERE d.scope=a.scope AND d.message_id=a.message_id) ORDER BY a.message_id''',(scope,event_id))]
        return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()

    def model_brief_snapshot(self,scope,title,as_of=None):
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        if not snap or snap['stale'] or not 1<=len(snap['rows'])<=20:raise ValueError('invalid source count')
        cutoff=datetime.fromisoformat(as_of) if as_of else datetime.now(timezone.utc)
        if cutoff.utcoffset() is None:raise ValueError('invalid cutoff')
        for row in snap['rows']:
            t=datetime.fromisoformat(row['recorded_at'])
            if t.utcoffset() is None or t>=cutoff:raise ValueError('source time')
        relations=self.flow._event_rows(scope,event['id'])[1]
        if self._pending_update_hint(scope,title,snapshot=snap) or self._unresolved_linked_updates(scope,snap,relations):
            raise ValueError('pending update')
        dates=self._event_annotated_dates(scope,snap,cutoff)
        # 不截断原话或关系：超过预算直接拒绝，避免把局部冒充完整事件。
        data={'title':title,'sources':[r['content'] for r in snap['rows']],
              'relations':relations,'dates':dates}
        if len(relations)>40 or len(json.dumps(data,ensure_ascii=False))>12000:
            raise ValueError('event too large')
        return {'title':title,'event_id':event['id'],'fingerprint':snap['digest'],
                'date_token':self._event_date_token(scope,event['id']),'as_of':cutoff.isoformat(),
                'sources':[{'id':r['id'],'content':r['content'],'recorded_at':r['recorded_at'],
                            'sha':r['source_sha256']} for r in snap['rows']],
                'relations':relations,'dates':dates}

    def model_segment_snapshot(self,scope,title,as_of=None):
        """完整关系连通组不拆分，形成有界分段；任何内容超限都拒绝。"""
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        if not snap or snap['stale'] or not 1<=len(snap['rows'])<=100:raise ValueError('source count')
        cutoff=datetime.fromisoformat(as_of) if as_of else datetime.now(timezone.utc)
        if cutoff.utcoffset() is None:raise ValueError('time')
        for r in snap['rows']:
            t=datetime.fromisoformat(r['recorded_at'])
            if t.utcoffset() is None or t>=cutoff:raise ValueError('source time')
        relations=self.flow._event_rows(scope,event['id'])[1]
        if len(relations)>200:raise ValueError('too many relationships')
        if self._pending_update_hint(scope,title,snapshot=snap) or self._unresolved_linked_updates(scope,snap,relations):
            raise ValueError('pending update')
        dates=self._event_annotated_dates(scope,snap,cutoff)
        sources=[{'id':r['id'],'content':r['content'],'recorded_at':r['recorded_at'],
                  'sha':r['source_sha256']} for r in snap['rows']]
        ids=[r['id'] for r in sources];parent={x:x for x in ids}
        def find(x):
            while parent[x]!=x:
                parent[x]=parent[parent[x]];x=parent[x]
            return x
        for edge in relations:
            a,b=edge['newer'],edge['older']
            if a not in parent or b not in parent:raise ValueError('invalid relationship')
            parent[find(a)]=find(b)
        groups={}
        for mid in ids:groups.setdefault(find(mid),[]).append(mid)
        segments=[];current=[]
        def fits(part):
            chosen=set(part)
            sub={'title':title,'sources':[x for x in sources if x['id'] in chosen],
                 'relations':[x for x in relations if x['newer'] in chosen and x['older'] in chosen],
                 'dates':{k:v for k,v in dates.items() if k in chosen}}
            return len(part)<=20 and len(sub['relations'])<=40 and len(make_brief_prompt(sub))<=14000
        for group in groups.values():
            if not fits(group):raise ValueError('relationship group too large')
            if current and not fits(current+group):segments.append(current);current=[]
            current.extend(group)
        if current:segments.append(current)
        if len(segments)>6:raise ValueError('too many segments')
        return {'strategy':'segmented_v1','title':title,'event_id':event['id'],
                'fingerprint':snap['digest'],'date_token':self._event_date_token(scope,event['id']),
                'as_of':cutoff.isoformat(),'sources':sources,'relations':relations,'dates':dates,'segments':segments}

    @staticmethod
    def model_segment_part(snapshot,part_no):
        if type(part_no) is not int or not 0<=part_no<len(snapshot['segments']):raise ValueError('part')
        ids=set(snapshot['segments'][part_no])
        return {'title':snapshot['title'],'sources':[x for x in snapshot['sources'] if x['id'] in ids],
                'relations':[x for x in snapshot['relations'] if x['newer'] in ids and x['older'] in ids],
                'dates':{k:v for k,v in snapshot['dates'].items() if k in ids}}

    @staticmethod
    def _segment_cache_key(snapshot,part,provider):
        if not isinstance(provider,str) or not provider.strip():raise ValueError('provider')
        # 摘要依据须与当前完整模型提示、事件和提供商一致。
        payload=json.dumps(['segment-cache-v1',provider,snapshot['event_id'],make_brief_prompt(part)],ensure_ascii=False)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def read_segment_cache(self,scope,snapshot,part_no,provider):
        if self.model_segment_snapshot(scope,snapshot['title'],snapshot['as_of'])!=snapshot:raise ValueError('changed')
        part=self.model_segment_part(snapshot,part_no);key=self._segment_cache_key(snapshot,part,provider)
        row=self.flow.db.execute('SELECT result_json,created_at FROM dl_segment_cache WHERE scope=? AND cache_key=?',
                                 (scope,key)).fetchone()
        if not row:return None
        try:
            stamp=datetime.fromisoformat(row['created_at']);age=datetime.now(timezone.utc)-stamp
            if stamp.utcoffset() is None or age<timedelta(0) or age>timedelta(days=7):return None
            validate_brief_output(row['result_json'],part)
        except (ValueError,TypeError,OverflowError):return None
        return row['result_json']

    def save_segment_cache(self,scope,snapshot,part_no,provider,raw):
        if self.model_segment_snapshot(scope,snapshot['title'],snapshot['as_of'])!=snapshot:raise ValueError('changed')
        part=self.model_segment_part(snapshot,part_no);validate_brief_output(raw,part)
        key=self._segment_cache_key(snapshot,part,provider)
        with self.flow.db:
            self.flow.db.execute('INSERT OR REPLACE INTO dl_segment_cache VALUES(?,?,?,?)',
                (scope,key,raw,datetime.now(timezone.utc).isoformat()))
            self.flow.db.execute('''DELETE FROM dl_segment_cache WHERE scope=? AND cache_key NOT IN
                (SELECT cache_key FROM dl_segment_cache WHERE scope=? ORDER BY created_at DESC,cache_key DESC LIMIT 128)''',
                (scope,scope))

    def segment_cache_count(self,scope):
        return self.flow.db.execute('SELECT count(*) FROM dl_segment_cache WHERE scope=?',(scope,)).fetchone()[0]

    def clear_segment_cache(self,scope):
        with self.flow.db:
            cursor=self.flow.db.execute('DELETE FROM dl_segment_cache WHERE scope=?',(scope,))
        return cursor.rowcount

    def _check_segment_results(self,snapshot,raw_parts,raw_final):
        if not isinstance(raw_parts,list) or len(raw_parts)!=len(snapshot['segments']):raise ValueError('incomplete')
        results=[validate_brief_output(raw,self.model_segment_part(snapshot,i)) for i,raw in enumerate(raw_parts)]
        final=validate_brief_output(raw_final,snapshot)
        if not any(final['source_id']==r['source_id'] and final['quote']==r['quote'] for r in results):
            raise ValueError('final quote must match a segment citation')
        return results,final

    def save_segmented_brief_draft(self,scope,snapshot,raw_parts,raw_final,reused=None):
        if self.model_segment_snapshot(scope,snapshot['title'],snapshot['as_of'])!=snapshot:raise ValueError('changed')
        parts,result=self._check_segment_results(snapshot,raw_parts,raw_final);draft='BS-'+uuid.uuid4().hex[:16]
        if reused is None:reused=[False]*len(parts)
        if len(reused)!=len(parts) or any(type(x) is not bool for x in reused):raise ValueError('invalid cache flags')
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_brief_model_drafts VALUES(?,?,?,?,?,NULL)',
                (scope,draft,json.dumps(snapshot,ensure_ascii=False),json.dumps(result,ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
            for i,item in enumerate(parts):
                self.flow.db.execute('INSERT INTO dl_brief_segment_results (scope,draft_id,part_no,result_json,cache_reused) VALUES(?,?,?,?,?)',
                                     (scope,draft,i,json.dumps(item,ensure_ascii=False),int(reused[i])))
        return self.read_model_brief_draft(scope,draft)

    def save_model_brief_draft(self,scope,snapshot,raw):
        current=self.model_brief_snapshot(scope,snapshot['title'],snapshot['as_of'])
        if current!=snapshot:raise ValueError('snapshot changed')
        result=validate_brief_output(raw,current);draft='BS-'+uuid.uuid4().hex[:16]
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_brief_model_drafts VALUES(?,?,?,?,?,NULL)',
                (scope,draft,json.dumps(current,ensure_ascii=False),json.dumps(result,ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return self.read_model_brief_draft(scope,draft)

    def read_model_brief_draft(self,scope,draft_id):
        row=self.flow.db.execute('SELECT * FROM dl_brief_model_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not row:raise ValueError('draft missing')
        saved=json.loads(row['snapshot_json']);result=json.loads(row['result_json'])
        try:
            if saved.get('strategy')=='segmented_v1':
                current=self.model_segment_snapshot(scope,saved['title'],saved['as_of'])
                rows=list(self.flow.db.execute(
                    'SELECT result_json,cache_reused FROM dl_brief_segment_results WHERE scope=? AND draft_id=? ORDER BY part_no',
                    (scope,draft_id)))
                raw_parts=[r['result_json'] for r in rows]
                self._check_segment_results(saved,raw_parts,json.dumps(result,ensure_ascii=False))
                segments=[json.loads(x) for x in raw_parts];reused=[bool(r['cache_reused']) for r in rows]
            else:
                current=self.model_brief_snapshot(scope,saved['title'],saved['as_of']);segments=[];reused=[]
                validate_brief_output(json.dumps(result,ensure_ascii=False),current)
            valid=current==saved
        except (ValueError,TypeError,KeyError):valid=False;segments=[];reused=[]
        return {'draft_id':draft_id,'snapshot':saved,'result':result,'current':valid,'segment_results':segments,'segment_reused':reused,
                'accepted_brief_id':row['accepted_brief_id']}

    def confirm_model_brief_draft(self,scope,draft_id):
        draft=self.read_model_brief_draft(scope,draft_id)
        if not draft['current']:raise ValueError('stale draft')
        if draft['accepted_brief_id'] is not None:return draft['accepted_brief_id']
        snap=draft['snapshot'];result=draft['result']
        source=next(x for x in snap['sources'] if x['id']==result['source_id'])
        with self.flow.db:
            cursor=self.flow.db.execute('''INSERT INTO dl_source_briefs
              (scope,event_id,source_id,source_sha,source_fingerprint,date_token,excerpt,brief,created_at,origin)
              VALUES(?,?,?,?,?,?,?,?,?,?)''',(scope,snap['event_id'],source['id'],source['sha'],snap['fingerprint'],
                snap['date_token'],result['quote'],result['summary'],datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'model_reviewed'))
            brief_id=cursor.lastrowid
            self.flow.db.execute('UPDATE dl_brief_model_drafts SET accepted_brief_id=? WHERE scope=? AND draft_id=?',
                                 (brief_id,scope,draft_id))
        return brief_id

    def save_source_brief(self,scope,title,source_id,excerpt,brief):
        """人工编写的派生摘要，绑定事件及原文，不作为事实校验。"""
        if (not isinstance(excerpt,str) or not 2<=len(excerpt)<=120 or
            not isinstance(brief,str) or not 1<=len(brief)<=180 or
            not brief.strip() or '\x00' in excerpt or '\x00' in brief):
            raise ValueError('invalid brief')
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        source=next((r for r in snap['rows'] if r['id']==source_id),None)
        if not source or excerpt not in source['content']:raise ValueError('excerpt not found')
        with self.flow.db:
            cursor=self.flow.db.execute('''INSERT INTO dl_source_briefs
              (scope,event_id,source_id,source_sha,source_fingerprint,date_token,excerpt,brief,created_at,origin)
              VALUES(?,?,?,?,?,?,?,?,?,?)''',(scope,event['id'],source_id,source['source_sha256'],snap['digest'],
                    self._event_date_token(scope,event['id']),excerpt,brief,datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    'human_written'))
        return cursor.lastrowid

    def read_source_brief(self,scope,title):
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        row=self.flow.db.execute('''SELECT * FROM dl_source_briefs WHERE scope=? AND event_id=?
                                  ORDER BY id DESC LIMIT 1''',(scope,event['id'])).fetchone()
        if not row:return None
        source=next((r for r in snap['rows'] if r['id']==row['source_id']),None)
        valid=bool(source and row['source_sha']==source['source_sha256'] and row['excerpt'] in source['content']
                   and row['source_fingerprint']==snap['digest']
                   and row['date_token']==self._event_date_token(scope,event['id']))
        return {'id':row['id'],'brief':row['brief'],'excerpt':row['excerpt'],'source_id':row['source_id'],
                'source_time':source['recorded_at'] if source else None,'created_at':row['created_at'],
                'current':valid,'origin':row['origin']}

    def _event_annotated_dates(self,scope,snap,cutoff):
        """只返回在请求时点可用、且与已审核原话一致的人工日期标注。"""
        known={row['id']:row['source_sha256'] for row in snap['rows']};out={}
        rows=self.flow.db.execute('''SELECT a.message_id,a.occurred_date,a.source_sha,a.annotated_at
            FROM dl_date_annotations a JOIN e417_phase p ON p.scope=a.scope AND p.message_id=a.message_id
            WHERE a.scope=? AND p.event_id=? AND a.id=(SELECT max(d.id) FROM dl_date_annotations d
                WHERE d.scope=a.scope AND d.message_id=a.message_id)''',(scope,snap['id']))
        for row in rows:
            try:
                annotated=datetime.fromisoformat(row['annotated_at'])
                if annotated.utcoffset() is None or annotated>=cutoff:continue
                if row['message_id'] not in known or row['source_sha']!=known[row['message_id']]:continue
                value=row['occurred_date']
                if value is not None and date.fromisoformat(value).isoformat()==value:out[row['message_id']]=value
            except (ValueError,TypeError,OverflowError):continue
        return out

    def _context_event(self,scope,event,question,budget,*,focus_ids=None,cutoff=None,current_message_id=None,minimum_only=False):
        plan=self._prepare_context_event(scope,event,question,focus_ids=focus_ids,cutoff=cutoff,
                                         current_message_id=current_message_id)
        return plan(budget,minimum_only) if plan else ''

    def _prepare_context_event(self,scope,event,question,*,focus_ids=None,cutoff=None,current_message_id=None):
        """单次请求内准备来源、关系与时间边界，返回可重复分配预算的临时函数。"""
        snap=self.flow._snapshot(scope,event['id'])
        if not snap or snap['stale'] or len(snap['rows'])>1000:return None
        # 当前消息或将来的记录不能作为已发生的历史；关系组也不能被拆开。
        if cutoff is None:cutoff=datetime.now(timezone.utc)
        try:
            if cutoff.utcoffset() is None:return None
            if any((datetime.fromisoformat(r['recorded_at']).utcoffset() is None or
                    datetime.fromisoformat(r['recorded_at'])>=cutoff) for r in snap['rows']):return None
        except (TypeError,ValueError,OverflowError):return None
        if isinstance(current_message_id,str) and 1<=len(current_message_id)<=256:
            event_hash=hashlib.sha256((scope+'\0'+current_message_id).encode('utf-8')).hexdigest()
            source=self.flow.writer.status(scope,'captured:'+event_hash)
            if source and any(r['id']==source['message_id'] for r in snap['rows']):return None
        pending=self.flow.db.execute("SELECT 1 FROM e417_ingest WHERE scope=? AND candidate_event_id=? AND state='pending_review' LIMIT 1",(scope,event['id'])).fetchone()
        if pending:return None
        rows=snap['rows'];relations=self.flow._event_rows(scope,event['id'])[1]
        pending_hint=self._pending_update_hint(scope,event['title'],snapshot=snap)
        try:linked_update_hint=bool(self._unresolved_linked_updates(scope,snap,relations))
        except (TypeError,ValueError,OverflowError):return None
        by_id={r['id']:r for r in rows};parent={k:k for k in by_id}
        def root(x):
            while parent[x]!=x:
                parent[x]=parent[parent[x]];x=parent[x]
            return x
        for edge in relations:
            if edge['newer'] not in parent or edge['older'] not in parent:return None
            a,b=root(edge['newer']),root(edge['older'])
            if a!=b:parent[a]=b
        groups={}
        for row in rows:groups.setdefault(root(row['id']),[]).append(row)
        earliest=bool(re.search(r'最初|最早|起初|原本|一开始|原计划',question))
        ordered=sorted(groups.values(),key=lambda g:(g[0]['recorded_at'],g[0]['id']) if earliest else
                       (g[-1]['recorded_at'],g[-1]['id']),reverse=not earliest)
        detail_ranked=False
        if self.detail_priority_enabled and not focus_ids and not earliest and not re.search(r'不是|并非|不对|记错|错了|别说|不指',question):
            # 去除事件名称与别名后，只把问题中有辨识度的三字重合用于组级排序。
            detail=question.replace(event['title'],' ')
            for alias in self.list_aliases(scope,event['title']):detail=detail.replace(alias,' ')
            generic={'怎么样','是什么','有没有','多少钱','什么时候','为什么','如何','哪里有','是不是','记不记','还记得'}
            grams={detail[i:i+3] for i in range(len(detail)-2)
                   if all('\u4e00'<=c<='\u9fff' for c in detail[i:i+3])
                   and detail[i:i+3] not in generic and len(self._character_pairs(detail[i:i+3]))>=2}
            group_hits=[]
            for group in ordered:
                hits={term for term in grams if any(term in row['content'] for row in group)}
                group_hits.append(hits)
            frequencies=Counter(term for hits in group_hits for term in hits)
            scores={id(group):sum(1.0/frequencies[term] for term in hits)
                    for group,hits in zip(ordered,group_hits)}
            if any(scores.values()):
                ordered.sort(key=lambda group:-scores[id(group)])
                detail_ranked=True
        if focus_ids:
            # Stable sort keeps the question's temporal order inside each tier.
            ordered.sort(key=lambda g:not any(r['id'] in focus_ids for r in g))
        selected_dates={};target_date=None
        if self.occurred_date_context_enabled:
            # 仅支持有完整年月日的查询；无法判定时不把其他日期当作答案。
            dates=list(re.finditer(r'(?<!\d)(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?!\d)',question))
            prefixes=list(re.finditer(r'(?<!\d)\d{4}[-/年]\d{1,2}',question))
            if prefixes:
                if len(dates)!=len(prefixes) or re.search(r'不是|并非|不对|记错|错了|别说|不指',question):return None
                try:targets={date(*(int(part) for part in match.groups())).isoformat() for match in dates}
                except ValueError:return None
                if len(targets)!=1:return None
                target_date=next(iter(targets))
                selected_dates=self._event_annotated_dates(scope,snap,cutoff)
                ordered=[g for g in ordered if any(selected_dates.get(r['id'])==target_date for r in g)]
                if focus_ids:ordered=[g for g in ordered if any(r['id'] in focus_ids for r in g)]
                if not ordered:return None
        kinds={'corrects':'修正','supersedes':'替代','related':'相关','retracts':'撤回'}
        imported_ids={r['linked_source'] for r in self.flow.db.execute('''SELECT c.linked_source FROM dl_capture c
            JOIN dl_import_provenance p ON p.scope=c.scope AND p.seq=c.seq
            JOIN e417_phase ph ON ph.scope=c.scope AND ph.message_id=c.linked_source
            WHERE c.scope=? AND ph.event_id=?''',(scope,event['id']))}
        def plan(budget,minimum_only=False):
            def render(selected,compact=False):
                ids={r['id'] for r in selected}
                chronological=[r for r in rows if r['id'] in ids]
                index={r['id']:i+1 for i,r in enumerate(chronological)}
                payload={'事件':event['title'],'说明':'用户历史原话及人工关系，不等于已证实现况',
                         '覆盖':f'{len(chronological)}/{len(rows)}条，可能遗漏背景',
                         '原话':[{'编号':index[r['id']],'记录时间':r['recorded_at'],'内容':r['content']} for r in chronological],
                         '关系':[{'较新':index[x['newer']],'类型':kinds.get(x['kind'],'未知'),'较旧':index[x['older']]}
                               for x in relations if x['newer'] in ids and x['older'] in ids]}
                if compact:
                    # 精简字段名称，保留每条完整原话、记录时间及全部组内关系。
                    payload['说明']='原话项=[记录时间,全文]，序号从1起；关系项=[新序号,类型,旧序号]。历史陈述不等于现况'
                    payload['原话']=[[r['recorded_at'],r['content']] for r in chronological]
                    payload['关系']=[[index[x['newer']],kinds.get(x['kind'],'未知'),index[x['older']]]
                                   for x in relations if x['newer'] in ids and x['older'] in ids]
                imported_numbers=[index[r['id']] for r in chronological if r['id'] in imported_ids]
                if imported_numbers:payload['导入时间说明']={'原话序号':imported_numbers,'说明':'记录时间由导入文件提供，未经独立核验'}
                if target_date:
                    payload['日期标注']={'说明':'日期由人工标注，不等于客观事实；其他记录日期未提供',
                                     '条目':[[index[r['id']],selected_dates[r['id']]] for r in chronological if r['id'] in selected_dates]}
                if detail_ranked:payload['选择依据']='按问题与原文的字面重合优先，可能漏掉相关背景'
                if linked_update_hint:payload['关系提醒']='已关联原话中可能有更新尚未核对，旧内容不一定反映现况'
                if pending_hint:payload['待审提醒']='有可能相关的新消息尚未审核，旧记录不一定反映现况'
                return json.dumps(payload,ensure_ascii=False,separators=(',',':'))
            def choose(compact):
                selected=[]
                for position,group in enumerate(ordered):
                    proposal=selected+group
                    if len(render(proposal,compact))<=budget:
                        selected=proposal
                        if minimum_only:break
                    elif position==0:return [],''  # 不以其他记录冒充优先组。
                return (selected,render(selected,compact)) if selected else ([], '')
            selected,output=choose(False)
            if self.compact_context_enabled:
                compact_selected,compact_output=choose(True)
                if (len(compact_selected)>len(selected) or
                    (minimum_only and compact_output and (not output or len(compact_output)<len(output)))):
                    selected,output=compact_selected,compact_output
            # 最小预留只包含第一优先关系组，不使用摘要替代原话。
            if minimum_only:return output
            # 默认依旧优先原话。显式开启概览优先时，只有未覆盖全部原话才比较摘要。
            if output and (not self.brief_overview_priority_enabled or len(selected)==len(rows)):return output
            # 仅在原话无法放入时考虑摘要；明确指向事件，且没有检测到可能的更新。
            if (not self.reviewed_brief_context_enabled or focus_ids or target_date or pending_hint or linked_update_hint or
                re.search(r'最初|最早|起初|原本|以前|之前|后来|现在|最近|过去|历史|当时|何时|时候|哪天|几月|几号|不是|并非|不对|记错|错了|\d{4}',question)):
                return output
            names=self._named_events(scope,question,[event])
            if not names:return output
            # 摘要没有细节完整性保证，只供事件整体的有限提问。
            remainder=question.replace(event['title'],'')
            for alias in self.list_aliases(scope,event['title']):remainder=remainder.replace(alias,'')
            remainder=re.sub(r'[\s，,。！？?!：:]+','',remainder)
            if not re.fullmatch(r'(?:|是什么|怎么样|怎样|如何|的情况|什么情况|说过什么|聊过什么|介绍一下|讲讲|帮我回忆一下)',remainder):return output
            try:
                summary=self.read_source_brief(scope,event['title'])
                created=datetime.fromisoformat(summary['created_at']) if summary else None
                if not summary or not summary['current'] or created.utcoffset() is None or created>=cutoff:return output
            except (ValueError,TypeError,OverflowError):return output
            origin={'human_written':'人工编写','model_reviewed':'模型生成、人工确认',
                    'legacy_unknown':'旧摘要，生成方式未记录'}.get(summary['origin'],'生成方式未知')
            field='人工摘要' if summary['origin']=='human_written' else '摘要'
            packet={'事件':event['title'],'说明':origin+'摘要，可能遗漏或误解，并非完整历史或客观事实',
                    field:summary['brief'],'依据摘录':summary['excerpt'],
                    '依据记录时间':summary['source_time'],'覆盖':'未包含完整历史，请勿推断遗漏内容'}
            if self.flow.db.execute('''SELECT 1 FROM dl_capture c JOIN dl_import_provenance p
                ON p.scope=c.scope AND p.seq=c.seq WHERE c.scope=? AND c.linked_source=? LIMIT 1''',
                (scope,summary['source_id'])).fetchone():
                packet['依据时间来源']='来自导入文件，未经独立核验'
            result=json.dumps(packet,ensure_ascii=False,separators=(',',':'))
            return result if len(result)<=budget and (not output or len(result)<len(output)) else output

        return plan

    def _semantic_auto_groups(self,scope,query,current_hash,excluded,cutoff,after_gap):
        """对近期自动组完整来源编码；有错误则放弃整次语义选择。"""
        if not self.auto_group_semantic_enabled or not self.model_dir:return {}
        db=self.flow.db
        try:
            limit=datetime.fromisoformat(cutoff)
            boundary=datetime.fromisoformat(after_gap) if after_gap else None
            if limit.utcoffset() is None or (boundary and boundary.utcoffset() is None):return {}
            from .auto_organize import read_group
            idx=self._index();encoder=idx.embedder
            if getattr(encoder,'model_key',None)!=idx.encoder_key:return {}
            if self.automatic_memory_enabled:
                self.sync_auto_source_vectors(scope,limit=20)
                missing=db.execute('''SELECT 1 FROM dl_capture c WHERE c.scope=? AND c.state='pending'
                    AND c.event_hash<>? AND NOT EXISTS (SELECT 1 FROM dl_auto_source_vectors v
                    WHERE v.scope=c.scope AND v.seq=c.seq AND v.model_key=? AND v.content_sha=c.content_sha) LIMIT 1''',
                    (scope,current_hash,idx.model_key)).fetchone()
                if missing:return {}
            groups={}
            sql="SELECT seq FROM dl_capture WHERE scope=? AND state='pending' ORDER BY seq DESC"
            if not self.automatic_memory_enabled:sql+=' LIMIT 60'
            for row in db.execute(sql,(scope,)).fetchall():
                group=read_group(db,scope,row['seq'])
                if not group:continue
                key=tuple(group['members'])
                if key in groups:continue
                texts=[];valid=True
                for seq in key:
                    source=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
                    if not source or 'pending:'+str(seq) in excluded or source['event_hash']==current_hash:
                        valid=False;break
                    stamp=datetime.fromisoformat(source['recorded_at'])
                    if stamp.utcoffset() is None or stamp>=limit or (boundary and stamp<=boundary):valid=False;break
                    if hashlib.sha256(source['content'].encode()).hexdigest()!=source['content_sha']:valid=False;break
                    texts.append(source['content'])
                if valid:groups[key]=(group,texts)
                if len(groups)>1000:return {}
            if not groups:return {}
            texts=[];owners=[]
            for key,(_,sources) in groups.items():
                for source in sources:texts.append(source);owners.append(key)
            if not self.automatic_memory_enabled and len(texts)>100:return {}
            query_vectors=encoder.encode_queries([query])
            if not isinstance(query_vectors,(list,tuple)) or len(query_vectors)!=1:return {}
            q=_unit(query_vectors[0]);scores={}
            if self.automatic_memory_enabled:
                for key in groups:
                    for seq in key:
                        vector=db.execute('''SELECT v.vector_json FROM dl_auto_source_vectors v JOIN dl_capture c
                            ON c.scope=v.scope AND c.seq=v.seq AND c.content_sha=v.content_sha
                            WHERE v.scope=? AND v.seq=? AND v.model_key=?''',(scope,seq,idx.model_key)).fetchone()
                        if not vector:return {}
                        v=_unit(json.loads(vector['vector_json']))
                        if len(v)!=len(q):return {}
                        score=sum(a*b for a,b in zip(q,v));scores[key]=max(scores.get(key,-1.0),score)
            else:
                for start in range(0,len(texts),20):
                    batch=texts[start:start+20];vectors=encoder.encode_documents(batch)
                    if not isinstance(vectors,(list,tuple)) or len(vectors)!=len(batch):return {}
                    for key,vector in zip(owners[start:start+20],vectors):
                        v=_unit(vector)
                        if len(v)!=len(q):return {}
                        score=sum(a*b for a,b in zip(q,v));scores[key]=max(scores.get(key,-1.0),score)
            ranked=sorted(scores.items(),key=lambda x:(-x[1],x[0]))
            if ranked[0][1]<self.source_min_cosine or (len(ranked)>1 and ranked[0][1]-ranked[1][1]<self.source_min_lead):return {}
            key=ranked[0][0]
            # 编码期间可能有其他数据库连接改变来源或关联。
            if not read_group(db,scope,key[0]) or tuple(read_group(db,scope,key[0])['members'])!=key:return {}
            return {key:groups[key][0]}
        except Exception:return {}

    def _auto_source_conflict(self,scope,seq):
        if not self.automatic_memory_enabled or not self.model_dir:return False
        try:
            from .auto_topology import source_has_conflict
            return source_has_conflict(self.flow.db,scope,self._index().model_key,seq)
        except Exception:return True

    def _auto_group_context(self,scope,query,cutoff,budget,current_message_id,excluded,after_gap=None):
        """仅提供唯一来源完好的临时分组；不把自动关联当作确认事实。"""
        if (not self.auto_group_context_enabled or not isinstance(current_message_id,str) or
            not 1<=len(current_message_id)<=256 or re.search(r'不是|并非|别说|不指|不对|记错|认错|错了',query)):
            return ''
        terms={query[i:i+4] for i in range(len(query)-3)
               if all('\u4e00'<=c<='\u9fff' for c in query[i:i+4])}
        if not terms and not self.auto_group_semantic_enabled:return ''
        db=self.flow.db;version=db.execute('PRAGMA data_version').fetchone()[0]
        current_hash=hashlib.sha256((scope+'\0'+current_message_id).encode()).hexdigest()
        # 较早积压可能改变分组；当前消息自身尚未处理不阻塞。
        if db.execute('''SELECT 1 FROM dl_auto_jobs j JOIN dl_capture c ON c.scope=j.scope AND c.seq=j.seq
            WHERE j.scope=? AND j.status='pending' AND c.event_hash<>? LIMIT 1''',(scope,current_hash)).fetchone():return ''
        from .auto_organize import read_group
        matches={}
        for seq in self.pending_index.candidates(scope,self._character_pairs(query)):
            row=db.execute('SELECT content FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
            if not row or not any(t in row['content'] for t in terms):continue
            group=read_group(db,scope,seq)
            if group:matches[tuple(group['members'])]=group
            if len(matches)>1:return ''
        matching='字面重合'
        if not matches:
            matches=self._semantic_auto_groups(scope,query,current_hash,excluded,cutoff,after_gap)
            matching='本地语义相似'
        if len(matches)!=1:return ''
        members=next(iter(matches));limit=datetime.fromisoformat(cutoff);items=[]
        try:
            boundary=datetime.fromisoformat(after_gap) if after_gap else None
            if boundary is not None and boundary.utcoffset() is None:return ''
        except (TypeError,ValueError,OverflowError):return ''
        for seq in members:
            if 'pending:'+str(seq) in excluded:return ''
            if self._auto_source_conflict(scope,seq):return ''
            row=db.execute('''SELECT c.*,p.seq AS imported FROM dl_capture c LEFT JOIN dl_import_provenance p
                ON p.scope=c.scope AND p.seq=c.seq WHERE c.scope=? AND c.seq=?''',(scope,seq)).fetchone()
            if not row or row['event_hash']==current_hash or row['state']!='pending':return ''
            if hashlib.sha256(row['content'].encode()).hexdigest()!=row['content_sha']:return ''
            try:
                timestamp=datetime.fromisoformat(row['recorded_at'])
                if timestamp.utcoffset() is None or timestamp>=limit or (boundary and timestamp<=boundary):return ''
            except (TypeError,ValueError,OverflowError):return ''
            item={'序号':seq,'原话':row['content'],'记录时间':row['recorded_at']}
            if row['imported'] is not None:item['时间来源']='导入文件，未经独立核验'
            items.append(item)
        basis=next(iter(matches.values()))['basis']
        payload={'待审记录序号':members[0],'待审相关序号':list(members),'可能相关原话':items,
                 '线索方式':basis,'定位方式':matching,
                 '说明':'系统自动关联的未确认线索，事件归属及新旧关系均不确定'}
        relation=next(iter(matches.values())).get('relation_hint')
        if relation:payload['关系线索']=relation+'（模型推测，未确认）'
        if self.automatic_memory_enabled and self.model_dir:
            from .auto_model import unresolved_hint_sources
            if unresolved_hint_sources(db,scope,self._index().model_key,members):
                payload['变化核验提示']='曾有相关变化线索现已失效或正在重新定位；新旧关系及当前状态尚不确定'
        if boundary:payload['采集情况']='先前存在已检测到的采集缺口，历史可能不完整；缺失内容无法恢复'
        output=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if db.execute('PRAGMA data_version').fetchone()[0]!=version:return ''
        return output if len(output)<=budget else ''

    def sync_auto_topics(self,limit=20):
        if not self.automatic_memory_enabled or not self.model_dir:return 0
        from .auto_topics import sync_topics,reconcile_topics
        from .auto_topology import recover_model_merges,rebuild_conflicted_topics
        model_key=self._index().model_key
        recover_model_merges(self.flow.db,model_key)
        rebuild_conflicted_topics(self.flow.db,model_key)
        count=sync_topics(self.flow.db,model_key,limit)
        reconcile_topics(self.flow.db,model_key)
        return count

    def _auto_topic_context(self,scope,query,cutoff,budget,current_message_id,excluded,after_gap=None):
        if not self.automatic_memory_enabled or not self.model_dir:return ''
        from .auto_topics import topic_context
        try:
            idx=self._index()
            if getattr(idx.embedder,'model_key',None)!=idx.encoder_key:return ''
            return topic_context(self.flow.db,idx.model_key,idx.embedder,scope,query,cutoff,budget,current_message_id,excluded,after_gap)
        except Exception:return ''

    def sync_auto_source_vectors(self,scope=None,limit=20):
        """增量编码已保存原话；失败记录退避，不更改原话或确认状态。"""
        if not self.automatic_memory_enabled or not self.model_dir:return 0
        idx=self._index();encoder=idx.embedder;db=self.flow.db
        if getattr(encoder,'model_key',None)!=idx.encoder_key:return 0
        now=datetime.now(timezone.utc);stamp=now.isoformat(timespec='seconds')
        rows=db.execute('''SELECT c.scope,c.seq,c.content,c.content_sha FROM dl_capture c
            LEFT JOIN dl_auto_source_vectors v ON v.scope=c.scope AND v.seq=c.seq AND v.model_key=? AND v.content_sha=c.content_sha
            LEFT JOIN dl_auto_vector_failures f ON f.scope=c.scope AND f.seq=c.seq AND f.model_key=? AND f.content_sha=c.content_sha
            WHERE c.state='pending' AND (? IS NULL OR c.scope=?) AND v.seq IS NULL
                AND (f.seq IS NULL OR f.next_at<=?) ORDER BY c.seq LIMIT ?''',
            (idx.model_key,idx.model_key,scope,scope,stamp,limit)).fetchall()
        completed=0
        for start in range(0,len(rows),20):
            batch=rows[start:start+20];valid=[];outcomes={};chunk_counts={}
            for row in batch:
                try:
                    if hashlib.sha256(row['content'].encode()).hexdigest()!=row['content_sha']:raise ValueError('source changed')
                    valid.append(row)
                except (AttributeError,TypeError,ValueError):outcomes[(row['scope'],row['seq'])]=None
            if valid:
                try:
                    encoded=encoder.encode_documents([r['content'] for r in valid])
                    if not isinstance(encoded,(list,tuple)) or len(encoded)!=len(valid):raise ValueError('shape')
                except Exception:
                    # 一条超长或损坏原话不能使同批其他消息失去索引。
                    encoded=[]
                    for row in valid:
                        try:
                            one=encoder.encode_documents([row['content']])
                            encoded.append(one[0] if isinstance(one,(list,tuple)) and len(one)==1 else None)
                        except ValueError:
                            try:
                                if len(row['content'])<=128:raise ValueError('not a long source')
                                from .auto_vector_chunks import encode_complete_source
                                vector,count=encode_complete_source(encoder,row['content'])
                                encoded.append(vector);chunk_counts[(row['scope'],row['seq'])]=count
                            except Exception:encoded.append(None)
                        except Exception:encoded.append(None)
                for row,raw in zip(valid,encoded):
                    try:outcomes[(row['scope'],row['seq'])]=_unit(raw)
                    except Exception:outcomes[(row['scope'],row['seq'])]=None
            with db:
                for row in batch:
                    current=db.execute('SELECT content,content_sha,state FROM dl_capture WHERE scope=? AND seq=?',(row['scope'],row['seq'])).fetchone()
                    if not current or current['state']!='pending' or current['content']!=row['content'] or current['content_sha']!=row['content_sha']:continue
                    vector=outcomes[(row['scope'],row['seq'])]
                    if vector is not None:
                        db.execute('INSERT OR REPLACE INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',
                                   (row['scope'],row['seq'],idx.model_key,row['content_sha'],json.dumps(vector,separators=(',',':'))))
                        count=chunk_counts.get((row['scope'],row['seq']))
                        if count:
                            db.execute('INSERT OR REPLACE INTO dl_auto_vector_chunks VALUES(?,?,?,?,?)',
                                       (row['scope'],row['seq'],idx.model_key,row['content_sha'],count))
                        else:db.execute('DELETE FROM dl_auto_vector_chunks WHERE scope=? AND seq=? AND model_key=?',
                                        (row['scope'],row['seq'],idx.model_key))
                        db.execute('DELETE FROM dl_auto_vector_failures WHERE scope=? AND seq=? AND model_key=?',(row['scope'],row['seq'],idx.model_key))
                        completed+=1
                    else:
                        failure=db.execute('SELECT attempts FROM dl_auto_vector_failures WHERE scope=? AND seq=? AND model_key=?',
                                           (row['scope'],row['seq'],idx.model_key)).fetchone()
                        attempts=(failure['attempts'] if failure else 0)+1
                        retry=(now+timedelta(seconds=min(300,2**min(attempts,8)))).isoformat(timespec='seconds')
                        db.execute('INSERT OR REPLACE INTO dl_auto_vector_failures VALUES(?,?,?,?,?,?)',
                                   (row['scope'],row['seq'],idx.model_key,row['content_sha'],attempts,retry))
        return completed

    def _semantic_unlinked_context(self,scope,query,cutoff,budget,current_message_id,excluded,after_gap=None):
        """自动模式下让单条未归组原话也能参与本地语义召回。"""
        if (not self.automatic_memory_enabled or not self.model_dir or not isinstance(current_message_id,str) or
            not 1<=len(current_message_id)<=256 or re.search(r'不是|并非|别说|不指|不对|记错|认错|错了',query)):return ''
        db=self.flow.db;version=db.execute('PRAGMA data_version').fetchone()[0]
        current_hash=hashlib.sha256((scope+'\0'+current_message_id).encode()).hexdigest()
        if db.execute('''SELECT 1 FROM dl_auto_jobs j JOIN dl_capture c ON c.scope=j.scope AND c.seq=j.seq
            WHERE j.scope=? AND j.status='pending' AND c.event_hash<>? LIMIT 1''',(scope,current_hash)).fetchone():return ''
        try:
            limit=datetime.fromisoformat(cutoff);boundary=datetime.fromisoformat(after_gap) if after_gap else None
            if limit.utcoffset() is None or (boundary and boundary.utcoffset() is None):return ''
            from .auto_organize import read_group
            idx=self._index();encoder=idx.embedder
            if getattr(encoder,'model_key',None)!=idx.encoder_key:return ''
            # 小规模新库可以立即建立索引；大量历史由后台分批补齐。
            self.sync_auto_source_vectors(scope,limit=20)
            missing=db.execute('''SELECT 1 FROM dl_capture c WHERE c.scope=? AND c.state='pending'
                AND c.event_hash<>? AND NOT EXISTS (SELECT 1 FROM dl_auto_source_vectors v
                WHERE v.scope=c.scope AND v.seq=c.seq AND v.model_key=? AND v.content_sha=c.content_sha) LIMIT 1''',
                (scope,current_hash,idx.model_key)).fetchone()
            if missing:return ''
            # 只保留得分最高的两个来源；仍检查全部合格候选以保留歧义保护。
            best=None;second=None;q=None
            for row in db.execute('''WITH ordered AS (
                SELECT c.*,LEAD(content) OVER (ORDER BY seq) AS next_content,
                    LEAD(recorded_at) OVER (ORDER BY seq) AS next_time
                FROM dl_capture c WHERE c.scope=?
                ) SELECT c.*,v.vector_json,m.topic_id AS auto_topic,
                    (g.seq IS NOT NULL OR ch.seq IS NOT NULL OR p.seq IS NOT NULL) AS may_have_group,
                    EXISTS(SELECT 1 FROM dl_auto_topic_members n WHERE n.scope=m.scope
                        AND n.topic_id=m.topic_id AND n.model_key=m.model_key AND n.seq<>m.seq) AS has_topic_peer
                FROM ordered c JOIN dl_auto_source_vectors v
                ON v.scope=c.scope AND v.seq=c.seq AND v.model_key=? AND v.content_sha=c.content_sha
                LEFT JOIN dl_auto_topic_members m ON m.scope=c.scope AND m.seq=c.seq AND m.model_key=v.model_key
                LEFT JOIN dl_auto_groups g ON g.scope=c.scope AND g.seq=c.seq
                LEFT JOIN dl_auto_chains ch ON ch.scope=c.scope AND ch.seq=c.seq
                LEFT JOIN dl_auto_model_pairs p ON p.scope=c.scope AND p.seq=c.seq
                WHERE c.state='pending' ORDER BY c.seq''',(scope,idx.model_key)):
                if ('pending:'+str(row['seq']) in excluded or row['event_hash']==current_hash or
                    row['content'].strip()==query.strip() or _UPDATE_CUES.search(row['content'])):continue
                # 只有存在分组记录时才执行完整来源校验，失效分组仍可回退单条召回。
                if row['may_have_group'] and read_group(db,scope,row['seq']):continue
                if row['has_topic_peer']:continue
                # 没有事件归属时不可能存在事件内冲突；有归属的单条仍需校验。
                if row['auto_topic'] is not None and self._auto_source_conflict(scope,row['seq']):continue
                if hashlib.sha256(row['content'].encode()).hexdigest()!=row['content_sha']:continue
                stamp=datetime.fromisoformat(row['recorded_at'])
                if stamp.utcoffset() is None or stamp>=limit or (boundary and stamp<=boundary):continue
                if q is None:
                    vectors=encoder.encode_queries([query])
                    if not isinstance(vectors,(list,tuple)) or len(vectors)!=1:return ''
                    q=_unit(vectors[0])
                v=_unit(json.loads(row['vector_json']))
                if len(v)!=len(q):return ''
                item=(sum(a*b for a,b in zip(q,v)),row)
                if best is None or item[0]>best[0]:second=best;best=item
                elif second is None or item[0]>second[0]:second=item
            if best is None or best[0]<self.source_min_cosine or (second is not None and best[0]-second[0]<self.source_min_lead):return ''
            row=best[1]
            payload={'待审记录序号':row['seq'],'未整理历史原话':row['content'],'记录时间':row['recorded_at'],
                     '定位方式':'本地语义相似','说明':'自动选择的历史原话，仅是未确认线索，可能无关或过时'}
            peer=self._possible_update_peer(scope,row)
            if peer:
                payload['后续可能更新']={'序号':peer['seq'],'原话':peer['content'],'记录时间':peer['recorded_at']}
                payload['说明']='自动选择的历史原话，其后一条可能是更新；新旧关系与当前状态均未确认'
            from .auto_vector_chunks import add_chunk_notice
            add_chunk_notice(db,scope,idx.model_key,[row['seq']],payload)
            from .auto_model import unresolved_hint_sources
            if unresolved_hint_sources(db,scope,idx.model_key,{row['seq']}):
                payload['变化核验提示']='曾有相关变化线索现已失效或正在重新定位；新旧关系及当前状态尚不确定'
            if db.execute('SELECT 1 FROM dl_import_provenance WHERE scope=? AND seq=?',(scope,row['seq'])).fetchone():
                payload['时间来源']='导入文件，未经独立核验'
            if boundary:payload['采集情况']='先前存在已检测到的采集缺口，历史可能不完整；缺失内容无法恢复'
            output=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
            if db.execute('PRAGMA data_version').fetchone()[0]!=version:return ''
            return output if len(output)<=budget else ''
        except Exception:return ''

    def _possible_update_peer(self,scope,row,window=180,max_chars=40):
        """相邻 180 秒内、含变化词且未处理的短句，视为该原话的“可能更新”（仅线索）。"""
        try: stamp=datetime.fromisoformat(row['recorded_at'])
        except (TypeError,ValueError): return None
        if stamp.tzinfo is None: return None
        nxt=self.flow.db.execute('''SELECT seq,content,content_sha,recorded_at,state FROM dl_capture
            WHERE scope=? AND seq>? ORDER BY seq LIMIT 1''',(scope,row['seq'])).fetchone()
        if not nxt or nxt['state']!='pending': return None
        text=nxt['content'].strip()
        if not text or len(text)>max_chars or not _UPDATE_CUES.search(text): return None
        try: t=datetime.fromisoformat(nxt['recorded_at'])
        except (TypeError,ValueError): return None
        if t.tzinfo is None or not 0<=(t-stamp).total_seconds()<=window: return None
        if hashlib.sha256(nxt['content'].encode('utf-8')).hexdigest()!=nxt['content_sha']: return None
        return nxt

    def _pending_context(self,scope,query,cutoff,budget,current_message_id,excluded,after_gap=None):
        """Recall one line or a bounded possible update chain; never the current message."""
        if (not self.pending_context_enabled or not isinstance(current_message_id,str) or
            not 1<=len(current_message_id)<=256 or re.search(r'不是|并非|别说|不指|不对|记错|认错|错了',query)):
            return ''
        phrases={query[i:i+4] for i in range(len(query)-3)
                 if all('\u4e00'<=c<='\u9fff' for c in query[i:i+4])
                 and len(self._character_pairs(query[i:i+4]))>=2}
        if not phrases:return ''
        current_hash=hashlib.sha256((scope+'\0'+current_message_id).encode('utf-8')).hexdigest()
        try:
            limit=datetime.fromisoformat(cutoff)
            boundary=datetime.fromisoformat(after_gap) if after_gap else None
            if limit.utcoffset() is None or (boundary and boundary.utcoffset() is None):return ''
        except (TypeError,ValueError,OverflowError):return ''
        version=self.flow.db.execute('PRAGMA data_version').fetchone()[0];found=[]
        for seq in self.pending_index.candidates(scope,self._character_pairs(query)):
            if 'pending:'+str(seq) in excluded:continue
            if self._auto_source_conflict(scope,seq):continue
            row=self.flow.db.execute('''SELECT c.seq,c.content,c.content_sha,c.recorded_at,c.event_hash,c.state,p.content_sha AS indexed_sha
                FROM dl_capture c JOIN dl_pending_index p ON p.scope=c.scope AND p.seq=c.seq
                WHERE c.scope=? AND c.seq=?''',(scope,seq)).fetchone()
            if (not row or row['state']!='pending' or row['event_hash']==current_hash or
                row['content'].strip()==query.strip()):continue
            if row['content_sha']!=row['indexed_sha'] or hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:continue
            try:
                stamp=datetime.fromisoformat(row['recorded_at'])
                if stamp.utcoffset() is None or stamp>=limit or (boundary and stamp<=boundary):continue
            except (TypeError,ValueError):continue
            if not any(p in row['content'] for p in phrases):continue
            found.append(row)
            if len(found)>5:return ''
        if not found:return ''
        if self.automatic_memory_enabled and self.model_dir:
            try:
                model_key=self._index().model_key
                for candidate in found:
                    if self.flow.db.execute('''SELECT 1 FROM dl_auto_topic_members m WHERE m.scope=? AND m.seq=? AND m.model_key=?
                        AND (SELECT COUNT(*) FROM dl_auto_topic_members n WHERE n.scope=m.scope AND n.topic_id=m.topic_id AND n.model_key=m.model_key)>1''',
                        (scope,candidate['seq'],model_key)).fetchone():return ''
            except Exception:return ''
        found.sort(key=lambda item:item['seq'])
        row=found[0]
        def neighbor(seq,previous):
            direction='<' if previous else '>'
            ordering='DESC' if previous else 'ASC'
            return self.flow.db.execute(f'''SELECT seq,content,content_sha,recorded_at,event_hash,state FROM dl_capture
                WHERE scope=? AND seq {direction} ? ORDER BY seq {ordering} LIMIT 1''',(scope,seq)).fetchone()
        def same_window(first,second):
            try:
                a=datetime.fromisoformat(first['recorded_at']);b=datetime.fromisoformat(second['recorded_at'])
                return a.tzinfo is not None and b.tzinfo is not None and 0<=(b-a).total_seconds()<=180
            except (TypeError,ValueError):return False
        def acceptable(item):
            if (not item or item['state']!='pending' or 'pending:'+str(item['seq']) in excluded or
                item['event_hash']==current_hash or item['content'].strip()==query.strip() or
                hashlib.sha256(item['content'].encode('utf-8')).hexdigest()!=item['content_sha']):return False
            try:
                stamp=datetime.fromisoformat(item['recorded_at'])
                return stamp.utcoffset() is not None and stamp<limit and (not boundary or stamp>boundary)
            except (TypeError,ValueError):return False
        # Move back to the base message for an update, never expose a partial chain.
        backward_hops=0
        while _FOLLOWUP_UPDATE.match(row['content'].strip()):
            previous=neighbor(row['seq'],True)
            if not previous or not same_window(previous,row) or not acceptable(previous):return ''
            row=previous
            backward_hops+=1
            if backward_hops>=5:return ''
        chain=[row]
        while True:
            following=neighbor(chain[-1]['seq'],False)
            if (following and same_window(chain[-1],following) and
                hashlib.sha256(following['content'].encode('utf-8')).hexdigest()!=following['content_sha']):return ''
            if not following or not same_window(chain[-1],following):break
            if not (_FOLLOWUP_UPDATE.match(following['content'].strip()) or
                    (len(following['content'])<=40 and _UPDATE_CUES.search(following['content']))):break
            # The current user's words already appear in the request itself.
            if following['event_hash']==current_hash:break
            if not acceptable(following) or len(chain)>=5:return ''
            chain.append(following)
        if not {x['seq'] for x in found}.issubset({x['seq'] for x in chain}):return ''
        payload={'待审记录序号':row['seq'],'未整理历史原话':row['content'],'记录时间':row['recorded_at'],
                 '说明':'这是未关联的旧用户原话，仅为可能相关线索；指代与内容尚未确认'}
        if self.automatic_memory_enabled and self.model_dir:
            from .auto_model import unresolved_hint_sources
            if unresolved_hint_sources(self.flow.db,scope,self._index().model_key,{x['seq'] for x in chain}):
                payload['变化核验提示']='曾有相关变化线索现已失效或正在重新定位；新旧关系及当前状态尚不确定'
        imported=[x['seq'] for x in chain if self.flow.db.execute(
            'SELECT 1 FROM dl_import_provenance WHERE scope=? AND seq=?',(scope,x['seq'])).fetchone()]
        if imported:payload['导入时间说明']={'序号':imported,'说明':'时间由导入文件提供，未经独立核验'}
        if len(chain)>1:
            updates=[{'序号':x['seq'],'原话':x['content'],'记录时间':x['recorded_at']} for x in chain[1:]]
            payload['待审相关序号']=[x['seq'] for x in chain]
            payload['后续可能更新']=updates[0]
            if len(updates)>1:payload['进一步可能更新']=updates[1:]
            payload['说明']='相邻采集原话可能有更新关系；内容、事件和指代均尚未确认'
        if boundary:payload['采集情况']='先前存在已检测到的采集缺口，历史可能不完整；缺失内容无法恢复'
        output=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if self.flow.db.execute('PRAGMA data_version').fetchone()[0]!=version:return ''
        return output if len(output)<=budget else ''

    def _two_explicit_mentions(self,scope,query,events):
        """两个事件必须各有不重叠的明确名字，避免标题包含造成歧义。"""
        if len(events)!=2 or re.search(r'[「」『』“”"\']|不是|并非|别说|不指|不去|错了|记错|纠正',query):return False
        aliases={event['id']:[] for event in events}
        for row in self.flow.db.execute('SELECT event_id,alias FROM dl_event_alias WHERE scope=?',(scope,)):
            if row['event_id'] in aliases:aliases[row['event_id']].append(row['alias'])
        names=[[event['title']]+aliases[event['id']] for event in events]
        if set(names[0])&set(names[1]):return False
        spans=[]
        for choices in names:
            found=[]
            for name in choices:
                if not name:continue
                found.extend((match.start(),match.end()) for match in re.finditer(re.escape(name),query))
            spans.append(found)
        return any(a[1]<=b[0] or b[1]<=a[0] for a in spans[0] for b in spans[1])

    def _multi_event_packet(self,scope,query,events,budget,*,cutoff,current_message_id=None,data_version=None):
        """先预留每个事件的优先关系组，再分配和回收剩余字符预算。"""
        # 其他连接在本次准备及预算分配期间写入时，丢弃结果而不是复用旧快照。
        if data_version is None:data_version=self.flow.db.execute('PRAGMA data_version').fetchone()[0]
        if not self._two_explicit_mentions(scope,query,events):return ''
        wrapper={'多个事件':[],'说明':'分别是不同事件的历史原话，不表示事件之间存在关联'}
        overhead=len(json.dumps(wrapper,ensure_ascii=False,separators=(',',':')))+len(events)-1
        available=budget-overhead
        if available<=0:return ''
        # 同一请求中每个事件只做一次来源、时间和关系准备，不跨请求缓存。
        plans=[self._prepare_context_event(scope,event,query,cutoff=cutoff,
                                          current_message_id=current_message_id) for event in events]
        if not all(plans):return ''
        def retrieve(index,limit,minimum=False):
            packet=plans[index](limit,minimum)
            if not packet:return ''
            try:data=json.loads(packet)
            except (TypeError,ValueError):return ''
            return packet if isinstance(data,dict) and data.get('原话') else ''
        minimum=[retrieve(i,available,True) for i in range(len(events))]
        if not all(minimum):return ''
        remaining=available-sum(map(len,minimum))
        if remaining<0:return ''
        # 不拆分任何关系组；若当前事件用不满，未用预算转交下一个事件。
        first=retrieve(0,len(minimum[0])+remaining//2)
        if not first:return ''
        second_budget=available-len(first)
        second=retrieve(1,second_budget)
        if not second:return ''
        spare=available-len(first)-len(second)
        if spare>0:
            first=retrieve(0,len(first)+spare)
            if not first:return ''
        if len(first)+len(second)>available:return ''
        wrapper['多个事件']=[json.loads(first),json.loads(second)]
        result=json.dumps(wrapper,ensure_ascii=False,separators=(',',':'))
        if self.flow.db.execute('PRAGMA data_version').fetchone()[0]!=data_version:return ''
        return result if len(result)<=budget else ''

    def _background_context(self,scope,query,budget,current_message_id,cutoff):
        """每轮上下文兜底：取较早保存的原话作为背景，绝不当作本轮答案。"""
        if not self.always_context_enabled:return ''
        if not isinstance(current_message_id,str) or not 1<=len(current_message_id)<=256:return ''
        try: limit=datetime.fromisoformat(cutoff)
        except (TypeError,ValueError):return ''
        if limit.tzinfo is None:return ''
        current_hash=hashlib.sha256((scope+'\0'+current_message_id).encode('utf-8')).hexdigest()
        rows=self.flow.db.execute('''SELECT seq,content,content_sha,event_hash,recorded_at
            FROM dl_capture WHERE scope=? AND state='pending' ORDER BY seq''',(scope,)).fetchall()
        candidates=[]
        for row in rows:
            if row['event_hash']==current_hash:continue
            try: stamp=datetime.fromisoformat(row['recorded_at'])
            except (TypeError,ValueError):continue
            if stamp.utcoffset() is None or stamp>=limit:continue
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:continue
            candidates.append(row)
        if not candidates:return ''
        chosen=candidates[:2]  # 较早的原话 = 当前窗口之外的长期背景
        latest=candidates[-1]
        if latest['seq'] not in {item['seq'] for item in chosen}:
            chosen.append(latest)
        chosen.sort(key=lambda item:item['seq'])
        payload={'背景上下文':'以下为该会话较早保存的原话（未确认，可能过时）；仅作背景，不代表本轮答案',
                 '最近原话':[{'序号':item['seq'],'原话':item['content'],
                          '记录时间':item['recorded_at']} for item in chosen],
                 '说明':'本轮没有直接匹配的记忆；以上仅为背景线索，不要断言为当前事实'}
        out=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        while len(out)>budget and len(payload['最近原话'])>1:
            payload['最近原话']=payload['最近原话'][:-1]
            out=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        return out if len(out)<=budget else ''

    def context_packet(self,scope,query,budget=600,*,time_hint=None,exclude_ids=None,current_message_id=None,as_of=None):
        """Bounded source packet; similarity is association only, not evidence."""
        if not 1<=len(query)<=400: return ''
        request_time=as_of if as_of is not None else datetime.now(timezone.utc)
        if not isinstance(request_time,datetime) or request_time.utcoffset() is None:return ''
        data_version=self.flow.db.execute('PRAGMA data_version').fetchone()[0] if self.multi_event_context_enabled else None
        events=self.flow._events(scope)
        # Avoid treating negated or quoted titles as positive references.
        exact=[e for e,_ in self._named_events(scope,query,events)]
        # Scope ambiguity prevents source promotion, even if vector search returns one.
        if len(exact)==1:
            e=exact[0]
            return self._context_event(scope,e,time_hint or query,budget,cutoff=request_time,current_message_id=current_message_id)
        # 默认延续多标题歧义保护；实验模式仅允许两个明确且不重叠的标题。
        if len(exact)>1:
            if (not self.multi_event_context_enabled or len(exact)!=2 or
                any(e['id'] in (exclude_ids or ()) for e in exact)):return ''
            return self._multi_event_packet(scope,query,exact,budget,cutoff=request_time,
                                            current_message_id=current_message_id,data_version=data_version)
        cutoff=request_time.astimezone(timezone.utc).isoformat()
        excluded=set(exclude_ids or ())
        eligible=[event for event in events if event['id'] not in excluded]
        details=self._weak_candidate_details(scope,{'content':query,'recorded_at':cutoff},eligible) if self.weak_association_enabled else []
        ids=[x['event_id'] for x in details]
        method='本地词组弱匹配'
        semantic_incomplete=False
        if self.association_context_enabled and len(details)==1:
            item=details[0]
            source_ids={c['source_id'] for c in item['clues'] if c['source_id'] and len(c['text'])>=4}
            # 同一原话上的两个不重叠词组也可作为弱线索；不能跨原话拼凑。
            if not re.search(r'不是|并非|不指|别说|不对|记错',query):
                snap=self.flow._snapshot(scope,item['event_id'])
                terms={c['text'] for c in item['clues'] if c['source_id']}
                if snap and not snap['stale']:
                    source_ids.update(row['id'] for row in snap['rows']
                                      if sum(term in row['content'] for term in terms)>=2)
            event=next((e for e in events if e['id']==item['event_id']),None)
            if source_ids and event:
                wrapper={'未确认的联想事件':event['title'],
                         '说明':'以下历史原话可能相关；指代尚未确认，不要断言是同一事件',
                         '关联历史':None}
                overhead=len(json.dumps(wrapper,ensure_ascii=False,separators=(',',':')))-len('null')
                packet=self._context_event(scope,event,query,budget-overhead,focus_ids=source_ids,
                                           cutoff=request_time,current_message_id=current_message_id)
                if packet:
                    wrapper['关联历史']=json.loads(packet)
                    output=json.dumps(wrapper,ensure_ascii=False,separators=(',',':'))
                    if len(output)<=budget:return output
        if not ids and self.model_dir:
            # Avoid withholding a successful local result while a BGE load blocks.
            try:
                semantic_ids=self._index().search(scope,query)
                semantic_incomplete=bool(self.index.last_sync.get('unavailable'))
            except Exception:semantic_ids=[]
            allowed={e['id']:e for e in events};limit=datetime.fromisoformat(cutoff)
            denied=self._negated_events(scope,query,events)
            for eid in semantic_ids:
                event=allowed.get(eid)
                if not event or eid in denied or eid in excluded:continue
                snap=self.flow._snapshot(scope,eid)
                if not snap or snap['stale']:continue
                try:
                    times=[datetime.fromisoformat(x['recorded_at']) for x in snap['rows']]
                    if any(t.tzinfo is None or t>=limit for t in times):continue
                except (TypeError,ValueError):continue
                ids.append(eid)
            method='本地语义相似'
            # 事件向量只是召回线索；仅在独立开关开启且唯一候选时尝试定位原话。
            if self.semantic_source_context_enabled and not semantic_incomplete and len(ids)==1 and not re.search(r'不是|并非|不指|别说|不对|记错|错了',query):
                event=allowed[ids[0]]
                try:
                    version=self.flow.db.execute('PRAGMA data_version').fetchone()[0]
                    snap=self.flow._snapshot(scope,event['id'])
                    indexed=self.flow.db.execute('SELECT fingerprint FROM e419_vectors WHERE scope=? AND event_id=? AND model_key=?',
                                                 (scope,event['id'],self.index.model_key)).fetchone()
                    if snap and not snap['stale'] and indexed and indexed['fingerprint']==snap['digest'] and 1<=len(snap['rows'])<=100:
                        rows=snap['rows'];encoder=self.embedder
                        questions=encoder.encode_queries([query])
                        if not isinstance(questions,(list,tuple)) or len(questions)!=1:
                            raise ValueError('embedding shape')
                        q=_unit(questions[0]);rank=[];cached=self.semantic_source_vectors
                        if getattr(encoder,'model_key',None)!=self.index.encoder_key:raise ValueError('encoder changed')
                        # 每批最多 20 条，必须完成全部批次，不能把部分候选当作唯一。
                        for start in range(0,len(rows),20):
                            batch=rows[start:start+20]
                            keys=[(scope,self.index.model_key,r['id'],hashlib.sha256(r['content'].encode('utf-8')).hexdigest()) for r in batch]
                            missing=[(r,k) for r,k in zip(batch,keys) if k not in cached]
                            if missing:
                                docs=encoder.encode_documents([r['content'] for r,k in missing])
                                if not isinstance(docs,(list,tuple)) or len(docs)!=len(missing):
                                    raise ValueError('embedding shape')
                                prepared=[_unit(vector) for vector in docs]
                                if any(len(vector)!=len(q) for vector in prepared):raise ValueError('embedding dimension')
                                for (_,key),vector in zip(missing,prepared):cached[key]=vector
                            for row,key in zip(batch,keys):
                                v=cached[key];cached.move_to_end(key)
                                if len(v)!=len(q):raise ValueError('embedding dimension')
                                rank.append((sum(a*b for a,b in zip(q,v)),row['id']))
                            while len(cached)>100:cached.popitem(last=False)
                        rank.sort(key=lambda x:(-x[0],x[1]))
                        # 门槛不是概率或真实性：两个来源接近时不选。
                        if rank[0][0]>=self.source_min_cosine and (len(rank)==1 or rank[0][0]-rank[1][0]>=self.source_min_lead):
                            wrapper={'未确认的联想事件':event['title'],'匹配方式':'本地语义相似',
                                     '说明':'语义相似仅是线索，历史原话可能无关，指代尚未确认', '关联历史':None}
                            overhead=len(json.dumps(wrapper,ensure_ascii=False,separators=(',',':')))-len('null')
                            packet=self._context_event(scope,event,query,budget-overhead,focus_ids={rank[0][1]},
                                                       cutoff=request_time,current_message_id=current_message_id)
                            latest=self.flow._snapshot(scope,event['id'])
                            unchanged=(latest and not latest['stale'] and latest['digest']==snap['digest'] and
                                       self.flow.db.execute('PRAGMA data_version').fetchone()[0]==version)
                            if packet and unchanged:
                                wrapper['关联历史']=json.loads(packet)
                                output=json.dumps(wrapper,ensure_ascii=False,separators=(',',':'))
                                if len(output)<=budget:return output
                except Exception:
                    while len(self.semantic_source_vectors)>100:self.semantic_source_vectors.popitem(last=False)
                    pass  # 模型不可用或来源不满足条件时仅退回事件候选。
        mapping={e['id']:e['title'] for e in events}
        titles=[mapping[eid] for eid in ids if eid in mapping]
        if not titles:
            if self.automatic_memory_enabled and self.model_dir:
                from .auto_cross_update import cross_update_context
                blocked,paired=cross_update_context(self.flow.db,self._index().model_key,self._index().embedder,
                    scope,query,cutoff,budget,current_message_id,excluded)
                if blocked:return paired
            automatic=self._auto_topic_context(scope,query,cutoff,budget,current_message_id,excluded)
            if not automatic:automatic=self._auto_group_context(scope,query,cutoff,budget,current_message_id,excluded)
            pending=automatic or self._pending_context(scope,query,cutoff,budget,current_message_id,excluded)
            relevant=pending or (self._semantic_unlinked_context(scope,query,cutoff,budget,current_message_id,excluded)
                                 if self.automatic_memory_enabled else '')
            return relevant or self._background_context(
                scope,query,min(budget,320),current_message_id,cutoff)
        payload={'未确认的联想候选':titles,'匹配方式':method,'说明':'候选可能不相关，不是已确认记忆'}
        if method=='本地语义相似' and semantic_incomplete:
            payload['索引提示']='部分事件尚未完成编码，候选不完整，不提供语义原话'
        if method=='本地词组弱匹配' and (self.event_priority_enabled or self.recency_rank_enabled or self.preference_rank_enabled):
            payload['排序提示']='候选顺序可能受人工标记、近期记录或偏好字面线索影响'
        out=json.dumps(payload,ensure_ascii=False)
        return out if len(out)<=budget else ''

    def _auto_followup(self,scope,focus,budget,cutoff,current_message_id,excluded):
        """承接近期系统候选，重新核验完整来源；不把指代推测视为确认。"""
        if not isinstance(current_message_id,str) or not 1<=len(current_message_id)<=256:return ''
        manifest=focus.get('auto_sources')
        if not isinstance(manifest,list) or not 1<=len(manifest)<=5:return ''
        db=self.flow.db;version=db.execute('PRAGMA data_version').fetchone()[0]
        try:
            ids=[x['seq'] for x in manifest]
            if any(type(x) is not int for x in ids) or ids!=sorted(set(ids)):return ''
            from .auto_model import separated
            if any(separated(db,scope,a,b) for i,a in enumerate(ids) for b in ids[i+1:]):return ''
            current_hash=hashlib.sha256((scope+'\0'+current_message_id).encode()).hexdigest()
            if db.execute('''SELECT 1 FROM dl_auto_jobs j JOIN dl_capture c ON c.scope=j.scope AND c.seq=j.seq
                WHERE j.scope=? AND j.status='pending' AND c.event_hash<>? LIMIT 1''',(scope,current_hash)).fetchone():return ''
            from .auto_organize import read_group
            items=[]
            for entry in manifest:
                seq=entry['seq']
                if 'pending:'+str(seq) in excluded:return ''
                if self._auto_source_conflict(scope,seq):return ''
                row=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
                if (not row or row['state']!='pending' or row['event_hash']==current_hash or row['content_sha']!=entry['sha'] or
                    row['recorded_at']!=entry['time'] or hashlib.sha256(row['content'].encode()).hexdigest()!=entry['sha']):return ''
                t=datetime.fromisoformat(row['recorded_at'])
                if t.utcoffset() is None or t>=cutoff:return ''
                group=read_group(db,scope,seq)
                if group and group['members']!=ids:return ''
                key=self._index().model_key if self.model_dir else None
                member=db.execute('SELECT topic_id FROM dl_auto_topic_members WHERE scope=? AND seq=? AND model_key=?',(scope,seq,key)).fetchone()
                if member:
                    linked=[r['seq'] for r in db.execute('SELECT seq FROM dl_auto_topic_members WHERE scope=? AND topic_id=? AND model_key=? ORDER BY seq',(scope,member['topic_id'],key))]
                    if linked!=ids:return ''
                following=db.execute('SELECT seq,content,recorded_at FROM dl_capture WHERE scope=? AND seq>? ORDER BY seq LIMIT 1',(scope,seq)).fetchone()
                if following and following['seq'] not in ids and _FOLLOWUP_UPDATE.match(following['content'].strip()):
                    later=datetime.fromisoformat(following['recorded_at'])
                    if later.utcoffset() is None or 0<=(later-t).total_seconds()<=180:return ''
                item={'序号':seq,'原话':row['content'],'记录时间':row['recorded_at']}
                if db.execute('SELECT 1 FROM dl_import_provenance WHERE scope=? AND seq=?',(scope,seq)).fetchone():item['时间来源']='导入文件，未经独立核验'
                items.append(item)
            payload={'待审记录序号':ids[0],'待审相关序号':ids,'可能相关原话':items,
                '说明':'可能延续上一轮的未确认线索；指代及事件关系不确定'}
            if self.model_dir:
                from .auto_model import unresolved_hint_sources
                if unresolved_hint_sources(db,scope,self._index().model_key,ids):
                    payload['变化核验提示']='曾有相关变化线索现已失效或正在重新定位；新旧关系及当前状态尚不确定'
            result=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
            if db.execute('PRAGMA data_version').fetchone()[0]!=version:return ''
            return result if len(result)<=budget else ''
        except (TypeError,KeyError,ValueError,OverflowError):return ''

    def context_turn(self,scope,query,focus=None,budget=600,*,exclude_ids=None,current_message_id=None,as_of=None):
        """Resolve only short, explicit follow-ups to the preceding named event."""
        followup=re.fullmatch(r'(?:那件事|这件事|那个计划|这个计划)?(?:后来|最后|最初|现在|结果)(?:呢|怎么样|如何|是什么|怎么样了)?[？?。！!]*',query)
        # 对“那个背包是什么颜色”这类短追问，仅在原话中存在相同细节时延续。
        detail=re.fullmatch(r'(?:那个|这个|那次的|这次的|它的|之前说的)([\u4e00-\u9fff]{2,12}?)(?:呢|怎么样(?:了)?|是什么(?:颜色|时间|价格)?|多少钱|在哪(?:里)?|什么(?:颜色|时间|价格|时候)|[？?])[？?。！!]*',query)
        if detail and (re.search(r'不是|并非|不对|记错|错了|纠正|别说|不指',query) or
                       self._named_events(scope,query,self.flow._events(scope))):detail=None
        if followup and self.automatic_memory_enabled and focus and focus.get('auto_sources'):
            if focus.get('remaining',0)<1:return '',None
            cutoff=as_of if as_of is not None else datetime.now(timezone.utc)
            if not isinstance(cutoff,datetime) or cutoff.utcoffset() is None:return '',None
            if focus.get('cross_query') or focus.get('topic_query'):
                try:
                    query_before=focus.get('cross_query') or focus.get('topic_query');manifest=focus['auto_sources']
                    if not isinstance(query_before,str) or not 1<=len(query_before)<=400 or not isinstance(manifest,list) or not 2<=len(manifest)<=budget//10:return '',None
                    version=self.flow.db.execute('PRAGMA data_version').fetchone()[0]
                    stamp=cutoff.astimezone(timezone.utc).isoformat()
                    if focus.get('cross_query'):
                        from .auto_cross_update import cross_update_context
                        blocked,packet=cross_update_context(self.flow.db,self._index().model_key,self._index().embedder,scope,query_before,
                            stamp,budget,current_message_id,set(exclude_ids or ()))
                        if not blocked:packet=''
                    else:
                        if len(manifest)>10:return '',None
                        packet=self._auto_topic_context(scope,query_before,stamp,budget,current_message_id,set(exclude_ids or ()),
                                                        after_gap=self.capture_gap_boundary(scope))
                    data=json.loads(packet) if packet else {}
                    ids=data.get('待审相关序号')
                    previous=[entry['seq'] for entry in manifest]
                    if (not isinstance(ids,list) or any(type(x) is not int for x in ids) or
                        ids!=sorted(set(ids)) or not set(previous).issubset(ids)):return '',None
                    for entry in manifest:
                        row=self.flow.db.execute('SELECT content_sha,recorded_at FROM dl_capture WHERE scope=? AND seq=? AND state=\'pending\'',(scope,entry['seq'])).fetchone()
                        if not row or row['content_sha']!=entry['sha'] or row['recorded_at']!=entry['time']:return '',None
                    refreshed=[]
                    for seq in ids:
                        row=self.flow.db.execute('SELECT content_sha,recorded_at FROM dl_capture WHERE scope=? AND seq=? AND state=\'pending\'',(scope,seq)).fetchone()
                        if not row:return '',None
                        refreshed.append({'seq':seq,'sha':row['content_sha'],'time':row['recorded_at']})
                    if self.flow.db.execute('PRAGMA data_version').fetchone()[0]!=version:return '',None
                    return packet,{**focus,'auto_sources':refreshed,'remaining':focus['remaining']-1}
                except (KeyError,TypeError,ValueError):return '',None
            packet=self._auto_followup(scope,focus,budget,cutoff,current_message_id,set(exclude_ids or ()))
            return (packet,{**focus,'remaining':focus['remaining']-1}) if packet else ('',None)
        if followup or detail:
            if not focus or focus.get('remaining',0)<1 or focus['event_id'] in (exclude_ids or ()):return '',None
            snap=self.flow._snapshot(scope,focus['event_id'])
            if (not snap or snap['stale'] or snap['digest']!=focus['digest'] or
                focus.get('alias_token')!=self._alias_token(scope,snap['id'])):return '',None
            if detail:
                terms=self._character_pairs(detail.group(1))
                if not terms or not any(term in row['content'] for row in snap['rows'] for term in terms):
                    # 无法把细节绑定到前一事件时，按普通检索处理。
                    packet=self.context_packet(scope,query,budget=budget,exclude_ids=exclude_ids,
                                               current_message_id=current_message_id,as_of=as_of)
                    return packet,None
            anchor=focus.get('anchor_name','')
            anchored=self._named_events(scope,anchor,self.flow._events(scope)) if anchor else []
            if len(anchored)!=1 or anchored[0][0]['id']!=snap['id']:return '',None
            prefix='可能延续上一轮提到的事件，指代未确认；如果不相关请忽略。\n'
            packet=self.context_packet(scope,snap['title'],budget=budget-len(prefix),time_hint=query,
                                       current_message_id=current_message_id,as_of=as_of)
            if not packet:return '',None
            # The referent is a conversational guess, even after a named event.
            next_focus={**focus,'remaining':focus['remaining']-1}
            return (prefix+packet,next_focus) if len(prefix+packet)<=budget else ('',None)
        events=self.flow._events(scope)
        exact=[e for e,_ in self._named_events(scope,query,events)]
        packet=self.context_packet(scope,query,budget=budget,exclude_ids=exclude_ids,current_message_id=current_message_id,as_of=as_of)
        if (not packet or len(exact)!=1 or re.search(r'[「」『』“”"\']|不是|不对|错了|纠正|并非',query)):
            return packet,None
        snap=self.flow._snapshot(scope,exact[0]['id'])
        if not snap or snap['stale']:return packet,None
        names=[snap['title']]+self.list_aliases(scope,snap['title'])
        anchor=next((name for name in names if name in query and not re.search(
            r'(?:不是|并非|不指|别说|并不是)\s*'+re.escape(name),query)),None)
        if not anchor:return packet,None
        return packet,{'event_id':snap['id'],'digest':snap['digest'],'remaining':2,
                       'alias_token':self._alias_token(scope,snap['id']),'anchor_name':anchor}

    def verify_stored_backup(self,name):
        if not isinstance(name,str) or not re.fullmatch(r'memory_\d{8}T\d{6}_[0-9a-f]{8}\.sqlite3',name):
            raise ValueError('invalid backup name')
        path=self.root/'backups'/name
        if path.is_symlink() or not path.is_file():raise ValueError('backup missing')
        from .recovery import verify_backup
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
            from .recovery import verify_backup
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

    def close(self):
        self.semantic_source_vectors.clear()
        self.flow.close()
