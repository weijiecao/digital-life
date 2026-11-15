import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from astrbot_plugin_digital_memory_store.storage import Store


@contextmanager
def _conn(path, **kwargs):
    """在 Windows 上显式关闭连接：`with _conn(...)` 只提交，不关闭句柄。"""
    conn = sqlite3.connect(path, **kwargs)
    try:
        with conn:
            yield conn
    finally:
        conn.close()

class FakeEncoder:
    model_key='fixture-only-v1'
    def __init__(self, path): pass
    def encode_documents(self, texts): return [[1.,0.] if '书' in t else [0.,1.] for t in texts]
    def encode_queries(self, texts): return [[1.,0.] if '书' in t else [0.,1.] for t in texts]

class SemanticFixtureEncoder:
    model_key='semantic-source-fixture-v1'
    def __init__(self,path): self.ambiguous=False
    def encode_documents(self,texts):
        return [[1.,0.] if ('书店计划' in t or '原计划周六' in t or self.ambiguous) else [0.,1.] for t in texts]
    def encode_queries(self,texts): return [[1.,0.] for _ in texts]

class PartiallyFailingEncoder(SemanticFixtureEncoder):
    model_key='partial-failure-fixture-v1'
    def __init__(self,path): self.ambiguous=False;self.fail=False
    def encode_documents(self,texts):
        if self.fail and any('超长事件' in t for t in texts):raise ValueError('event too long')
        return super().encode_documents(texts)

class LimitedLengthEncoder:
    model_key='length-limited-fixture-v1'
    def __init__(self,path): self.received=[]
    def encode_documents(self,texts):
        self.received.extend(texts)
        if any(len(text)>100 for text in texts):raise ValueError('input too long')
        return [[1.,0.] for _ in texts]
    def encode_queries(self,texts):return [[1.,0.] for _ in texts]

class RareDetailEncoder:
    model_key='rare-detail-fixture-v1'
    def __init__(self,path):pass
    def encode_documents(self,texts):
        if any(len(t)>100 for t in texts):raise ValueError('too long')
        return [[1.,0.] if '稀有线索' in t else [0.,1.] for t in texts]
    def encode_queries(self,texts):return [[1.,0.] for _ in texts]

class TestStore(unittest.TestCase):
    @unittest.skipUnless(hasattr(sqlite3.Connection, 'setlimit'), '需要 Python 3.11+ 的 sqlite3.Connection.setlimit')
    def test_long_capture_indexes_fully_and_splits_sql_parameters(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_cross_update import sync_text_index,_indexed_candidates
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',LimitedLengthEncoder);a.automatic_memory_enabled=True
            scope=a.scope('1','s');db=a.flow.db
            text=''.join(chr(0x4e00+i) for i in range(1200))
            a.capture(scope,'1',text,recorded_at='2025-01-01T00:00:00+00:00')
            a.capture(scope,'2',text+'取消',recorded_at='2025-01-02T00:00:00+00:00')
            process_jobs(db);self.assertEqual(a.sync_auto_source_vectors(),2)
            self.assertEqual(db.execute('SELECT content FROM dl_capture WHERE scope=? AND seq=1',(scope,)).fetchone()[0],text)
            self.assertEqual(sync_text_index(db,100),2)
            old=db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,450)
            try:self.assertEqual(_indexed_candidates(db,scope,'unused'),{1,2})
            finally:db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,old)
            a.close()

    def test_oversized_source_is_fully_chunked_without_blocking_other_sources(self):
        import json
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',LimitedLengthEncoder);a.automatic_memory_enabled=True
            scope=a.scope('1','s');other=a.scope('2','s')
            long_text='书店的详细计划'+'很长的补充说明'*30+'带一件雨衣'
            a.capture(scope,'1',long_text,recorded_at='2025-01-01T00:00:00+00:00')
            a.capture(other,'1','另一会话的普通记录',recorded_at='2025-01-01T00:00:00+00:00')
            process_jobs(a.flow.db)
            self.assertEqual(a.sync_auto_source_vectors(),2)
            row=a.flow.db.execute('SELECT part_count FROM dl_auto_vector_chunks WHERE scope=?',(scope,)).fetchone()
            self.assertGreater(row['part_count'],1)
            self.assertEqual(a.flow.db.execute('SELECT content FROM dl_capture WHERE scope=?',(scope,)).fetchone()[0],long_text)
            packet=a._semantic_unlinked_context(scope,'购买小说','2025-02-01T00:00:00+00:00',3000,'q',set())
            data=json.loads(packet)
            self.assertEqual(data['未整理历史原话'],long_text);self.assertIn('分段向量',data['检索提示'])
            with a.flow.db:a.flow.db.execute("UPDATE dl_capture SET content='改过' WHERE scope=?",(scope,))
            self.assertFalse(a.flow.db.execute('SELECT 1 FROM dl_auto_vector_chunks WHERE scope=?',(scope,)).fetchone())
            a.close()

    def test_adjacent_model_sources_require_matching_policy_and_recheck_on_finish(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');db=a.flow.db
            a.capture(scope,'1','原来去书店',recorded_at='2025-01-01T12:00:00+00:00')
            a.auto_model_enabled=True;a.auto_model_policy='current'
            a.capture(scope,'2','现在改去公园',recorded_at='2025-01-01T12:01:00+00:00')
            process_jobs(db)
            self.assertIsNone(prepare(db,policy='current'))
            self.assertEqual(db.execute('SELECT status FROM dl_auto_model_jobs WHERE scope=? AND seq=2',(scope,)).fetchone()[0],'skipped')
            with db:
                db.execute("INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,1,'done','current')",(scope,))
                db.execute("UPDATE dl_auto_model_jobs SET status='pending' WHERE scope=? AND seq=2",(scope,))
            snapshot=prepare(db,policy='current')
            self.assertEqual([x['seq'] for x in snapshot['sources']],[1,2])
            with db:db.execute("UPDATE dl_auto_model_jobs SET policy='other' WHERE scope=? AND seq=1",(scope,))
            self.assertFalse(finish(db,snapshot,'{"verdict":"related","quote_a":"书店","quote_b":"公园","relation":"update"}'))
            self.assertFalse(db.execute('SELECT 1 FROM dl_auto_relation_hints WHERE scope=?',(scope,)).fetchone())
            a.close()

    def test_interrupted_change_finds_older_authorized_source(self):
        import hashlib
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish,read_relation_hint
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);scope=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            records=[('周末的书店计划','2025-01-01T12:00:00+00:00','[1.0,0.0]'),
                     ('今天工作挺忙','2025-01-03T12:00:00+00:00','[0.0,1.0]'),
                     ('那个书店计划取消了','2025-01-03T12:01:00+00:00','[1.0,0.0]')]
            with db:
                for seq,(content,stamp,vector) in enumerate(records,1):
                    sha=hashlib.sha256(content.encode()).hexdigest()
                    db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',
                               (seq,scope,'e'+str(seq),content,sha,stamp))
                    db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(scope,seq,key,sha,vector))
                    db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',
                               (scope,seq,'pending' if seq==3 else 'done','p'))
            snapshot=prepare(db,policy='p',model_key=key)
            self.assertEqual([x['seq'] for x in snapshot['sources']],[1,3])
            self.assertEqual(snapshot['sources'][0]['mode'],'cross_update_search')
            self.assertTrue(finish(db,snapshot,'{"verdict":"related","quote_a":"书店计划","quote_b":"书店计划","relation":"update"}'))
            self.assertEqual(read_relation_hint(db,scope,1,3),'可能更新')
            with db:
                db.execute("UPDATE dl_auto_source_vectors SET vector_json='[1.0,0.0]' WHERE scope=? AND seq=2",(scope,))
                db.execute("UPDATE dl_auto_model_jobs SET status='pending',attempts=0 WHERE scope=? AND seq=3",(scope,))
                db.execute('DELETE FROM dl_auto_model_limit')
            adjacent=prepare(db,policy='p',model_key=key)
            self.assertEqual([x['seq'] for x in adjacent['sources']],[2,3])
            a.close()

    def test_independent_changes_rank_separately_and_close_scores_remain_ambiguous(self):
        import json
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_cross_update import cross_update_context, sync_text_index
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);a.automatic_memory_enabled=True
            scope=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            texts=['书店计划原定周六','书店计划改到周日','运动安排原定周二','运动安排改到周三']
            for i,text in enumerate(texts,1):
                a.capture(scope,str(i),text,recorded_at=f'2025-01-0{i}T12:00:00+00:00')
            process_jobs(db);a.sync_auto_source_vectors();a.sync_auto_topics()
            with db:
                db.execute("UPDATE dl_auto_topic_members SET topic_id='other' WHERE scope=? AND seq IN (2,4)",(scope,))
                db.execute("UPDATE dl_auto_source_vectors SET vector_json='[0.8,0.6]' WHERE scope=? AND seq IN (3,4)",(scope,))
            sync_text_index(db,100)
            args=(db,key,a._index().embedder,scope,'书','2025-02-01T00:00:00+00:00',1500,'q',set())
            blocked,packet=cross_update_context(*args)
            self.assertTrue(blocked)
            self.assertEqual([x['原话'] for x in json.loads(packet)['可能相关原话']],texts[:2])
            with db:db.execute("UPDATE dl_auto_source_vectors SET vector_json='[1.0,0.0]' WHERE scope=? AND seq IN (3,4)",(scope,))
            self.assertEqual(cross_update_context(*args),(True,''))
            a.close()

    def test_model_and_rule_changes_are_merged_without_losing_later_source(self):
        import hashlib,json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish
        from astrbot_plugin_digital_memory_store.auto_cross_update import cross_update_context
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);a.automatic_memory_enabled=True;s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            texts=['我原来住在青岛','我现在住在深圳南山区','我现在住在深圳南山区改到福田']
            def insert(i,topic,status):
                text=texts[i-1];sha=hashlib.sha256(text.encode()).hexdigest();stamp=f'2025-01-0{i}T12:00:00+00:00'
                db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',(i,s,'f'+str(i),text,sha,stamp))
                db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',(s,i,key,topic,sha,stamp,'测试'))
                db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(s,i,key,sha,'[1.0,0.0]'))
                db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',(s,i,status,'p'))
            with db:
                for topic in ('old','new'):db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,topic,key,'2025-01-01T00:00:00+00:00'))
                insert(1,'old','done');insert(2,'new','pending')
            snapshot=prepare(db,policy='p',model_key=key)
            self.assertEqual([x['seq'] for x in snapshot['sources']],[1,2])
            self.assertTrue(finish(db,snapshot,'{"verdict":"related","quote_a":"青岛","quote_b":"深圳","relation":"update"}'))
            with db:insert(3,'new','done')
            cutoff='2025-02-01T00:00:00+00:00'
            blocked,packet=cross_update_context(db,key,a._index().embedder,s,'书',cutoff,1500,'q',set())
            self.assertTrue(blocked);data=json.loads(packet)
            self.assertEqual(data['线索方式'],'跨事件组合变化线索')
            self.assertEqual([x['原话'] for x in data['可能相关原话']],texts)
            self.assertIn('未确认',data['说明']);self.assertEqual(len(data['可能的更新关系']),1)
            self.assertEqual(cross_update_context(db,key,a._index().embedder,s,'书',cutoff,100,'q',set()),(True,''))
            rows=db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? ORDER BY seq',(s,)).fetchall()
            focus={'auto_sources':[{'seq':r['seq'],'sha':r['content_sha'],'time':r['recorded_at']} for r in rows],
                   'cross_query':'书','remaining':2}
            follow,_=a.context_turn(s,'后来呢',focus,budget=1500,current_message_id='q2',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            self.assertEqual(json.loads(follow)['待审相关序号'],[1,2,3]);a.close()

    def test_connected_cross_model_updates_are_supplied_as_complete_chain(self):
        import hashlib,json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish
        from astrbot_plugin_digital_memory_store.auto_cross_update import cross_update_context
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);a.automatic_memory_enabled=True
            s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            texts=['以前居住在青岛','后来搬到了深圳','现在在成都生活']
            vectors=['[1.0,0.0]','[0.9,0.4358898944]','[0.8,0.6]']
            with db:
                for i,text in enumerate(texts,1):
                    sha=hashlib.sha256(text.encode()).hexdigest();stamp=f'2025-01-0{i}T12:00:00+00:00';topic='t'+str(i)
                    db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',(i,s,'f'+str(i),text,sha,stamp))
                    db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,topic,key,stamp))
                    db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',(s,i,key,topic,sha,stamp,'测试'))
                    db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(s,i,key,sha,vectors[i-1]))
                    db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',(s,i,'done' if i==1 else 'pending','p'))
            first=prepare(db,policy='p',model_key=key)
            self.assertEqual([x['seq'] for x in first['sources']],[1,2])
            self.assertTrue(finish(db,first,'{"verdict":"related","quote_a":"青岛","quote_b":"深圳","relation":"update"}'))
            with db:db.execute('DELETE FROM dl_auto_model_limit')
            second=prepare(db,policy='p',model_key=key)
            self.assertEqual([x['seq'] for x in second['sources']],[2,3])
            self.assertTrue(finish(db,second,'{"verdict":"related","quote_a":"深圳","quote_b":"成都","relation":"update"}'))
            cutoff='2025-02-01T00:00:00+00:00'
            blocked,packet=cross_update_context(db,key,a._index().embedder,s,'书',cutoff,1500,'q',set())
            self.assertTrue(blocked);data=json.loads(packet)
            self.assertEqual([x['原话'] for x in data['可能相关原话']],texts)
            self.assertEqual([(x['较早序号'],x['较新序号']) for x in data['可能的更新关系']],[(1,2),(2,3)])
            self.assertEqual(cross_update_context(db,key,a._index().embedder,s,'书',cutoff,1500,'q',{'pending:1'}),(True,''))
            self.assertEqual(cross_update_context(db,key,a._index().embedder,s,'书',cutoff,80,'q',set()),(True,''))
            rows=db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? ORDER BY seq',(s,)).fetchall()
            focus={'auto_sources':[{'seq':r['seq'],'sha':r['content_sha'],'time':r['recorded_at']} for r in rows],
                   'cross_query':'书','remaining':2}
            follow,_=a.context_turn(s,'后来呢',focus,budget=1500,current_message_id='q2',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            self.assertEqual(json.loads(follow)['待审相关序号'],[1,2,3])
            # 非线索来源与第一个来源被错误放入同一事件，反向判断应隔离整个事件。
            text='另一件不相关的事情';sha=hashlib.sha256(text.encode()).hexdigest();stamp='2025-01-04T12:00:00+00:00'
            first=rows[0]
            manifest=json.dumps([{'seq':1,'sha256':first['content_sha'],'recorded_at':first['recorded_at'],'mode':'rebuild_split'},
                                 {'seq':4,'sha256':sha,'recorded_at':stamp,'mode':'rebuild_split'}])
            with db:
                db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',(4,s,'f4',text,sha,stamp))
                db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',(s,4,key,'t1',sha,stamp,'测试'))
                db.execute('INSERT INTO dl_auto_separations VALUES(?,?,?,?,?,1)',(s,4,1,manifest,stamp))
            blocked,remaining=cross_update_context(db,key,a._index().embedder,s,'书',cutoff,1500,'q',set())
            self.assertTrue(blocked)
            self.assertEqual(json.loads(remaining)['待审相关序号'],[2,3])
            self.assertIn('变化核验提示',json.loads(remaining))
            a.close()

    def test_cross_model_waits_for_vectors_and_resumes_after_restart_without_blocking_other_scope(self):
        import hashlib
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s');key=a._index().model_key;db=a.flow.db
            content='测试原始信息';sha=hashlib.sha256(content.encode()).hexdigest()
            # 模拟旧版本因向量未补齐而永久跳过的任务。
            rows=[(1,s,'p','done',False),(2,s,'other','done',False),(3,s,'p','skipped',True),
                  (4,other,'p','done',True),(5,other,'p','pending',True)]
            with db:
                for seq,scope,policy,status,has_vector in rows:
                    stamp=f'2025-01-0{seq}T12:00:00+00:00'
                    db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',
                               (seq,scope,'f'+str(seq),content,sha,stamp))
                    db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',(scope,seq,status,policy))
                    if has_vector:db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(scope,seq,key,sha,'[1.0,0.0]'))
            snapshot=prepare(db,policy='p',model_key=key)
            self.assertEqual(snapshot['scope'],other);self.assertEqual([x['seq'] for x in snapshot['sources']],[4,5])
            self.assertEqual(db.execute('SELECT status FROM dl_auto_model_jobs WHERE scope=? AND seq=3',(s,)).fetchone()[0],'waiting_vectors')
            self.assertTrue(finish(db,snapshot,'{"verdict":"uncertain","quote_a":"","quote_b":""}'))
            a.close();a=Store(d,'fixture-only',FakeEncoder);db=a.flow.db
            with db:
                db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(s,1,key,sha,'[1.0,0.0]'))
                db.execute('DELETE FROM dl_auto_model_limit')
            self.assertIsNone(prepare(db,policy='wrong',model_key=key))
            self.assertEqual(db.execute('SELECT status FROM dl_auto_model_jobs WHERE scope=? AND seq=3',(s,)).fetchone()[0],'waiting_vectors')
            snapshot=prepare(db,policy='p',model_key=key)
            self.assertEqual([x['seq'] for x in snapshot['sources']],[1,3])
            self.assertEqual(snapshot['sources'][0]['mode'],'cross_update_search');a.close()

    def test_new_cross_model_job_waits_for_incomplete_vectors(self):
        import hashlib
        from astrbot_plugin_digital_memory_store.auto_model import prepare
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            text='测试用户原话';sha=hashlib.sha256(text.encode()).hexdigest()
            with db:
                for i in (1,2):
                    db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',
                        (i,s,'f'+str(i),text,sha,f'2025-01-0{i}T12:00:00+00:00'))
                    db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',(s,i,'done' if i==1 else 'pending','p'))
                db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(s,2,key,sha,'[1.0,0.0]'))
            self.assertIsNone(prepare(db,policy='p',model_key=key))
            self.assertEqual(db.execute('SELECT status FROM dl_auto_model_jobs WHERE scope=? AND seq=2',(s,)).fetchone()[0],'waiting_vectors')
            with db:db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(s,1,key,sha,'[1.0,0.0]'))
            snapshot=prepare(db,policy='p',model_key=key)
            self.assertEqual([x['seq'] for x in snapshot['sources']],[1,2]);a.close()

    def test_cross_event_semantic_model_hint_supplies_sources_and_invalidates(self):
        import hashlib,json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish,read_relation_hint,requeue_changed_hints,unresolved_hint_sources
        from astrbot_plugin_digital_memory_store.auto_cross_update import cross_update_context
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);a.automatic_memory_enabled=True
            s=a.scope('1','s');other=a.scope('2','s');key=a._index().model_key;db=a.flow.db
            texts=['原来住在青岛东部','上午看了一部电影','今年搬到了深圳南山']
            with db:
                for i,text in enumerate(texts,1):
                    sha=hashlib.sha256(text.encode()).hexdigest();stamp=f'2025-01-0{i}T12:00:00+00:00';topic='t'+str(i)
                    db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',(i,s,'f'+str(i),text,sha,stamp))
                    db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,topic,key,stamp))
                    db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',(s,i,key,topic,sha,stamp,'测试'))
                    db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(s,i,key,sha,'[0.0,1.0]' if i==2 else '[1.0,0.0]'))
                    db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',(s,i,'pending' if i==3 else 'done','p'))
            snapshot=prepare(db,policy='p',model_key=key)
            self.assertEqual([x['seq'] for x in snapshot['sources']],[1,3])
            self.assertEqual(snapshot['sources'][0]['mode'],'cross_update_search')
            with db:db.execute("UPDATE dl_auto_topic_members SET topic_id='changed' WHERE scope=? AND seq=2",(s,))
            self.assertFalse(finish(db,snapshot,'{"verdict":"related","quote_a":"青岛","quote_b":"深圳","relation":"update"}'))
            self.assertFalse(db.execute('SELECT 1 FROM dl_auto_relation_hints WHERE scope=?',(s,)).fetchone())
            with db:db.execute('DELETE FROM dl_auto_model_limit')
            snapshot=prepare(db,policy='p',model_key=key)
            self.assertTrue(finish(db,snapshot,'{"verdict":"related","quote_a":"青岛","quote_b":"深圳","relation":"update"}'))
            self.assertEqual(read_relation_hint(db,s,1,3),'可能更新')
            cutoff='2025-02-01T00:00:00+00:00'
            blocked,packet=cross_update_context(db,key,a._index().embedder,s,'书',cutoff,1500,'q',set())
            self.assertTrue(blocked);data=json.loads(packet)
            self.assertEqual([r['原话'] for r in data['可能相关原话']],[texts[0],texts[2]])
            self.assertIn('未确认',data['可能的更新关系'][0]['关系'])
            self.assertEqual(cross_update_context(db,key,a._index().embedder,other,'书',cutoff,1500,'q',set()),(False,''))
            rows=db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? AND seq IN (1,3) ORDER BY seq',(s,)).fetchall()
            focus={'auto_sources':[{'seq':r['seq'],'sha':r['content_sha'],'time':r['recorded_at']} for r in rows],
                   'cross_query':'书','remaining':2}
            follow,_=a.context_turn(s,'后来呢',focus,budget=1500,current_message_id='q2',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            self.assertEqual(json.loads(follow)['待审相关序号'],[1,3])
            with db:db.execute("UPDATE dl_auto_topic_members SET topic_id='other' WHERE scope=? AND seq=2",(s,))
            self.assertIsNone(read_relation_hint(db,s,1,3))
            self.assertEqual(requeue_changed_hints(db,key,'wrong-policy'),0)
            self.assertEqual(requeue_changed_hints(db,key,'p'),1)
            self.assertEqual(unresolved_hint_sources(db,s,key,{1,3}),{1,3})
            with db:db.execute('DELETE FROM dl_auto_model_limit')
            retry=prepare(db,policy='p',model_key=key)
            self.assertEqual([x['seq'] for x in retry['sources']],[1,3])
            self.assertTrue(finish(db,retry,'{"verdict":"related","quote_a":"青岛","quote_b":"深圳","relation":"update"}'))
            self.assertFalse(unresolved_hint_sources(db,s,key,{1,3}));a.close()

    def test_cross_event_semantic_ties_and_missing_vectors_do_not_select(self):
        import hashlib
        from astrbot_plugin_digital_memory_store.auto_model import _cross_snapshot
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            text='不同事件的原话';sha=hashlib.sha256(text.encode()).hexdigest()
            with db:
                for i in range(1,4):
                    stamp=f'2025-01-0{i}T12:00:00+00:00';topic='t'+str(i)
                    db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',(i,s,'f'+str(i),text,sha,stamp))
                    db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,topic,key,stamp))
                    db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',(s,i,key,topic,sha,stamp,'测试'))
                    db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(s,i,key,sha,'[1.0,0.0]'))
                    db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',(s,i,'pending','p'))
            self.assertIsNone(_cross_snapshot(db,s,key,3,'p',choose=True)[1])
            with db:db.execute('DELETE FROM dl_auto_source_vectors WHERE scope=? AND seq=1',(s,))
            self.assertIsNone(_cross_snapshot(db,s,key,3,'p',choose=True));a.close()

    def test_large_topic_model_snapshot_and_unique_semantic_candidate(self):
        import hashlib,json
        from astrbot_plugin_digital_memory_store.auto_model import _topic_snapshot,_semantic_topic_candidate,prepare,finish,read_relation_hint
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            content='事件的原话';sha=hashlib.sha256(content.encode()).hexdigest();stamp='2025-01-01T12:00:00+00:00';later='2025-01-02T12:00:00+00:00'
            with db:
                db.executemany('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',
                    [(i,s,'f'+str(i),content,sha,later if i==5002 else stamp) for i in range(1,5003)])
                db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,'large',key,stamp))
                db.executemany('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',
                    [(s,i,key,'large',sha,later if i==5002 else stamp,'测试') for i in range(1,5003)])
                db.executemany('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',
                    [(s,i,key,sha,'[1.0,0.0]' if i in (5001,5002) else '[0.0,1.0]') for i in range(1,5003)])
                db.executemany('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',
                    [(s,i,'pending' if i==5002 else 'done','') for i in range(1,5003)])
            snapshot=_topic_snapshot(db,s,key,'large',5002)
            old_digest=hashlib.sha256(json.dumps([[i,sha,later if i==5002 else stamp] for i in range(1,5003)],separators=(',',':')).encode()).hexdigest()
            self.assertEqual(snapshot,(5002,old_digest))
            candidate=_semantic_topic_candidate(db,s,key,'large',5002,'')
            self.assertEqual(candidate[0]['seq'],5001);self.assertEqual(candidate[2],old_digest)
            request=prepare(db,model_key=key)
            self.assertEqual([x['seq'] for x in request['sources']],[5001,5002])
            self.assertTrue(finish(db,request,'{"verdict":"related","quote_a":"原话","quote_b":"原话","relation":"update"}'))
            self.assertEqual(read_relation_hint(db,s,5001,5002),'可能更新')
            with db:db.execute('DELETE FROM dl_auto_source_vectors WHERE scope=? AND seq=2500',(s,))
            self.assertIsNone(_semantic_topic_candidate(db,s,key,'large',5002,''))
            with db:db.execute("UPDATE dl_auto_topic_members SET content_sha='bad' WHERE scope=? AND seq=2500",(s,))
            self.assertIsNone(_topic_snapshot(db,s,key,'large',5002));a.close()

    def test_recheck_prioritizes_original_pair_and_respects_policy(self):
        from astrbot_plugin_digital_memory_store.auto_model import prepare
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            texts=['我住在北京东城区','后来我入住了南山区','后来我入住了南山区附近']
            with db:
                for i,text in enumerate(texts,1):
                    sha=hashlib.sha256(text.encode()).hexdigest();stamp=f'2025-01-0{i}T12:00:00+00:00'
                    db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',(i,s,'f'+str(i),text,sha,stamp))
                    db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',(s,i,key,'topic',sha,stamp,'测试'))
                    db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(s,i,key,sha,'[1.0,0.0]'))
                    db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',(s,i,'pending' if i==3 else 'done','p'))
                db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,'topic',key,'2025-01-01T00:00:00+00:00'))
                db.execute('INSERT INTO dl_auto_hint_rechecks VALUES(?,?,?,?)',(s,key,3,1))
            snapshot=prepare(db,policy='p',model_key=key)
            self.assertEqual([x['seq'] for x in snapshot['sources']],[1,3])
            with db:
                db.execute("UPDATE dl_auto_model_jobs SET policy='other' WHERE scope=? AND seq=1",(s,))
                db.execute("UPDATE dl_auto_model_jobs SET status='pending',attempts=0 WHERE scope=? AND seq=3",(s,))
                db.execute('DELETE FROM dl_auto_model_limit')
            snapshot=prepare(db,policy='p',model_key=key)
            self.assertEqual([x['seq'] for x in snapshot['sources']],[2,3]);a.close()

    def test_uncertain_recheck_retries_after_reorganization_and_resolves_original_pair(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish,requeue_changed_hints,unresolved_hint_sources
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True;a.auto_model_enabled=True
            s=a.scope('1','s');db=a.flow.db;key=a._index().model_key
            for i,t in enumerate(['书店活动时间原定周六','书店准备买小说','书店活动时间改到周日'],1):
                a.capture(s,str(i),t,recorded_at=f'2025-01-0{i}T12:00:00+00:00')
            process_jobs(db);a.sync_auto_source_vectors();a.sync_auto_topics()
            with db:db.execute("UPDATE dl_auto_model_jobs SET status='done' WHERE seq IN (1,2)")
            snapshot=prepare(db,model_key=key)
            self.assertTrue(finish(db,snapshot,'{"verdict":"related","quote_a":"周六","quote_b":"周日","relation":"update"}'))
            with db:db.execute("UPDATE dl_capture SET recorded_at='2025-01-02T13:00:00+00:00' WHERE scope=? AND seq=2",(s,))
            process_jobs(db);a.sync_auto_source_vectors();a.sync_auto_topics()
            self.assertEqual(requeue_changed_hints(db,key),1)
            with db:db.execute('DELETE FROM dl_auto_model_limit')
            retry=prepare(db,model_key=key);self.assertEqual([x['seq'] for x in retry['sources']],[1,3])
            self.assertTrue(finish(db,retry,'{"verdict":"uncertain","quote_a":"","quote_b":""}'))
            self.assertEqual(unresolved_hint_sources(db,s,key,{3}),{1,3})
            self.assertEqual(db.execute('SELECT status FROM dl_auto_model_jobs WHERE scope=? AND seq=3',(s,)).fetchone()[0],'done')
            with db:db.execute("UPDATE dl_capture SET recorded_at='2025-01-02T14:00:00+00:00' WHERE scope=? AND seq=2",(s,))
            process_jobs(db);a.sync_auto_source_vectors();a.sync_auto_topics()
            self.assertEqual(db.execute('SELECT status FROM dl_auto_model_jobs WHERE scope=? AND seq=3',(s,)).fetchone()[0],'pending')
            a.close();a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True;db=a.flow.db
            with db:db.execute('DELETE FROM dl_auto_model_limit')
            retry=prepare(db,model_key=key);self.assertEqual([x['seq'] for x in retry['sources']],[1,3])
            self.assertTrue(finish(db,retry,'{"verdict":"related","quote_a":"周六","quote_b":"周日","relation":"update"}'))
            self.assertFalse(unresolved_hint_sources(db,s,key,{3}));a.close()

    def test_stale_hint_relocates_when_old_source_deleted(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish,requeue_changed_hints,unresolved_hint_sources
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True;a.auto_model_enabled=True
            s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            for i,t in enumerate(['书店活动时间原定周六','书店准备买小说','书店活动时间改到周日'],1):
                a.capture(s,str(i),t,recorded_at=f'2025-01-0{i}T12:00:00+00:00')
            process_jobs(db);a.sync_auto_source_vectors();a.sync_auto_topics()
            with db:db.execute("UPDATE dl_auto_model_jobs SET status='done' WHERE seq IN (1,2)")
            snapshot=prepare(db,model_key=key)
            self.assertEqual([x['seq'] for x in snapshot['sources']],[1,3])
            self.assertTrue(finish(db,snapshot,'{"verdict":"related","quote_a":"周六","quote_b":"周日","relation":"update"}'))
            with db:
                db.execute('DELETE FROM dl_capture WHERE scope=? AND seq=1',(s,))
                db.execute('DELETE FROM dl_auto_model_limit')
            self.assertEqual(requeue_changed_hints(db,key),1)
            retry=prepare(db,model_key=key)
            self.assertEqual([x['seq'] for x in retry['sources']],[2,3])
            self.assertTrue(finish(db,retry,'{"verdict":"related","quote_a":"书店","quote_b":"周日","relation":"update"}'))
            self.assertEqual(unresolved_hint_sources(db,s,key,{3}),{1,3})
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],2)
            a.close()

    def test_semantic_candidate_compares_different_wording_without_change_keyword(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish,read_relation_hint,_semantic_topic_candidate
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True;a.auto_model_enabled=True
            s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            a.capture(s,'1','原本周六去逛书店',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','周日会去那边买小说',recorded_at='2025-01-03T12:00:00+00:00')
            process_jobs(db);a.sync_auto_source_vectors()
            rows=db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? ORDER BY seq',(s,)).fetchall()
            with db:
                db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,'topic',key,rows[0]['recorded_at']))
                db.executemany('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',
                    [(s,r['seq'],key,'topic',r['content_sha'],r['recorded_at'],'测试') for r in rows])
                db.execute("UPDATE dl_auto_source_vectors SET vector_json='[1.0,0.0]' WHERE scope=?",(s,))
                db.execute("UPDATE dl_auto_model_jobs SET status='done' WHERE scope=? AND seq=1",(s,))
            snapshot=prepare(db,model_key=key)
            self.assertEqual([x['seq'] for x in snapshot['sources']],[1,2])
            self.assertEqual(snapshot['sources'][0]['mode'],'topic_update_search')
            self.assertTrue(finish(db,snapshot,'{"verdict":"related","quote_a":"周六","quote_b":"周日","relation":"update"}'))
            self.assertEqual(read_relation_hint(db,s,1,2),'可能更新')
            import json
            packet=json.loads(a._auto_topic_context(s,'购买小说','2025-02-01T00:00:00+00:00',1000,'q',set()))
            self.assertEqual([x['原话'] for x in packet['可能相关原话']],['原本周六去逛书店','周日会去那边买小说'])
            self.assertIn('未确认',packet['可能的更新关系'][0]['关系'])
            with db:db.execute('DELETE FROM dl_auto_source_vectors WHERE scope=? AND seq=1',(s,))
            self.assertIsNone(_semantic_topic_candidate(db,s,key,'topic',2,''));a.close()

    def test_semantic_topic_candidate_rejects_equal_scoring_prior_sources(self):
        from astrbot_plugin_digital_memory_store.auto_model import _semantic_topic_candidate
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            text='可能有关的文字';sha=hashlib.sha256(text.encode()).hexdigest();stamp='2025-01-01T12:00:00+00:00'
            with db:
                db.executemany('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',[(i,s,'f'+str(i),text,sha,stamp) for i in range(1,4)])
                db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,'topic',key,stamp))
                db.executemany('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',[(s,i,key,'topic',sha,stamp,'测试') for i in range(1,4)])
                db.executemany('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',[(s,i,key,sha,'[1.0,0.0]') for i in range(1,4)])
                db.executemany('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',[(s,i,'pending','') for i in range(1,4)])
            self.assertIsNone(_semantic_topic_candidate(db,s,key,'topic',3,''));a.close()

    def test_skipped_update_job_retries_after_topic_index_is_ready(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish,read_relation_hint,requeue_changed_hints
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True;a.auto_model_enabled=True
            s=a.scope('1','s');key=a._index().model_key
            a.capture(s,'1','书店计划原来周六出发',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','书店计划改到周日出发',recorded_at='2025-01-03T12:00:00+00:00')
            db=a.flow.db;process_jobs(db)
            self.assertIsNone(prepare(db,model_key=key))
            self.assertEqual(db.execute('SELECT status FROM dl_auto_model_jobs WHERE scope=? AND seq=2',(s,)).fetchone()[0],'waiting_vectors')
            a.sync_auto_source_vectors();a.sync_auto_topics()
            self.assertEqual(db.execute('SELECT status FROM dl_auto_model_jobs WHERE scope=? AND seq=2',(s,)).fetchone()[0],'waiting_vectors')
            a.close();a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True;db=a.flow.db
            snapshot=prepare(db,model_key=key)
            self.assertEqual([x['seq'] for x in snapshot['sources']],[1,2])
            self.assertTrue(finish(db,snapshot,'{"verdict":"related","quote_a":"周六","quote_b":"周日","relation":"update"}'))
            self.assertEqual(read_relation_hint(db,s,1,2),'可能更新')
            with db:
                db.execute("UPDATE dl_auto_model_jobs SET status='skipped' WHERE scope=? AND seq=2",(s,))
                db.execute("DELETE FROM dl_auto_schema_migrations WHERE migration='retry_indexed_update_jobs_v1'")
            a.close();a=Store(d,'fixture-only',SemanticFixtureEncoder);db=a.flow.db
            self.assertEqual(db.execute('SELECT status FROM dl_auto_model_jobs WHERE scope=? AND seq=2',(s,)).fetchone()[0],'pending')
            a.close()

    def test_cross_topic_chain_over_five_keeps_full_sources_and_followup(self):
        import json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_cross_update import cross_update_context
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True
            s=a.scope('1','s');key=a._index().model_key
            texts=['书店计划原定周六']+[f'书店计划改到周{x}' for x in '日一二三四']
            for i,t in enumerate(texts,1):a.capture(s,str(i),t,recorded_at=f'2025-01-0{i}T12:00:00+00:00')
            db=a.flow.db;process_jobs(db);a.sync_auto_source_vectors();a.sync_auto_topics()
            with db:db.execute("UPDATE dl_auto_topic_members SET topic_id='different' WHERE scope=? AND seq=2",(s,))
            cutoff='2025-02-01T00:00:00+00:00'
            blocked,packet=cross_update_context(db,key,a._index().embedder,s,'购买小说',cutoff,2000,'q',set())
            self.assertTrue(blocked);self.assertEqual([r['原话'] for r in json.loads(packet)['可能相关原话']],texts)
            rows=db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? ORDER BY seq',(s,)).fetchall()
            focus={'auto_sources':[{'seq':r['seq'],'sha':r['content_sha'],'time':r['recorded_at']} for r in rows],
                   'cross_query':'购买小说','remaining':2}
            follow,_=a.context_turn(s,'后来呢',focus,budget=2000,current_message_id='q2',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            self.assertEqual(json.loads(follow)['待审相关序号'],[1,2,3,4,5,6])
            self.assertEqual(cross_update_context(db,key,a._index().embedder,s,'购买小说',cutoff,100,'q',set()),(True,''))
            a.close()

    def test_changed_group_retries_skipped_sources_but_unchanged_group_does_not(self):
        from astrbot_plugin_digital_memory_store.auto_organize import organize_recent,read_group
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s');key=a._index().model_key
            a.capture(s,'1','计划去青岛海边散步');a.capture(s,'2','青岛海边散步之后去吃饭')
            db=a.flow.db
            with db:db.executemany('INSERT INTO dl_auto_topic_skips VALUES(?,?,?)',[(s,1,key),(s,2,key),(other,1,key)])
            organize_recent(db,s)
            self.assertEqual(read_group(db,s,1)['members'],[1,2])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dl_auto_topic_skips WHERE scope=?',(s,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dl_auto_topic_skips WHERE scope=?',(other,)).fetchone()[0],1)
            with db:db.execute('INSERT INTO dl_auto_topic_skips VALUES(?,?,?)',(s,1,key))
            organize_recent(db,s)
            self.assertTrue(db.execute('SELECT 1 FROM dl_auto_topic_skips WHERE scope=? AND seq=1',(s,)).fetchone())
            a.close()

    def test_large_topic_accepts_group_member_only_without_opposing_evidence(self):
        import hashlib,json
        from unittest.mock import patch
        from astrbot_plugin_digital_memory_store.auto_topics import sync_topics
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            value='同一件事情';sha=hashlib.sha256(value.encode()).hexdigest()
            old='2025-01-01T12:00:00+00:00';new='2025-01-02T12:00:00+00:00'
            with db:
                db.executemany('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',
                    [(i,s,'f'+str(i),value,sha,new if i==102 else old) for i in range(1,103)])
                db.executemany('INSERT INTO dl_auto_jobs(scope,seq,status) VALUES(?,?,?)',[(s,i,'done') for i in range(1,103)])
                db.executemany('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',[(s,i,key,sha,'[1.0,0.0]') for i in range(1,103)])
                db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,'large',key,old))
                db.executemany('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',
                    [(s,i,key,'large',sha,old,'测试') for i in range(1,102)])
                manifest=json.dumps([{'seq':1,'sha256':sha,'recorded_at':old,'mode':'rebuild_split'},
                                     {'seq':102,'sha256':sha,'recorded_at':new,'mode':'rebuild_split'}])
                db.execute('INSERT INTO dl_auto_separations VALUES(?,?,?,?,?,1)',(s,102,1,manifest,new))
            group={'members':[101,102],'basis':'测试关联'}
            with patch('astrbot_plugin_digital_memory_store.auto_topics.read_group',return_value=group):
                sync_topics(db,key)
                self.assertFalse(db.execute('SELECT 1 FROM dl_auto_topic_members WHERE scope=? AND seq=102',(s,)).fetchone())
                with db:
                    db.execute('DELETE FROM dl_auto_separations WHERE scope=?',(s,))
                    db.execute('DELETE FROM dl_auto_topic_skips WHERE scope=? AND seq=102',(s,))
                sync_topics(db,key)
            self.assertEqual(db.execute('SELECT topic_id FROM dl_auto_topic_members WHERE scope=? AND seq=102',(s,)).fetchone()[0],'large')
            a.close()

    def test_historical_unrelated_survives_rebuild_and_invalidates_on_source_change(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish,separated
        from astrbot_plugin_digital_memory_store.auto_topology import rebuild_conflicted_topics
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True;a.auto_model_enabled=True
            s=a.scope('1','s');key=a._index().model_key
            for i,text in enumerate(['书店活动时间原定周六','书店准备买小说','书店活动时间改到周日'],1):
                a.capture(s,str(i),text,recorded_at=f'2025-01-0{i}T12:00:00+00:00')
            db=a.flow.db;process_jobs(db);a.sync_auto_source_vectors();a.sync_auto_topics()
            db.execute("UPDATE dl_auto_model_jobs SET status='done' WHERE seq IN (1,2)");db.commit()
            snapshot=prepare(db,model_key=key)
            self.assertEqual([x['seq'] for x in snapshot['sources']],[1,3])
            self.assertTrue(finish(db,snapshot,'{"verdict":"unrelated","quote_a":"周六","quote_b":"周日"}'))
            self.assertTrue(separated(db,s,1,3));self.assertEqual(rebuild_conflicted_topics(db,key),1)
            self.assertTrue(separated(db,s,1,3))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],3)
            a.close();a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True;db=a.flow.db
            a.sync_auto_topics()
            topics={r['seq']:r['topic_id'] for r in db.execute('SELECT seq,topic_id FROM dl_auto_topic_members WHERE scope=?',(s,))}
            self.assertIn(1,topics);self.assertIn(3,topics);self.assertNotEqual(topics[1],topics[3])
            db.execute("UPDATE dl_capture SET content='已变更' WHERE scope=? AND seq=1",(s,));db.commit()
            self.assertFalse(separated(db,s,1,3));a.close()

    def test_conflicted_topic_over_100_sources_can_be_rebuilt(self):
        import hashlib,json
        from astrbot_plugin_digital_memory_store.auto_topology import rebuild_conflicted_topics
        from astrbot_plugin_digital_memory_store.auto_model import separated
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            text='原始记录';sha=hashlib.sha256(text.encode()).hexdigest();stamp='2025-01-01T12:00:00+00:00'
            with db:
                db.executemany('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',
                    [(i,s,'f'+str(i),text,sha,stamp) for i in range(1,102)])
                db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,'large',key,stamp))
                db.executemany('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',
                    [(s,i,key,'large',sha,stamp,'测试') for i in range(1,102)])
                manifest=json.dumps([{'seq':i,'sha256':sha,'recorded_at':stamp} for i in (1,2)])
                db.execute('INSERT INTO dl_auto_separations VALUES(?,?,?,?,?,1)',(s,2,1,manifest,stamp))
            self.assertTrue(separated(db,s,1,2));self.assertEqual(rebuild_conflicted_topics(db,key),1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dl_auto_topic_members WHERE scope=?',(s,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],101)
            self.assertTrue(separated(db,s,1,2));a.close()

    def test_unlinked_large_history_keeps_invalid_groups_and_excludes_multi_member_topics(self):
        import hashlib,json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True
            s=a.scope('1','s');db=a.flow.db;key=a._index().model_key;stamp='2025-01-01T12:00:00+00:00'
            texts=['原计划周六去书店']+['无关天气']*5000
            rows=[(i,s,'fake-'+str(i),text,hashlib.sha256(text.encode()).hexdigest(),stamp)
                  for i,text in enumerate(texts,1)]
            with db:
                db.executemany('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',rows)
                db.executemany('INSERT INTO dl_auto_jobs(scope,seq,status) VALUES(?,?,?)',[(s,i,'done') for i in range(1,5002)])
                db.executemany('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',
                    [(s,r[0],key,r[4],'[1.0,0.0]' if r[0]==1 else '[0.0,1.0]') for r in rows])
                # 过期的单向分组不能让来源永久退出单条召回。
                db.execute('INSERT INTO dl_auto_groups VALUES(?,?,?,?,?,?)',(s,1,99999,rows[0][4],'invalid',stamp))
            args=(s,'购买小说','2025-02-01T00:00:00+00:00',800,'q',set())
            calls=[];db.set_trace_callback(calls.append)
            try:result=a._semantic_unlinked_context(*args)
            finally:db.set_trace_callback(None)
            self.assertEqual(json.loads(result)['未整理历史原话'],texts[0])
            self.assertLess(len(calls),50)
            # 已进入多成员推测事件的原话不能作为独立来源绕过事件保护。
            with db:
                db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,'topic',key,stamp))
                db.executemany('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',
                    [(s,r[0],key,'topic',r[4],stamp,'测试') for r in rows[:2]])
            self.assertFalse(a._semantic_unlinked_context(*args));a.close()

    def test_auto_vectors_batch_and_isolate_failed_sources(self):
        class BatchEncoder(FakeEncoder):
            model_key='batch-fixture-v1'
            def __init__(self,path):self.calls=[]
            def encode_documents(self,texts):
                self.calls.append(tuple(texts))
                if any('故障' in x for x in texts):raise ValueError('invalid input')
                return [[1.,0.] for _ in texts]
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',BatchEncoder);a.automatic_memory_enabled=True;s=a.scope('1','s')
            for i,text in enumerate(['书店计划','故障文本','准备午饭'],1):a.capture(s,str(i),text,recorded_at=f'2025-01-0{i}T12:00:00+00:00')
            self.assertEqual(a.sync_auto_source_vectors(),2)
            encoder=a._index().embedder
            self.assertEqual(len(encoder.calls[0]),3)
            self.assertEqual({r[0] for r in a.flow.db.execute('SELECT seq FROM dl_auto_source_vectors WHERE scope=?',(s,))},{1,3})
            self.assertEqual(a.flow.db.execute('SELECT attempts FROM dl_auto_vector_failures WHERE scope=? AND seq=2',(s,)).fetchone()[0],1)
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],3);a.close()

    def test_auto_vectors_discard_source_changed_during_batch(self):
        import hashlib
        class ChangingEncoder(FakeEncoder):
            model_key='changing-batch-fixture-v1'
            def __init__(self,path):self.hook=None
            def encode_documents(self,texts):
                if self.hook:self.hook();self.hook=None
                return [[1.,0.] for _ in texts]
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',ChangingEncoder);a.automatic_memory_enabled=True;s=a.scope('1','s')
            a.capture(s,'1','原本的书店计划',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','其他书店细节',recorded_at='2025-01-02T12:00:00+00:00')
            def change():
                text='更新的书店计划';sha=hashlib.sha256(text.encode()).hexdigest()
                with a.flow.db:a.flow.db.execute('UPDATE dl_capture SET content=?,content_sha=? WHERE scope=? AND seq=1',(text,sha,s))
            a._index().embedder.hook=change
            self.assertEqual(a.sync_auto_source_vectors(),1)
            self.assertFalse(a.flow.db.execute('SELECT 1 FROM dl_auto_source_vectors WHERE scope=? AND seq=1',(s,)).fetchone())
            self.assertFalse(a.flow.db.execute('SELECT 1 FROM dl_auto_vector_failures WHERE scope=? AND seq=1',(s,)).fetchone())
            self.assertEqual(a.sync_auto_source_vectors(),1);a.close()

    def test_text_index_queue_backfill_restart_and_source_mutations(self):
        import hashlib
        from astrbot_plugin_digital_memory_store.auto_cross_update import sync_text_index,_indexed_candidates
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');db=a.flow.db
            for i,value in enumerate(['书店计划原定周六','书店计划改到周日','其他天气'],1):
                a.capture(s,str(i),value,recorded_at=f'2025-01-0{i}T12:00:00+00:00')
            current_hash=hashlib.sha256((s+'\0q').encode()).hexdigest()
            self.assertEqual(sync_text_index(db,limit=1),1);self.assertIsNone(_indexed_candidates(db,s,current_hash))
            a.close();a=Store(d,'fixture-only',FakeEncoder);db=a.flow.db
            self.assertEqual(sync_text_index(db,limit=10),2)
            self.assertEqual(_indexed_candidates(db,s,current_hash),{1,2})
            text='书店计划改到周一';digest=hashlib.sha256(text.encode()).hexdigest()
            db.execute('UPDATE dl_capture SET content=?,content_sha=? WHERE scope=? AND seq=2',(text,digest,s));db.commit()
            self.assertIsNone(_indexed_candidates(db,s,current_hash));self.assertEqual(sync_text_index(db,limit=10),1)
            self.assertEqual(_indexed_candidates(db,s,current_hash),{1,2})
            a.capture(s,'4','书店计划取消了',recorded_at='2025-01-04T12:00:00+00:00')
            self.assertIsNone(_indexed_candidates(db,s,current_hash));self.assertEqual(sync_text_index(db,limit=10),1)
            self.assertEqual(_indexed_candidates(db,s,current_hash),{1,2,4})
            db.execute('DELETE FROM dl_capture WHERE scope=? AND seq=4',(s,));db.commit()
            self.assertEqual(_indexed_candidates(db,s,current_hash),{1,2})
            self.assertFalse(db.execute('SELECT 1 FROM dl_auto_text_grams WHERE scope=? AND seq=4',(s,)).fetchone())
            a.close()

    def test_cross_update_scans_beyond_5000_sources(self):
        import hashlib,json
        from astrbot_plugin_digital_memory_store.auto_cross_update import cross_update_context,sync_text_index,_indexed_candidates
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);a.automatic_memory_enabled=True;s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            raw=['天气平静']*5000+['书店计划原定周六','书店计划改到周日']
            with db:
                for i,value in enumerate(raw,1):
                    sha=hashlib.sha256(value.encode()).hexdigest();stamp='2025-01-01T12:00:00+00:00' if i<5002 else '2025-01-02T12:00:00+00:00'
                    db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',(i,s,'fake-'+str(i),value,sha,stamp))
                    db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(s,i,key,sha,'[1.0,0.0]' if i>=5001 else '[0.0,1.0]'))
            blocked,packet=cross_update_context(db,key,a._index().embedder,s,'看书','2025-02-01T00:00:00+00:00',1000,'q',set())
            self.assertTrue(blocked);self.assertEqual(json.loads(packet)['待审相关序号'],[5001,5002])
            self.assertEqual(sync_text_index(db,limit=5002),5002)
            import hashlib
            current_hash=hashlib.sha256((s+'\0q').encode()).hexdigest()
            self.assertEqual(_indexed_candidates(db,s,current_hash),{5001,5002})
            blocked2,packet2=cross_update_context(db,key,a._index().embedder,s,'看书','2025-02-01T00:00:00+00:00',1000,'q',set())
            self.assertEqual((blocked2,packet2),(blocked,packet))
            changed='书店计划改到周一';changed_sha=hashlib.sha256(changed.encode()).hexdigest()
            db.execute('UPDATE dl_capture SET content=?,content_sha=? WHERE scope=? AND seq=5002',(changed,changed_sha,s));db.commit()
            self.assertIsNone(_indexed_candidates(db,s,current_hash))
            self.assertEqual(sync_text_index(db,limit=10),1);self.assertEqual(_indexed_candidates(db,s,current_hash),{5001,5002})
            a.close()

    def test_large_automatic_topic_supplies_checked_excerpt(self):
        import hashlib,json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);a.automatic_memory_enabled=True;s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            content='书店讨论';sha=hashlib.sha256(content.encode()).hexdigest();stamp='2025-01-01T12:00:00+00:00'
            with db:
                db.executemany('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',
                               [(i,s,'synthetic-'+str(i),content,sha,stamp) for i in range(1,102)])
                db.executemany('INSERT INTO dl_auto_jobs(scope,seq,status) VALUES(?,?,?)',[(s,i,'done') for i in range(1,102)])
                db.executemany('INSERT INTO dl_auto_source_vectors(scope,seq,model_key,content_sha,vector_json) VALUES(?,?,?,?,?)',
                               [(s,i,key,sha,'[1.0,0.0]') for i in range(1,102)])
                db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,'topic',key,stamp))
                db.executemany('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',[(s,i,key,'topic',sha,stamp,'测试') for i in range(1,102)])
            result=a._auto_topic_context(s,'看书','2025-02-01T00:00:00+00:00',1500,'q',set())
            data=json.loads(result);self.assertIn('共 101 条',data['覆盖说明'])
            self.assertEqual([r['序号'] for r in data['可能相关原话']],[99,100,101])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],101)
            # 损坏或缺失的事件成员不能在摘录中被悄悄忽略。
            with db:db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',(s,99999,key,'topic',sha,stamp,'测试'))
            self.assertEqual(a._auto_topic_context(s,'看书','2025-02-01T00:00:00+00:00',1500,'q',set()),'')
            a.close()

    def test_automatic_topics_can_process_more_than_5000_prior_sources(self):
        import hashlib
        from astrbot_plugin_digital_memory_store.auto_topics import sync_topics
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            content='书店讨论';sha=hashlib.sha256(content.encode()).hexdigest();stamp='2025-01-01T12:00:00+00:00'
            with db:
                db.executemany('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',
                               [(i,s,'synthetic-'+str(i),content,sha,stamp) for i in range(1,5003)])
                db.executemany('INSERT INTO dl_auto_jobs(scope,seq,status) VALUES(?,?,?)',[(s,i,'done') for i in range(1,5003)])
                db.executemany('INSERT INTO dl_auto_source_vectors(scope,seq,model_key,content_sha,vector_json) VALUES(?,?,?,?,?)',
                               [(s,i,key,sha,'[1.0,0.0]') for i in range(1,5003)])
                db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,'topic',key,stamp))
                db.executemany('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',[(s,i,key,'topic',sha,stamp,'测试') for i in range(1,5002)])
            self.assertEqual(sync_topics(db,key,limit=1),1)
            self.assertEqual(db.execute('SELECT topic_id FROM dl_auto_topic_members WHERE scope=? AND seq=5002',(s,)).fetchone()[0],'topic')
            a.close()

    def test_topic_context_scans_beyond_5000_sources(self):
        import hashlib,json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);a.automatic_memory_enabled=True;s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            content='书店讨论';sha=hashlib.sha256(content.encode()).hexdigest();stamp='2025-01-01T12:00:00+00:00'
            with db:
                db.executemany('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',[(i,s,'fake-'+str(i),content,sha,stamp) for i in range(1,5002)])
                db.executemany('INSERT INTO dl_auto_jobs(scope,seq,status) VALUES(?,?,?)',[(s,i,'done') for i in range(1,5002)])
                db.executemany('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',[(s,i,key,sha,'[1.0,0.0]') for i in range(1,5002)])
                db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,'topic',key,stamp))
                db.executemany('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',[(s,i,key,'topic',sha,stamp,'测试') for i in range(1,5002)])
            result=a._auto_topic_context(s,'看书','2025-02-01T00:00:00+00:00',1500,'q',set())
            self.assertIn('共 5001 条',json.loads(result)['覆盖说明'])
            # 紧随事件末尾的未归组更新仍须阻止输出旧事件。
            update='改到周日吧';update_sha=hashlib.sha256(update.encode()).hexdigest()
            with db:
                db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',
                           (5002,s,'fake-5002',update,update_sha,'2025-01-01T12:01:00+00:00'))
                db.execute('INSERT INTO dl_auto_jobs(scope,seq,status) VALUES(?,?,?)',(s,5002,'done'))
                db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(s,5002,key,update_sha,'[1.0,0.0]'))
            self.assertEqual(a._auto_topic_context(s,'看书','2025-02-01T00:00:00+00:00',1500,'q',set()),'')
            a.close()

    def test_cross_update_guard_does_not_block_large_history_without_changes(self):
        import hashlib
        from astrbot_plugin_digital_memory_store.auto_cross_update import cross_update_context
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');db=a.flow.db
            content='喜欢书店';sha=hashlib.sha256(content.encode()).hexdigest();stamp='2025-01-01T12:00:00+00:00'
            with db:db.executemany('INSERT INTO dl_capture(scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?)',
                [(s,'synthetic-'+str(i),content,sha,stamp) for i in range(5001)])
            self.assertEqual(cross_update_context(db,a._index().model_key,a._index().embedder,s,'书店','2025-02-01T00:00:00+00:00',1000,'q',set()),(False,''))
            db.execute("UPDATE dl_capture SET content='书店取消了' WHERE scope=? AND seq=1",(s,));db.commit()
            self.assertEqual(cross_update_context(db,a._index().model_key,a._index().embedder,s,'书店','2025-02-01T00:00:00+00:00',1000,'q',set()),(True,''));a.close()

    def test_cross_topic_update_supplies_both_sources_and_preserves_uncertainty(self):
        import json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_cross_update import cross_update_context
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True
            s=a.scope('1','s');other=a.scope('2','s');key=a._index().model_key
            a.capture(s,'1','书店计划原定周六',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','书店计划改到周日',recorded_at='2025-01-03T12:00:00+00:00')
            a.capture(other,'1','书店计划改到周一',recorded_at='2025-01-03T12:00:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            a.flow.db.execute("UPDATE dl_auto_topic_members SET topic_id='other-topic' WHERE scope=? AND seq=2",(s,));a.flow.db.commit()
            cutoff='2025-02-01T00:00:00+00:00'
            blocked,packet=cross_update_context(a.flow.db,key,a._index().embedder,s,'购买小说',cutoff,1000,'q',set())
            self.assertTrue(blocked);data=json.loads(packet)
            self.assertEqual([r['原话'] for r in data['可能相关原话']],['书店计划原定周六','书店计划改到周日'])
            self.assertIn('尚未确认',data['说明'])
            routed=json.loads(a.context_packet(s,'购买小说',budget=1000,current_message_id='q',as_of=datetime(2025,2,1,tzinfo=timezone.utc)))
            self.assertEqual(routed['线索方式'],'跨事件文字重合与变化词')
            source_rows=a.flow.db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? ORDER BY seq',(s,)).fetchall()
            focus={'auto_sources':[{'seq':r['seq'],'sha':r['content_sha'],'time':r['recorded_at']} for r in source_rows],
                   'cross_query':'购买小说','remaining':2}
            continued,next_focus=a.context_turn(s,'后来呢',focus,budget=1000,current_message_id='q2',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            self.assertEqual(json.loads(continued)['待审相关序号'],[1,2]);self.assertEqual(next_focus['remaining'],1)
            self.assertEqual(cross_update_context(a.flow.db,key,a._index().embedder,s,'购买小说',cutoff,10,'q',set()),(True,''))
            a.capture(s,'3','书店计划取消了',recorded_at='2025-01-04T12:00:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            blocked,expanded=cross_update_context(a.flow.db,key,a._index().embedder,s,'购买小说',cutoff,1000,'q',set())
            self.assertTrue(blocked);self.assertEqual([x['原话'] for x in json.loads(expanded)['可能相关原话']],
                ['书店计划原定周六','书店计划改到周日','书店计划取消了'])
            updated,updated_focus=a.context_turn(s,'后来呢',focus,budget=1000,current_message_id='q2',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            self.assertEqual(json.loads(updated)['待审相关序号'],[1,2,4])
            self.assertEqual([item['seq'] for item in updated_focus['auto_sources']],[1,2,4])
            new_sources=a.flow.db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? ORDER BY seq',(s,)).fetchall()
            new_focus={'auto_sources':[{'seq':r['seq'],'sha':r['content_sha'],'time':r['recorded_at']} for r in new_sources],
                       'cross_query':'购买小说','remaining':2}
            follow,_=a.context_turn(s,'后来呢',new_focus,budget=1000,current_message_id='q3',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            self.assertEqual(json.loads(follow)['待审相关序号'],[r['seq'] for r in new_sources])
            a.close()

    def test_cross_topic_update_does_not_join_unrelated_tied_sources(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_cross_update import cross_update_context
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True;s=a.scope('1','s')
            for i,text in enumerate(['书店计划第一件事情','书店计划第二件事情','书店计划取消了'],1):
                a.capture(s,str(i),text,recorded_at=f'2025-01-0{i}T12:00:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            result=cross_update_context(a.flow.db,a._index().model_key,a._index().embedder,s,'购买小说','2025-02-01T00:00:00+00:00',1000,'q',set())
            self.assertEqual(result,(True,''));a.close()

    def test_cross_topic_update_respects_opposing_model_evidence(self):
        import json
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_cross_update import cross_update_context
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True;s=a.scope('1','s');key=a._index().model_key
            a.capture(s,'1','书店计划原定周六',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','书店计划改到周日',recorded_at='2025-01-01T12:01:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            db=a.flow.db;db.execute("UPDATE dl_auto_topic_members SET topic_id='other' WHERE scope=? AND seq=2",(s,))
            sources=db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? ORDER BY seq',(s,)).fetchall()
            manifest=json.dumps([{'seq':r['seq'],'sha256':r['content_sha'],'recorded_at':r['recorded_at']} for r in sources])
            db.execute('INSERT OR REPLACE INTO dl_auto_separations VALUES(?,?,?,?,?,1)',(s,2,1,manifest,'2025-01-01T12:03:00+00:00'));db.commit()
            self.assertEqual(cross_update_context(db,key,a._index().embedder,s,'购买小说','2025-02-01T00:00:00+00:00',1000,'q',set()),(True,''))
            a.close()

    def test_historical_update_search_validates_full_topic_snapshot(self):
        import json
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish,read_relation_hint,requeue_changed_hints,unresolved_hint_sources
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s')
            a.automatic_memory_enabled=True;a.auto_model_enabled=True
            texts=['书店活动时间原定周六','书店准备买小说','书店活动时间改到周日']
            for i,value in enumerate(texts,1):a.capture(s,str(i),value,recorded_at=f'2025-01-0{i}T12:00:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            a.flow.db.execute("UPDATE dl_auto_model_jobs SET status='done' WHERE seq IN (1,2)");a.flow.db.commit()
            snap=prepare(a.flow.db,model_key=a._index().model_key)
            self.assertEqual([x['seq'] for x in snap['sources']],[1,3])
            self.assertEqual(snap['sources'][0]['mode'],'topic_update_search')
            self.assertTrue(finish(a.flow.db,snap,'{"verdict":"related","quote_a":"周六","quote_b":"周日","relation":"update"}'))
            self.assertEqual(read_relation_hint(a.flow.db,s,1,3),'可能更新')
            a.flow.db.execute("UPDATE dl_capture SET recorded_at='2025-01-02T13:00:00+00:00' WHERE scope=? AND seq=2",(s,));a.flow.db.commit()
            self.assertIsNone(read_relation_hint(a.flow.db,s,1,3))
            self.assertEqual(requeue_changed_hints(a.flow.db,a._index().model_key),0)
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            with a.flow.db:a.flow.db.execute("UPDATE dl_auto_source_vectors SET vector_json='[1.0,0.0]' WHERE scope=?",(s,))
            self.assertEqual(unresolved_hint_sources(a.flow.db,s,a._index().model_key,{1,3}),{1,3})
            self.assertEqual(requeue_changed_hints(a.flow.db,a._index().model_key),1)
            self.assertEqual(requeue_changed_hints(a.flow.db,a._index().model_key),0)
            self.assertFalse(a.flow.db.execute('SELECT 1 FROM dl_auto_relation_hints WHERE scope=?',(s,)).fetchone())
            self.assertEqual(unresolved_hint_sources(a.flow.db,s,a._index().model_key,{1,3}),{1,3})
            packet=json.loads(a._auto_topic_context(s,'购买小说','2025-02-01T00:00:00+00:00',1500,'q',set()))
            self.assertIn('变化核验提示',packet)
            a.close();a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True
            self.assertEqual(unresolved_hint_sources(a.flow.db,s,a._index().model_key,{1,3}),{1,3})
            a.flow.db.execute('DELETE FROM dl_auto_model_limit');a.flow.db.commit()
            retry=prepare(a.flow.db,model_key=a._index().model_key)
            self.assertEqual([x['seq'] for x in retry['sources']],[1,3])
            self.assertTrue(finish(a.flow.db,retry,'{"verdict":"related","quote_a":"周六","quote_b":"周日","relation":"update"}'))
            self.assertEqual(read_relation_hint(a.flow.db,s,1,3),'可能更新')
            self.assertFalse(unresolved_hint_sources(a.flow.db,s,a._index().model_key,{1,3}))
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],3);a.close()

    def test_historical_update_search_rejects_tied_candidates(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s')
            a.automatic_memory_enabled=True;a.auto_model_enabled=True
            texts=['书店活动时间周六','书店活动时间周日','书店准备买小说','书店活动时间改到周一']
            for i,value in enumerate(texts,1):a.capture(s,str(i),value,recorded_at=f'2025-01-0{i}T12:00:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            a.flow.db.execute("UPDATE dl_auto_model_jobs SET status='done' WHERE seq IN (1,2,3)");a.flow.db.commit()
            self.assertIsNone(prepare(a.flow.db,model_key=a._index().model_key))
            self.assertEqual(a.flow.db.execute('SELECT status FROM dl_auto_model_jobs WHERE scope=? AND seq=4',(s,)).fetchone()[0],'skipped')
            a.close()

    def test_long_topic_excerpt_keeps_changes_and_prior_context(self):
        import json
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);a.automatic_memory_enabled=True;s=a.scope('1','s')
            texts=['书店计划原定周六','书店计划改到周日']+[f'书店计划讨论内容{i}' for i in range(2,8)]
            for i,value in enumerate(texts,1):
                a.capture(s,str(i),value,recorded_at=f'2025-01-{i:02d}T12:00:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            packet=json.loads(a._auto_topic_context(s,'购买小说','2025-02-01T00:00:00+00:00',2000,'q',set()))
            selected=[r['原话'] for r in packet['可能相关原话']]
            self.assertIn(texts[0],selected);self.assertIn(texts[1],selected)
            self.assertEqual(selected[-3:],texts[-3:]);self.assertIn('未列出全部历史',packet['覆盖说明'])
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],8)
            a.close()

    def test_conflicted_long_topic_is_rebuilt_without_rejoining_opposing_sources(self):
        import json
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_organize import read_group,read_model_group
        from astrbot_plugin_digital_memory_store.auto_topology import source_has_conflict
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);a.automatic_memory_enabled=True
            s=a.scope('1','s');key=a._index().model_key
            for i,text in enumerate(['吃完早饭','决定准备午饭','今晚听音乐','回家收拾房间'],1):
                a.capture(s,str(i),text,recorded_at=f'2025-01-01T12:0{i}:00+00:00')
            db=a.flow.db;process_jobs(db);a.sync_auto_source_vectors();a.sync_auto_topics()
            # 构造已形成的多条推测事件，以及有来源快照的后续反向判断。
            db.execute("INSERT OR IGNORE INTO dl_auto_topics VALUES(?,?,?,?)",(s,'fixture',key,'2025-01-01T00:00:00+00:00'))
            db.execute("UPDATE dl_auto_topic_members SET topic_id='fixture' WHERE scope=? AND model_key=?",(s,key))
            rows=db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? AND seq IN (2,3) ORDER BY seq',(s,)).fetchall()
            manifest=json.dumps([{'seq':r['seq'],'sha256':r['content_sha'],'recorded_at':r['recorded_at']} for r in rows])
            for left,right in ((2,3),(3,2)):
                db.execute('INSERT OR REPLACE INTO dl_auto_model_pairs VALUES(?,?,?,?,?)',(s,left,right,manifest,'2025-01-01T12:05:00+00:00'))
            db.execute('INSERT INTO dl_auto_separations VALUES(?,?,?,?,?,1)',(s,3,2,manifest,'2025-01-01T12:05:00+00:00'));db.commit()
            self.assertIsNotNone(read_model_group(db,s,2));self.assertIsNone(read_group(db,s,2))
            self.assertTrue(source_has_conflict(db,s,key,1))
            a.sync_auto_topics()
            members=db.execute('SELECT seq,topic_id FROM dl_auto_topic_members WHERE scope=? AND model_key=?',(s,key)).fetchall()
            topics={r['seq']:r['topic_id'] for r in members}
            self.assertIn(2,topics);self.assertIn(3,topics)
            self.assertNotEqual(topics[2],topics[3])
            self.assertFalse(any(source_has_conflict(db,s,key,seq) for seq in topics))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],4)
            a.close()

    def test_large_multi_event_merge_checks_opposition_and_reverts(self):
        import json
        from datetime import datetime,timedelta,timezone
        from astrbot_plugin_digital_memory_store.auto_topology import merge_model_topics,recover_model_merges
        from astrbot_plugin_digital_memory_store.auto_topics import reconcile_topics
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');key=a._index().model_key;db=a.flow.db
            start=datetime(2025,1,1,tzinfo=timezone.utc)
            for i in range(1,105):
                a.capture(s,str(i),f'第{i}条原始记录',recorded_at=(start+timedelta(seconds=i)).isoformat())
            for topic in ('A','B'):db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,topic,key,start.isoformat()))
            rows=db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? ORDER BY seq',(s,)).fetchall()
            for r in rows:
                topic='A' if r['seq']%2 else 'B'
                db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',
                           (s,r['seq'],key,topic,r['content_sha'],r['recorded_at'],'原推测'))
            pair=json.dumps([{'seq':r['seq'],'sha256':r['content_sha'],'recorded_at':r['recorded_at']} for r in rows[:2]])
            for left,right in ((1,2),(2,1)):
                db.execute('INSERT INTO dl_auto_model_pairs VALUES(?,?,?,?,?)',(s,left,right,pair,start.isoformat()))
            opposition=json.dumps([{'seq':r['seq'],'sha256':r['content_sha'],'recorded_at':r['recorded_at'],'mode':'rebuild_split'} for r in rows[2:4]])
            db.execute('INSERT INTO dl_auto_separations VALUES(?,?,?,?,?,1)',(s,4,3,opposition,start.isoformat()));db.commit()
            self.assertFalse(merge_model_topics(db,key,s,1))
            db.execute('DELETE FROM dl_auto_separations WHERE scope=?',(s,));db.commit()
            # 如果模型线索先于归组到达，后台也必须在成员就绪后重试。
            db.execute('DELETE FROM dl_auto_topic_members WHERE scope=? AND seq=2',(s,));db.commit()
            self.assertEqual(reconcile_topics(db,key),0)
            row=rows[1]
            db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',
                       (s,2,key,'B',row['content_sha'],row['recorded_at'],'原推测'));db.commit()
            a.close();a=Store(d,'fixture-only',FakeEncoder);db=a.flow.db
            # 初次因成员缺失或反向线索阻止，重启后由后台扫描自动重试。
            self.assertEqual(reconcile_topics(db,key),1)
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT topic_id) FROM dl_auto_topic_members WHERE scope=?',(s,)).fetchone()[0],1)
            db.execute('DELETE FROM dl_auto_model_pairs WHERE scope=?',(s,));db.commit()
            recover_model_merges(db,key)
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT topic_id) FROM dl_auto_topic_members WHERE scope=?',(s,)).fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],104)
            a.close()

    def test_model_supported_multi_event_merge_reverses_on_source_change(self):
        import json
        from astrbot_plugin_digital_memory_store.auto_topology import merge_model_topics,recover_model_merges,source_has_conflict
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',FakeEncoder);s=a.scope('1','s');key=a._index().model_key
            texts=['厨房用了新的厨具','早饭做了三明治','午饭准备意大利面','朋友带了甜点']
            for i,text in enumerate(texts,1):a.capture(s,str(i),text,recorded_at=f'2025-01-01T12:0{i}:00+00:00')
            db=a.flow.db
            for tid in ('A','B'):db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,tid,key,'2025-01-01T00:00:00+00:00'))
            sources=db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? ORDER BY seq',(s,)).fetchall()
            for row in sources:
                db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',
                           (s,row['seq'],key,'A' if row['seq']<=2 else 'B',row['content_sha'],row['recorded_at'],'原推测'))
            manifest=json.dumps([{'seq':x['seq'],'sha256':x['content_sha'],'recorded_at':x['recorded_at']} for x in sources[1:3]],separators=(',',':'))
            for left,right in ((2,3),(3,2)):db.execute('INSERT INTO dl_auto_model_pairs VALUES(?,?,?,?,?)',(s,left,right,manifest,'2025-01-01T12:04:00+00:00'))
            db.commit();self.assertTrue(merge_model_topics(db,key,s,2))
            self.assertEqual({r['topic_id'] for r in db.execute('SELECT topic_id FROM dl_auto_topic_members WHERE scope=?',(s,))},{'A'})
            # 后续明确的反向判断先隔离事件，再自动撤销推测合并。
            db.execute('INSERT INTO dl_auto_separations VALUES(?,?,?,?,?,1)',(s,3,2,manifest,'2025-01-01T12:04:00+00:00'));db.commit()
            self.assertTrue(source_has_conflict(db,s,key,1))
            recover_model_merges(db,key)
            self.assertEqual(db.execute('SELECT topic_id FROM dl_auto_topic_members WHERE scope=? AND seq=3',(s,)).fetchone()[0],'B')
            self.assertFalse(merge_model_topics(db,key,s,2))
            db.execute('DELETE FROM dl_auto_separations WHERE scope=?',(s,));db.commit()
            self.assertTrue(merge_model_topics(db,key,s,2))
            # 合并后新加入的来源也必须追踪其依赖。
            from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
            a.automatic_memory_enabled=True
            a.capture(s,'5','新的活动安排',recorded_at='2025-01-01T12:05:00+00:00')
            process_jobs(db);a.sync_auto_source_vectors();a.sync_auto_topics()
            self.assertTrue(db.execute('SELECT 1 FROM dl_auto_move_dependents WHERE scope=? AND seq=5',(s,)).fetchone())
            # 另一后台更改了成员归属，不能直接回退覆盖新状态。
            db.execute("UPDATE dl_auto_topic_members SET topic_id='C' WHERE scope=? AND seq=4",(s,))
            db.execute("UPDATE dl_capture SET recorded_at='2025-01-01T12:10:00+00:00' WHERE seq=3");db.commit()
            recover_model_merges(db,key)
            self.assertEqual(db.execute('SELECT state FROM dl_auto_topic_moves ORDER BY rowid DESC LIMIT 1').fetchone()[0],'conflict')
            self.assertTrue(source_has_conflict(db,s,key,1));self.assertTrue(source_has_conflict(db,s,key,4))
            # 冲突消失后后台可以自行完成回退。
            other=a.scope('2','s')
            changed=db.execute('SELECT content_sha,recorded_at FROM dl_capture WHERE scope=? AND seq=3',(s,)).fetchone()
            db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',
                       (s,3,key,'A',changed['content_sha'],changed['recorded_at'],'重定位中'))
            db.execute("UPDATE dl_auto_topic_members SET basis='新依赖',recorded_at='2025-01-01T12:06:00+00:00' WHERE scope=? AND seq=5",(s,))
            db.execute('INSERT OR REPLACE INTO dl_auto_topic_skips VALUES(?,?,?)',(s,99,key))
            db.execute('INSERT OR REPLACE INTO dl_auto_topic_skips VALUES(?,?,?)',(other,99,key))
            db.execute("UPDATE dl_auto_topic_members SET topic_id='A' WHERE scope=? AND seq=4",(s,));db.commit()
            recover_model_merges(db,key)
            row=db.execute('SELECT topic_id FROM dl_auto_topic_members WHERE scope=? AND seq=4',(s,)).fetchone()
            self.assertEqual(row['topic_id'],'B')
            self.assertEqual(db.execute('SELECT state FROM dl_auto_topic_moves ORDER BY rowid DESC LIMIT 1').fetchone()[0],'reverted')
            self.assertFalse(db.execute('SELECT 1 FROM dl_auto_topic_members WHERE scope=? AND seq=3',(s,)).fetchone())
            self.assertFalse(db.execute('SELECT 1 FROM dl_auto_topic_members WHERE scope=? AND seq=5',(s,)).fetchone())
            self.assertFalse(db.execute('SELECT 1 FROM dl_auto_topic_skips WHERE scope=? AND seq=99',(s,)).fetchone())
            self.assertTrue(db.execute('SELECT 1 FROM dl_auto_topic_skips WHERE scope=? AND seq=99',(other,)).fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],5);a.close()

    def test_model_update_can_compare_older_source_in_same_provisional_topic(self):
        import json
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish,read_relation_hint,requeue_changed_hints
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s')
            a.automatic_memory_enabled=True;a.auto_model_enabled=True
            records=[('1','书店计划原来周六出发','2025-01-01T12:00:00+00:00'),
                     ('2','今天在家做饭','2025-01-02T12:00:00+00:00'),
                     ('3','书店计划改到周日','2025-01-03T12:00:00+00:00')]
            for mid,text,stamp in records:a.capture(s,mid,text,recorded_at=stamp)
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            a.flow.db.execute("UPDATE dl_auto_model_jobs SET status='done' WHERE seq IN (1,2)");a.flow.db.commit()
            snap=prepare(a.flow.db,model_key=a._index().model_key)
            self.assertEqual([r['seq'] for r in snap['sources']],[1,3]);self.assertEqual(snap['sources'][0]['mode'],'topic_update')
            response='{"verdict":"related","quote_a":"周六","quote_b":"周日","relation":"update"}'
            self.assertTrue(finish(a.flow.db,snap,response));self.assertEqual(read_relation_hint(a.flow.db,s,1,3),'可能更新')
            packet=json.loads(a._auto_topic_context(s,'购买小说','2025-02-01T00:00:00+00:00',1000,'q',set()))
            self.assertIn('未确认',packet['可能的更新关系'][0]['关系'])
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM relations').fetchone()[0],0)
            a.flow.db.execute("UPDATE dl_auto_topic_members SET topic_id='other' WHERE scope=? AND seq=3",(s,));a.flow.db.commit()
            self.assertIsNone(read_relation_hint(a.flow.db,s,1,3))
            self.assertEqual(requeue_changed_hints(a.flow.db,a._index().model_key),1)
            self.assertEqual(a.flow.db.execute('SELECT status FROM dl_auto_model_jobs WHERE scope=? AND seq=3',(s,)).fetchone()[0],'pending')
            a.close()

    def test_source_change_reschedules_automatic_organization(self):
        import hashlib
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s');a.automatic_memory_enabled=True
            a.capture(s,'1','原计划周六去书店',recorded_at='2025-01-01T12:00:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            changed='书店计划后来买小说';digest=hashlib.sha256(changed.encode()).hexdigest()
            a.flow.db.execute('UPDATE dl_capture SET content=?,content_sha=? WHERE scope=? AND seq=1',(changed,digest,s));a.flow.db.commit()
            self.assertEqual(a.flow.db.execute('SELECT status FROM dl_auto_jobs WHERE scope=? AND seq=1',(s,)).fetchone()[0],'pending')
            self.assertFalse(a.flow.db.execute('SELECT 1 FROM dl_auto_topic_members WHERE scope=? AND seq=1',(s,)).fetchone())
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            member=a.flow.db.execute('SELECT content_sha FROM dl_auto_topic_members WHERE scope=? AND seq=1',(s,)).fetchone()
            self.assertEqual(member['content_sha'],digest);a.close()

    def test_background_model_can_split_two_source_provisional_topic(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish,separated
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s')
            a.automatic_memory_enabled=True;a.auto_model_enabled=True
            a.capture(s,'1','原计划周六去书店',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','书店计划后来买小说',recorded_at='2025-01-01T12:01:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            before=a.flow.db.execute('SELECT topic_id FROM dl_auto_topic_members WHERE scope=? ORDER BY seq',(s,)).fetchall()
            self.assertEqual(before[0]['topic_id'],before[1]['topic_id'])
            snapshot=prepare(a.flow.db)
            self.assertTrue(finish(a.flow.db,snapshot,'{"verdict":"unrelated","quote_a":"周六","quote_b":"小说"}'))
            after=a.flow.db.execute('SELECT topic_id,basis FROM dl_auto_topic_members WHERE scope=? ORDER BY seq',(s,)).fetchall()
            self.assertNotEqual(after[0]['topic_id'],after[1]['topic_id'])
            self.assertEqual(after[1]['basis'],'模型推测需分开');self.assertTrue(separated(a.flow.db,s,1,2))
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],2)
            a.flow.db.execute("UPDATE dl_capture SET content='变更后原话' WHERE seq=2");a.flow.db.commit()
            self.assertFalse(separated(a.flow.db,s,1,2));a.close()

    def test_legacy_false_and_uncertain_never_split_topic(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish,separated
        for response in ('{"related":false,"quote_a":"周六","quote_b":"小说"}',
                         '{"verdict":"uncertain","quote_a":"","quote_b":""}'):
            with self.subTest(response=response),tempfile.TemporaryDirectory() as d:
                a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s')
                a.automatic_memory_enabled=True;a.auto_model_enabled=True
                a.capture(s,'1','原计划周六去书店',recorded_at='2025-01-01T12:00:00+00:00')
                a.capture(s,'2','书店计划后来买小说',recorded_at='2025-01-01T12:01:00+00:00')
                process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
                snapshot=prepare(a.flow.db);self.assertTrue(finish(a.flow.db,snapshot,response))
                self.assertFalse(separated(a.flow.db,s,1,2))
                topics=a.flow.db.execute('SELECT topic_id FROM dl_auto_topic_members WHERE scope=? ORDER BY seq',(s,)).fetchall()
                self.assertEqual(topics[0]['topic_id'],topics[1]['topic_id']);a.close()

    def test_legacy_ambiguous_split_is_invalidated_on_upgrade(self):
        import json
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import separated
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s');a.automatic_memory_enabled=True
            a.capture(s,'1','原计划周六去书店',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','书店计划后来买小说',recorded_at='2025-01-01T12:01:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            rows=a.flow.db.execute('SELECT seq,content_sha,recorded_at FROM dl_capture WHERE scope=? ORDER BY seq',(s,)).fetchall()
            manifest=json.dumps([{'seq':r['seq'],'sha256':r['content_sha'],'recorded_at':r['recorded_at']} for r in rows],ensure_ascii=False,separators=(',',':'))
            a.flow.db.execute('INSERT INTO dl_auto_separations VALUES(?,?,?,?,?,0)',(s,2,1,manifest,'2025-01-01T12:02:00+00:00'))
            a.flow.db.execute("UPDATE dl_auto_topic_members SET basis='模型推测需分开' WHERE scope=? AND seq=2",(s,))
            a.flow.db.execute("DELETE FROM dl_auto_schema_migrations WHERE migration='model_separation_tristate_v1'")
            # 模拟 v0.15.75 数据库：没有三态版本字段。
            a.flow.db.execute('DROP INDEX dl_auto_separations_older')
            a.flow.db.execute('ALTER TABLE dl_auto_separations DROP COLUMN verdict_version')
            a.flow.db.commit();a.close()
            b=Store(d,'fixture-only',SemanticFixtureEncoder);b.automatic_memory_enabled=True
            self.assertFalse(separated(b.flow.db,s,1,2))
            self.assertFalse(b.flow.db.execute('SELECT 1 FROM dl_auto_topic_members WHERE scope=? AND seq=2',(s,)).fetchone())
            b.sync_auto_topics();self.assertTrue(b.flow.db.execute('SELECT 1 FROM dl_auto_topic_members WHERE scope=? AND seq=2',(s,)).fetchone())
            self.assertEqual(b.flow.db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],2);b.close()

    def test_new_automatic_group_relocates_conflict_free_singletons(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_topics import reconcile_topics
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s');a.automatic_memory_enabled=True
            a.capture(s,'1','原计划周六去书店',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','改到周日吧',recorded_at='2025-01-01T12:01:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            key=a._index().model_key
            a.flow.db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(s,'separate',key,'2025-01-01T00:00:00+00:00'))
            a.flow.db.execute("UPDATE dl_auto_topic_members SET topic_id='separate' WHERE scope=? AND seq=2 AND model_key=?",(s,key));a.flow.db.commit()
            # 大量后续候选不能使旧的指定关联被遗漏。
            for seq in range(100,160):
                a.flow.db.execute('INSERT INTO dl_auto_groups VALUES(?,?,?,?,?,?)',(s,seq,seq+1,'x','y','2025-01-01T00:00:00+00:00'))
            a.flow.db.commit()
            self.assertEqual(reconcile_topics(a.flow.db,key),0)
            # 游标跨进程保留，下一批自动回看较早线索，无需人工指定。
            a.close();a=Store(d,'fixture-only',SemanticFixtureEncoder)
            self.assertEqual(reconcile_topics(a.flow.db,key),1)
            rows=a.flow.db.execute('SELECT topic_id,basis FROM dl_auto_topic_members WHERE scope=? ORDER BY seq',(s,)).fetchall()
            self.assertEqual(rows[0]['topic_id'],rows[1]['topic_id'])
            self.assertEqual(rows[1]['basis'],'系统线索重新定位')
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],2)
            a.close()

    def test_background_model_relation_is_only_a_source_bound_hypothesis(self):
        import json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs,read_group
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');a.auto_model_enabled=True
            a.capture(s,'1','周末去图书馆',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','我去挑几本小说',recorded_at='2025-01-01T12:01:00+00:00')
            process_jobs(a.flow.db);snap=prepare(a.flow.db)
            raw='{"related":true,"quote_a":"图书馆","quote_b":"几本小说","relation":"correction"}'
            self.assertTrue(finish(a.flow.db,snap,raw))
            self.assertEqual(read_group(a.flow.db,s,1)['relation_hint'],'可能纠正')
            a.auto_group_context_enabled=True
            data=json.loads(a._auto_group_context(s,'周末去图书馆怎么样','2025-02-01T00:00:00+00:00',800,'q',set()))
            self.assertIn('未确认',data['关系线索'])
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM relations').fetchone()[0],0)
            a.flow.db.execute("UPDATE dl_capture SET content='不同原话' WHERE seq=2");a.flow.db.commit()
            self.assertIsNone(read_group(a.flow.db,s,1));a.close()

    def test_automatic_topic_uses_weak_recency_and_preference_signals(self):
        import json
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s');a.automatic_memory_enabled=True
            a.capture(s,'1','我很喜欢书店计划看书',recorded_at='2025-01-01T00:00:00+00:00')
            a.capture(s,'2','我特别喜欢书店计划买书',recorded_at='2025-01-03T00:00:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            data=json.loads(a._auto_topic_context(s,'购买小说','2025-01-05T00:00:00+00:00',1000,'q',set()))
            self.assertIn('不能证明偏好',data['排序提示']);self.assertIn('未确认',data['说明'])
            self.assertEqual(len(data['可能相关原话']),2);a.close()

    def test_long_automatic_topic_uses_extracts_without_deleting_history(self):
        import json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s');a.automatic_memory_enabled=True
            for i in range(6):a.capture(s,str(i),f'书店计划讨论内容{i}',recorded_at=f'2025-01-0{i+1}T12:00:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            data=json.loads(a._auto_topic_context(s,'购买小说','2025-02-01T00:00:00+00:00',1000,'q',set()))
            self.assertIn('共 6 条',data['覆盖说明'])
            self.assertEqual(len(data['可能相关原话']),3)
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],6)
            a.capture(s,'change','改到周日',recorded_at='2025-01-06T12:01:00+00:00');process_jobs(a.flow.db);a.sync_auto_source_vectors();a.sync_auto_topics()
            self.assertFalse(a._auto_topic_context(s,'购买小说','2025-02-01T00:00:00+00:00',1000,'q',set()))
            a.close()

    def test_automatic_topics_link_cross_day_and_supply_sources_without_user_review(self):
        import json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.automatic_memory_enabled=True;a.pending_context_enabled=True;a.auto_group_context_enabled=True;a.auto_group_semantic_enabled=True
            a.capture(s,'1','原计划周六去书店',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','书店计划后来买小说',recorded_at='2025-01-03T12:00:00+00:00')
            a.capture(other,'3','书店计划属于其他会话',recorded_at='2025-01-03T12:00:00+00:00')
            process_jobs(a.flow.db);a.sync_auto_source_vectors(limit=20);a.sync_auto_topics()
            rows=a.flow.db.execute('SELECT topic_id,basis FROM dl_auto_topic_members WHERE scope=? ORDER BY seq',(s,)).fetchall()
            self.assertEqual(len(rows),2);self.assertEqual(rows[0]['topic_id'],rows[1]['topic_id'])
            self.assertEqual(rows[1]['basis'],'本地语义相似')
            packet=json.loads(a.context_packet(s,'购买小说',budget=800,current_message_id='q',as_of=datetime(2025,2,1,tzinfo=timezone.utc)))
            self.assertEqual(packet['线索方式'],'系统推测事件')
            self.assertEqual([x['原话'] for x in packet['可能相关原话']],['原计划周六去书店','书店计划后来买小说'])
            self.assertIn('未确认',packet['说明']);self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
            a.flow.db.execute("UPDATE dl_capture SET content='其他内容' WHERE seq=2");a.flow.db.commit()
            self.assertFalse(a.flow.db.execute('SELECT 1 FROM dl_auto_topic_members WHERE scope=? AND seq=2',(s,)).fetchone())
            a.close()

    def test_automatic_semantic_recall_reaches_older_sources_after_indexing(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s')
            a.automatic_memory_enabled=True;a.pending_context_enabled=True
            a.capture(s,'old','原计划周六去书店',recorded_at='2025-01-01T00:00:00+00:00')
            for i in range(70):a.capture(s,'x'+str(i),f'其他记录{i}',recorded_at='2025-01-02T00:00:00+00:00')
            a.flow.db.execute("UPDATE dl_auto_jobs SET status='done'");a.flow.db.commit()
            args=(s,'购买小说','2025-02-01T00:00:00+00:00',800,'q',set())
            self.assertFalse(a._semantic_unlinked_context(*args))
            while a.sync_auto_source_vectors(s,20):pass
            data=json.loads(a._semantic_unlinked_context(*args))
            self.assertEqual(data['未整理历史原话'],'原计划周六去书店')
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM dl_auto_source_vectors WHERE scope=?',(s,)).fetchone()[0],71)
            # 高分竞争者位于历史末尾时仍必须发现，不能只扫描前面的候选。
            a.capture(s,'tie','书店计划购买绘本',recorded_at='2025-01-03T00:00:00+00:00')
            a.flow.db.execute("UPDATE dl_auto_jobs SET status='done' WHERE scope=? AND seq=72",(s,));a.flow.db.commit()
            a.sync_auto_source_vectors(s,20)
            self.assertFalse(a._semantic_unlinked_context(*args))
            excluded=(s,'购买小说','2025-02-01T00:00:00+00:00',800,'q',{'pending:72'})
            self.assertEqual(json.loads(a._semantic_unlinked_context(*excluded))['未整理历史原话'],'原计划周六去书店')
            a.flow.db.execute('DELETE FROM dl_capture WHERE scope=? AND seq=72',(s,));a.flow.db.commit()
            a.close()
            b=Store(d,'fixture-only',SemanticFixtureEncoder);b.automatic_memory_enabled=True
            self.assertEqual(json.loads(b._semantic_unlinked_context(*args))['未整理历史原话'],'原计划周六去书店')
            b.close()

    def test_automatic_single_semantic_recall_and_update_boundary(self):
        import json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s')
            a.automatic_memory_enabled=True;a.pending_context_enabled=True;a.auto_group_context_enabled=True;a.auto_group_semantic_enabled=True
            a.capture(s,'1','原计划周六去书店',recorded_at='2025-01-01T12:00:00+00:00');process_jobs(a.flow.db)
            kwargs={'current_message_id':'q','as_of':datetime(2025,2,1,tzinfo=timezone.utc),'budget':800}
            packet=json.loads(a.context_packet(s,'购买小说',**kwargs))
            self.assertEqual(packet['未整理历史原话'],'原计划周六去书店')
            self.assertEqual(packet['定位方式'],'本地语义相似');self.assertIn('未确认',packet['说明'])
            a.capture(s,'2','改到周日吧',recorded_at='2025-01-01T12:01:00+00:00');process_jobs(a.flow.db)
            self.assertFalse(a._semantic_unlinked_context(s,'购买小说','2025-02-01T00:00:00+00:00',800,'q',set()))
            a.close()

    def test_automatic_single_after_gap_excludes_older_source(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');a.pending_context_enabled=True
            a.capture(s,'1','我之前喜欢吃苹果',recorded_at='2025-01-01T00:00:00+00:00')
            a.capture(s,'2','现在喜欢喝橙汁',recorded_at='2025-01-03T00:00:00+00:00');process_jobs(a.flow.db)
            args=(s,'你喜欢吃苹果吗','2025-02-01T00:00:00+00:00',800,'q',set())
            self.assertFalse(a._pending_context(*args,after_gap='2025-01-02T00:00:00+00:00'))
            packet=a._pending_context(s,'喜欢喝橙汁吗','2025-02-01T00:00:00+00:00',800,'q',set(),after_gap='2025-01-02T00:00:00+00:00')
            self.assertIn('现在喜欢喝橙汁',packet);self.assertIn('历史可能不完整',packet);a.close()

    def test_semantic_recall_for_automatic_groups_fails_closed_on_ties(self):
        import json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-only',SemanticFixtureEncoder);s=a.scope('1','s');other=a.scope('2','s')
            sources=['原计划周六去书店','改到周日','计划去博物馆看展','改到周三']
            for i,text in enumerate(sources,1):
                a.capture(s,str(i),text,recorded_at=f'2025-01-01T12:0{i}:00+00:00')
            process_jobs(a.flow.db);a.auto_group_context_enabled=True;a.auto_group_semantic_enabled=True
            cutoff=datetime(2025,2,1,tzinfo=timezone.utc).isoformat()
            packet=a._auto_group_context(s,'购买小说',cutoff,800,'q',set())
            data=json.loads(packet)
            self.assertEqual(data['定位方式'],'本地语义相似')
            self.assertEqual([x['原话'] for x in data['可能相关原话']],sources[:2])
            self.assertIn('未确认',data['说明'])
            self.assertFalse(a._auto_group_context(other,'购买小说',cutoff,800,'q',set()))
            self.assertFalse(a._auto_group_context(s,'购买小说','2025-01-01T12:02:00+00:00',800,'q',set()))
            a.index.embedder.ambiguous=True
            self.assertFalse(a._auto_group_context(s,'购买小说',cutoff,800,'q',set()))
            a.index.embedder.encode_documents=lambda texts: (_ for _ in ()).throw(RuntimeError('encoder failure'))
            self.assertFalse(a._auto_group_context(s,'购买小说',cutoff,800,'q',set()))
            self.assertEqual(len(a.pending(s)),4)
            a.close()

    def test_model_auto_pair_is_provisional_scoped_and_source_bound(self):
        import json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs,read_group
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s');a.auto_model_enabled=True
            a.capture(s,'1','周末去图书馆',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','我去挑几本小说',recorded_at='2025-01-01T12:01:00+00:00')
            process_jobs(a.flow.db);snapshot=prepare(a.flow.db)
            self.assertEqual(snapshot['seq'],2)
            with self.assertRaises(ValueError):finish(a.flow.db,snapshot,'{"related":true,"quote_a":"没说过","quote_b":"几本"}')
            self.assertFalse(a.flow.db.execute('SELECT 1 FROM dl_auto_model_pairs').fetchone())
            with a.flow.db:a.flow.db.executemany('INSERT INTO dl_auto_topic_skips VALUES(?,?,?)',[(s,1,'model'),(s,2,'model'),(other,1,'model')])
            self.assertTrue(finish(a.flow.db,snapshot,'{"related":true,"quote_a":"图书馆","quote_b":"几本小说"}'))
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM dl_auto_topic_skips WHERE scope=?',(s,)).fetchone()[0],0)
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM dl_auto_topic_skips WHERE scope=?',(other,)).fetchone()[0],1)
            self.assertEqual(read_group(a.flow.db,s,1)['basis'],'模型提出的可能关联')
            self.assertIsNone(read_group(a.flow.db,other,1));a.auto_group_context_enabled=True
            result=json.loads(a.context_packet(s,'周末去图书馆的安排',budget=800,current_message_id='q',as_of=datetime(2025,2,1,tzinfo=timezone.utc)))
            self.assertEqual(result['线索方式'],'模型提出的可能关联');self.assertIn('未确认',result['说明'])
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
            a.flow.db.execute("UPDATE dl_capture SET content='更改内容' WHERE seq=2");a.flow.db.commit()
            self.assertIsNone(read_group(a.flow.db,s,1));a.close()

    def test_model_auto_throttles_and_does_not_use_changed_snapshot(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.auto_model import prepare,finish
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');a.auto_model_enabled=True
            a.capture(s,'1','周末去图书馆',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','我去挑几本小说',recorded_at='2025-01-01T12:01:00+00:00')
            process_jobs(a.flow.db);self.assertIsNone(prepare(a.flow.db,'other-policy'))
            snapshot=prepare(a.flow.db);self.assertIsNone(prepare(a.flow.db))
            a.flow.db.execute("UPDATE dl_capture SET recorded_at='2025-01-01T12:02:00+00:00' WHERE seq=2");a.flow.db.commit()
            self.assertFalse(finish(a.flow.db,snapshot,'{"related":true,"quote_a":"图书馆","quote_b":"几本小说"}'))
            job=a.flow.db.execute("SELECT status,attempts FROM dl_auto_model_jobs WHERE seq=2").fetchone()
            self.assertEqual((job['status'],job['attempts']),('pending',1))
            self.assertIsNone(prepare(a.flow.db))  # 重试仍受全局调用间隔限制。
            self.assertFalse(a.flow.db.execute('SELECT 1 FROM dl_auto_model_pairs').fetchone());a.close()

    def test_short_followup_is_provisional_and_can_supply_both_sources(self):
        import json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs,read_group
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            a.capture(s,'1','周六想去书店',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','改到周日吧',recorded_at='2025-01-01T12:01:00+00:00')
            process_jobs(a.flow.db);a.auto_group_context_enabled=True
            self.assertEqual(read_group(a.flow.db,s,1)['basis'],'短句可能更新')
            packet=a.context_packet(s,'周六想去书店的安排是什么',budget=800,current_message_id='q',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            data=json.loads(packet)
            self.assertEqual([r['原话'] for r in data['可能相关原话']],['周六想去书店','改到周日吧'])
            self.assertEqual(data['线索方式'],'短句可能更新');self.assertIn('未确认',data['说明'])
            a.flow.db.execute("UPDATE dl_capture SET recorded_at='2025-01-01T12:30:00+00:00' WHERE seq=2");a.flow.db.commit()
            self.assertIsNone(read_group(a.flow.db,s,1));a.close()

    def test_followup_chain_is_complete_and_cross_gap_is_rejected(self):
        import json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs,read_group
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.capture(s,'1','周六想去书店',recorded_at='2025-01-01T12:00:00+00:00')
            a.capture(s,'2','改到周日吧',recorded_at='2025-01-01T12:01:00+00:00')
            a.capture(s,'3','改到周一',recorded_at='2025-01-01T12:02:00+00:00')
            a.capture(other,'4','周六想去书店',recorded_at='2025-01-01T12:00:00+00:00')
            a.record_capture_gap(other,'2025-01-01T12:00:30+00:00')
            a.capture(other,'5','改到周日吧',recorded_at='2025-01-01T12:01:00+00:00')
            process_jobs(a.flow.db)
            self.assertEqual(read_group(a.flow.db,s,1)['members'],[1,2,3])
            a.auto_group_context_enabled=True
            packet=a.context_packet(s,'周六想去书店的安排是什么',current_message_id='q',budget=800,as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            data=json.loads(packet)
            self.assertEqual([x['原话'] for x in data['可能相关原话']],['周六想去书店','改到周日吧','改到周一'])
            self.assertIn('未确认',data['说明'])
            self.assertIsNone(read_group(a.flow.db,other,4));a.close()

    def test_followup_chain_does_not_truncate_and_invalidates_changed_source(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs,read_group
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            for i,text in enumerate(['周六想去书店','改到周日','改到周一','改到周二','改到周三'],1):
                a.capture(s,str(i),text,recorded_at=f'2025-01-01T12:0{i}:00+00:00')
            process_jobs(a.flow.db)
            self.assertEqual(read_group(a.flow.db,s,1)['members'],[1,2,3,4,5])
            a.flow.db.execute("UPDATE dl_capture SET content='改到周五' WHERE seq=3");a.flow.db.commit()
            self.assertIsNone(read_group(a.flow.db,s,1))
            a.flow.db.execute("UPDATE dl_capture SET content='改到周一' WHERE seq=3");a.flow.db.commit()
            a.capture(s,'6','改到周四',recorded_at='2025-01-01T12:06:00+00:00')
            process_jobs(a.flow.db)
            self.assertIsNone(read_group(a.flow.db,s,1));a.close()

    def test_gap_boundary_rejects_group_that_crosses_missing_interval(self):
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            a.capture(s,'1','计划去青岛海边散步',recorded_at='2025-01-01T00:00:00+00:00')
            a.capture(s,'2','青岛海边散步之后去吃饭',recorded_at='2025-01-03T00:00:00+00:00')
            process_jobs(a.flow.db);a.auto_group_context_enabled=True
            cutoff=datetime(2025,2,1,tzinfo=timezone.utc).isoformat()
            packet=a._auto_group_context(s,'青岛海边散步怎么样',cutoff,800,'q',set(),after_gap='2025-01-02T00:00:00+00:00')
            self.assertFalse(packet)
            a.record_capture_gap(s,'2025-01-02T00:00:00+00:00');a.acknowledge_capture_gap(s)
            self.assertEqual(a.capture_gap_boundary(s),'2025-01-02T00:00:00+00:00');a.close()

    def test_auto_group_context_preserves_sources_and_respects_boundaries(self):
        import json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.capture(s,'1','计划去青岛海边散步',recorded_at='2025-01-01T00:00:00+00:00')
            a.capture(s,'2','青岛海边散步之后去吃饭',recorded_at='2025-01-02T00:00:00+00:00')
            process_jobs(a.flow.db);a.auto_group_context_enabled=True
            asof=datetime(2025,2,1,tzinfo=timezone.utc)
            kwargs={'current_message_id':'query','as_of':asof,'budget':800}
            data=json.loads(a.context_packet(s,'青岛海边散步怎么样',**kwargs))
            self.assertEqual([x['原话'] for x in data['可能相关原话']],['计划去青岛海边散步','青岛海边散步之后去吃饭'])
            self.assertIn('未确认',data['说明'])
            self.assertFalse(a.context_packet(other,'青岛海边散步怎么样',**kwargs))
            self.assertFalse(a.context_packet(s,'青岛海边散步怎么样',current_message_id='2',as_of=asof,budget=800))
            self.assertFalse(a.context_packet(s,'青岛海边散步怎么样',current_message_id='query',as_of=datetime(2025,1,2,tzinfo=timezone.utc),budget=800))
            self.assertFalse(a.context_packet(s,'青岛海边散步怎么样',exclude_ids=['pending:1'],**kwargs))
            self.assertFalse(a.context_packet(s,'青岛海边散步怎么样',current_message_id='query',as_of=asof,budget=100))
            a.flow.db.execute("UPDATE dl_capture SET content='被篡改' WHERE seq=2");a.flow.db.commit()
            self.assertFalse(a.context_packet(s,'青岛海边散步怎么样',**kwargs));a.close()

    def test_auto_jobs_do_not_overtake_failed_same_scope(self):
        from unittest.mock import patch
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs,organize_recent
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.capture(s,'1','青岛海边散步');a.capture(s,'2','青岛海边散步吃饭')
            a.capture(other,'3','朋友喜欢吃面')
            def fail_first(db,scope,**kwargs):
                if scope==s:raise RuntimeError('fail')
                return organize_recent(db,scope,**kwargs)
            with patch('astrbot_plugin_digital_memory_store.auto_organize.organize_recent',side_effect=fail_first):
                process_jobs(a.flow.db)
            rows=[tuple(r) for r in a.flow.db.execute('SELECT seq,status FROM dl_auto_jobs ORDER BY seq')]
            self.assertEqual(rows,[(1,'pending'),(2,'pending'),(3,'done')]);a.close()

    def test_auto_jobs_restart_retry_and_source_bound_group(self):
        from unittest.mock import patch
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs,read_group
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s')
            a.capture(scope,'1','计划去青岛海边散步')
            a.capture(scope,'2','青岛海边散步之后去吃饭')
            with patch('astrbot_plugin_digital_memory_store.auto_organize.organize_recent',side_effect=RuntimeError('failed')):
                process_jobs(a.flow.db)
            self.assertEqual(a.flow.db.execute("SELECT COUNT(*) FROM dl_auto_jobs WHERE status='pending' AND attempts=1").fetchone()[0],1)
            a.close()
            b=Store(d,'',FakeEncoder)
            b.flow.db.execute("UPDATE dl_auto_jobs SET next_at=''");b.flow.db.commit()
            process_jobs(b.flow.db)
            self.assertEqual(read_group(b.flow.db,scope,1)['members'],[1,2])
            self.assertEqual(b.flow.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
            b.flow.db.execute("UPDATE dl_capture SET content='篡改' WHERE seq=2");b.flow.db.commit()
            self.assertIsNone(read_group(b.flow.db,scope,1));b.close()

    def test_automatic_candidates_are_provisional_and_relocated_by_later_source(self):
        from astrbot_plugin_digital_memory_store.auto_organize import organize_recent,read_localization
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.capture(s,'1','计划去青岛海边散步')
            organize_recent(a.flow.db,s)
            self.assertEqual(read_localization(a.flow.db,s,1)['state'],'unresolved')
            a.capture(other,'2','计划去青岛海边散步')
            a.capture(s,'3','青岛海边散步之后去吃饭')
            organize_recent(a.flow.db,s)
            result=read_localization(a.flow.db,s,1)
            self.assertEqual(result['state'],'candidate')
            self.assertEqual([x['seq'] for x in result['candidates']],[3])
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
            self.assertEqual(len(a.pending(s)),2)
            a.flow.db.execute("UPDATE dl_capture SET content='篡改' WHERE seq=3");a.flow.db.commit()
            self.assertIsNone(read_localization(a.flow.db,s,1));a.close()

    def test_whitespace_sources_preserved_through_link_relation_and_backup(self):
        from astrbot_plugin_digital_memory_store.recovery import verify_backup,restore_to_new_directory
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); a=Store(root/'db','',FakeEncoder); scope=a.scope('1','s')
            original='  原计划去书店\n'; updated='\n改到周日去书店  '
            a.capture(scope,'1',original,recorded_at='2025-01-01T00:00:00+00:00')
            a.capture(scope,'2',updated,recorded_at='2025-01-02T00:00:00+00:00')
            old=a.link_capture(scope,1,'书店')
            new=a.link_capture_relation(scope,2,'书店',old,'corrects')
            rows={r['id']:r['content'] for r in a.show(scope,'书店')}
            self.assertEqual(rows[old],original);self.assertEqual(rows[new],updated)
            backup=root/'db'/'backups'/a.backup()
            self.assertTrue(verify_backup(backup)['ok']);a.close()
            restore_to_new_directory(backup,root/'restored')
            b=Store(root/'restored','',FakeEncoder)
            self.assertEqual({r['id']:r['content'] for r in b.show(scope,'书店')},rows)
            b.close()

    def test_whitespace_sources_preserved_in_batch(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s')
            for i,text in enumerate(('  第一条','第二条\n'),1):a.capture(scope,str(i),text)
            self.assertEqual(a.link_capture_batch(scope,[1,2],'事件')['completed'],[1,2])
            self.assertEqual({r['content'] for r in a.show(scope,'事件')},{'  第一条','第二条\n'})
            a.close()

    def test_manual_save_rolls_back_event_and_source_when_review_fails(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');original=a.flow.review
            def fail(**kwargs):raise RuntimeError('review failed')
            a.flow.review=fail
            with self.assertRaises(RuntimeError):a.save(s,'书店','计划去书店')
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM e417_ingest').fetchone()[0],0)
            a.flow.review=original;a.save(s,'书店','计划去书店')
            self.assertEqual(len(a.show(s,'书店')),1);a.close()

    def test_atomic_link_recovers_legacy_pending_ingest(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            a.capture(s,'legacy','计划去书店',recorded_at='2025-01-01T00:00:00+00:00')
            row=a.flow.db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=1',(s,)).fetchone()
            original=a.flow.ingest(scope=s,origin='captured:'+row['event_hash'],speaker='user',
                kind='user_message',content=row['content'],recorded_at=row['recorded_at'])
            self.assertEqual(original['state'],'pending_review')
            mid=a.link_capture(s,1,'书店')
            self.assertEqual(mid,original['message_id'])
            self.assertEqual(a.link_capture(s,1,'书店'),mid)
            self.assertEqual(len(a.show(s,'书店')),1);a.close()

    def test_atomic_batch_preserves_previously_committed_partial_work(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            old=a.save(s,'书店','原计划周六去书店')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            for i,text in enumerate(('书店改到周日','书店改到周一'),1):
                a.capture(s,str(i),text,recorded_at='2025-02-01T00:00:00+00:00')
            ids=[x['draft_id'] for x in a.rule_update_drafts(s)['drafts']]
            a.link_capture_relation(s,1,'书店',old,'corrects')
            original=a.relate_sources
            def interrupted(scope,newer,older,kind):
                if newer!=a.flow.writer.status(s,'captured:'+a.flow.db.execute('SELECT event_hash FROM dl_capture WHERE seq=1').fetchone()[0])['message_id']:
                    raise RuntimeError('interrupted')
                return original(scope,newer,older,kind)
            a.relate_sources=interrupted
            self.assertEqual(a.confirm_contrast_batch(s,ids,'corrects'),{'completed':[],'failed':ids[1]})
            self.assertEqual([x['seq'] for x in a.pending(s)],[2])
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM relations').fetchone()[0],1)
            a.relate_sources=original
            self.assertEqual(a.confirm_contrast_batch(s,ids,'corrects'),{'completed':ids,'failed':None})
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM relations').fetchone()[0],2);a.close()

    def test_atomic_batch_blocks_external_writer_during_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            a.save(s,'书店','原计划周六去书店')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            for i,text in enumerate(('书店改到周日','书店改到周一'),1):
                a.capture(s,str(i),text,recorded_at='2025-02-01T00:00:00+00:00')
            ids=[x['draft_id'] for x in a.rule_update_drafts(s)['drafts']]
            original=a.relate_sources;blocked=[]
            def try_external(*args):
                try:
                    with _conn(a.path,timeout=0.01) as other:
                        other.execute("UPDATE events SET title='冲突修改'")
                except sqlite3.OperationalError as exc:
                    blocked.append(str(exc))
                return original(*args)
            a.relate_sources=try_external
            self.assertEqual(a.confirm_contrast_batch(s,ids,'corrects'),{'completed':ids,'failed':None})
            self.assertEqual(len(blocked),2)
            self.assertEqual(len(a.show(s,'书店')),3);a.close()

    def test_rule_update_drafts_batch_preflight_and_interrupted_retry(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.save(s,'书店','原计划周六去书店');a.save(s,'登山','原计划一月去登山')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            a.capture(s,'1','书店改到周日',recorded_at='2025-02-01T00:00:00+00:00')
            a.capture(s,'2','登山改到二月',recorded_at='2025-02-01T00:00:00+00:00')
            batch=a.rule_update_drafts(s);drafts=batch['drafts'];ids=[x['draft_id'] for x in drafts]
            self.assertEqual(len(ids),2);self.assertTrue(all(x['origin']=='rule' for x in drafts))
            self.assertEqual(drafts[0]['result']['new_quote'],'书店改到周日')
            self.assertEqual(len(a.pending(s)),2)
            with self.assertRaises(ValueError):a.confirm_contrast_batch(other,ids,'corrects')
            with self.assertRaises(ValueError):a.confirm_contrast_batch(s,[ids[0],ids[0]],'corrects')
            original=a.relate_sources;calls=[]
            def interrupted(*args):
                calls.append(args)
                if len(calls)==2:raise RuntimeError('interrupted')
                return original(*args)
            a.relate_sources=interrupted
            self.assertEqual(a.confirm_contrast_batch(s,ids,'corrects'),{'completed':[],'failed':ids[1]})
            self.assertEqual(len(a.pending(s)),2)
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM relations').fetchone()[0],0)
            a.relate_sources=original
            self.assertEqual(a.confirm_contrast_batch(s,ids,'corrects'),{'completed':ids,'failed':None})
            self.assertEqual(a.confirm_contrast_batch(s,ids,'corrects'),{'completed':ids,'failed':None})
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM relations').fetchone()[0],2)
            self.assertEqual(a.pending(s),[]);a.close()

    def test_update_batch_same_event_partial_retry_preserves_originals(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            old=a.save(s,'书店','计划周六去书店')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            for i,text in enumerate(('书店改到周日','书店改到周一'),1):
                a.capture(s,str(i),text,recorded_at='2025-02-01T00:00:00+00:00')
            ids=[x['draft_id'] for x in a.rule_update_drafts(s)['drafts']]
            self.assertEqual(len(ids),2)
            original=a.relate_sources;calls=[]
            def interrupted(*args):
                calls.append(args)
                if len(calls)==2:raise RuntimeError('interrupted')
                return original(*args)
            a.relate_sources=interrupted
            self.assertEqual(a.confirm_contrast_batch(s,ids,'corrects'),{'completed':[],'failed':ids[1]})
            self.assertEqual(len(a.pending(s)),2)
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM relations').fetchone()[0],0)
            a.relate_sources=original
            self.assertEqual(a.confirm_contrast_batch(s,ids,'corrects'),{'completed':ids,'failed':None})
            self.assertEqual(a.confirm_contrast_batch(s,ids,'corrects'),{'completed':ids,'failed':None})
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM relations WHERE scope=? AND older=?',(s,old)).fetchone()[0],2)
            self.assertEqual(len(a.show(s,'书店')),3)
            with self.assertRaises(ValueError):a.confirm_contrast_batch(s,ids,'related')
            a.close()

    def test_update_batch_stale_history_refuses_without_writes(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            old=a.save(s,'书店','计划周六去书店')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            a.capture(s,'1','书店改到周日',recorded_at='2025-02-01T00:00:00+00:00')
            ids=[x['draft_id'] for x in a.rule_update_drafts(s)['drafts']]
            same_source=a.rule_update_drafts(s)['drafts'][0]['draft_id']
            with self.assertRaises(ValueError):a.confirm_contrast_batch(s,[ids[0],same_source],'corrects')
            a.flow.db.execute('UPDATE messages SET content=? WHERE id=?',('不匹配的旧原话',old));a.flow.db.commit()
            with self.assertRaises(ValueError):a.confirm_contrast_batch(s,ids,'corrects')
            self.assertEqual(len(a.pending(s)),1);a.close()

    def test_update_batch_stops_when_other_history_changes_mid_operation(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            a.save(s,'书店','原计划周六去书店')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            for i,text in enumerate(('书店改到周日','书店改到周一'),1):
                a.capture(s,str(i),text,recorded_at='2025-02-01T00:00:00+00:00')
            ids=[x['draft_id'] for x in a.rule_update_drafts(s)['drafts']]
            original=a.relate_sources
            def add_unexpected_history(*args):
                result=original(*args);a.save(s,'书店','操作期间增加的其他记录');return result
            a.relate_sources=add_unexpected_history
            self.assertEqual(a.confirm_contrast_batch(s,ids,'corrects'),{'completed':[],'failed':ids[1]})
            self.assertEqual([x['seq'] for x in a.pending(s)],[1,2])
            self.assertEqual(len(a.show(s,'书店')),1);a.close()

    def test_rule_batch_draft_review_partial_and_stale_guard(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.save(s,'书店','之前去过书店');a.save(s,'登山','此前讨论过登山')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            for n,text in enumerate(('书店计划周六','书店准备购书','书店约了朋友','登山安排一月','登山准备装备','登山计划出发'),1):
                a.capture(s,str(n),text,recorded_at='2025-02-01T00:00:00+00:00')
            result=a.rule_group_draft(s);draft=result['draft'];did=draft['draft_id']
            self.assertEqual(draft['origin'],'rule');self.assertEqual(result['next_cursor'],6)
            self.assertEqual(len(draft['result']['groups']),2)
            self.assertEqual(len(a.pending(s)),6)
            with self.assertRaises(ValueError):a.read_group_draft(other,did)
            a.event_alias(s,'书店','逛书店')
            with self.assertRaises(ValueError):a.confirm_group_draft(s,did)
            a.event_alias(s,'书店','逛书店',remove=True)
            self.assertEqual(a.confirm_group_draft(s,did,[1,2,4])['completed'],[1,2,4])
            self.assertEqual([r['seq'] for r in a.pending(s)],[3,5,6])
            self.assertEqual(a.confirm_group_draft(s,did)['completed'],[1,2,3,4,5,6])
            self.assertEqual(a.pending(s),[])
            a.save(s,'书店','其他历史变化')
            with self.assertRaises(ValueError):a.confirm_group_draft(s,did)
            a.close()

    def test_old_semantic_index_is_not_reused_after_algorithm_upgrade(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-model',RareDetailEncoder);s=a.scope('1','s')
            a.save(s,'旧事件','稀有线索');e=a.flow._events(s)[0];snap=a.flow._snapshot(s,e['id'])
            a.flow.db.execute('''CREATE TABLE e419_vectors (
                scope TEXT NOT NULL,event_id TEXT NOT NULL,model_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,vector_json TEXT NOT NULL,dimensions INTEGER NOT NULL,
                indexed_at TEXT NOT NULL,PRIMARY KEY(scope,event_id,model_key))''')
            a.flow.db.execute('INSERT INTO e419_vectors VALUES(?,?,?,?,?,?,?)',
                (s,e['id'],'rare-detail-fixture-v1',snap['digest'],'[0,1]',2,'2025-01-01'))
            a.flow.db.commit();index=a._index()
            self.assertNotEqual(index.model_key,index.encoder_key)
            self.assertEqual(index.search(s,'虚构改写'),[e['id']])
            self.assertEqual(a.flow.db.execute('SELECT COUNT(*) FROM e419_vectors WHERE scope=?',(s,)).fetchone()[0],2)
            a.close()

    def test_segment_recall_preserves_rare_source_and_detects_incomplete_index(self):
        from astrbot_plugin_digital_memory_store.engine.e419.dynamic_index import IndexSafetyError
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-model',RareDetailEncoder);s=a.scope('1','s');other=a.scope('2','s')
            for n in range(12):a.save(s,'长事件','普通背景内容'+str(n))
            a.save(s,'长事件','稀有线索')
            index=a._index();index.synchronize(s)
            event=a.flow._events(s)[0]
            self.assertEqual(index.search(s,'虚构改写'),[event['id']])
            self.assertEqual(index.search(other,'虚构改写'),[])
            count=a.flow.db.execute('SELECT COUNT(*) FROM e419_segment_vectors WHERE scope=?',(s,)).fetchone()[0]
            self.assertEqual(count,13)
            a.flow.db.execute('DELETE FROM e419_segment_vectors WHERE scope=? AND message_id=(SELECT message_id FROM e419_segment_vectors WHERE scope=? LIMIT 1)',(s,s));a.flow.db.commit()
            with self.assertRaises(IndexSafetyError):index.search(s,'虚构改写')
            a.close()

    def test_semantic_source_selection_spans_batches_and_fails_closed(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-model',SemanticFixtureEncoder);s=a.scope('1','s')
            target=a.save(s,'书店计划','原计划周六去书店')
            for n in range(24):a.save(s,'书店计划','无关背景记录'+str(n))
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            a.semantic_source_context_enabled=True;q='之前买书的安排是什么'
            packet=json.loads(a.context_packet(s,q,budget=500))
            self.assertIn('未确认的联想事件',packet)
            self.assertTrue(any(x['内容']=='原计划周六去书店' for x in packet['关联历史']['原话']))
            original=a.embedder.encode_documents
            calls=[]
            def track(texts):
                calls.extend(texts);return original(texts)
            a.embedder.encode_documents=track
            self.assertIn('关联历史',a.context_packet(s,q,budget=500))
            self.assertEqual(calls,[])
            def ambiguous_in_other_batch(texts):
                vectors=original(texts)
                return [[1.,0.] if x=='无关背景记录23' else v for x,v in zip(texts,vectors)]
            a.semantic_source_vectors.clear()
            a.embedder.encode_documents=ambiguous_in_other_batch
            self.assertNotIn('关联历史',a.context_packet(s,q,budget=500))
            def fail_later_batch(texts):
                if any('无关背景记录23'==x for x in texts):raise ValueError('fixture failure')
                return original(texts)
            a.semantic_source_vectors.clear();a.embedder.encode_documents=fail_later_batch
            self.assertNotIn('关联历史',a.context_packet(s,q,budget=500))
            a.close()

    def test_long_semantic_event_uses_all_complete_sources_and_invalidates(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-model',LimitedLengthEncoder);s=a.scope('1','s')
            lines=['完整原话'+str(n)+'的内容' for n in range(12)]
            for line in lines:a.save(s,'记录合集',line)
            index=a._index();state=index.synchronize(s)
            self.assertEqual(state['segmented'],1);self.assertEqual(state['unavailable'],0)
            self.assertTrue(all(any(line in encoded and len(encoded)<=100 for encoded in a.embedder.received) for line in lines))
            event=a.flow._events(s)[0]
            self.assertEqual(index.search(s,'不同的问法'),[event['id']])
            # 任何一条原话自身过长时，不用剩余来源构造不完整事件向量。
            a.save(s,'记录合集','无法编码的长原话'+'字'*120)
            state=index.synchronize(s)
            self.assertEqual(state['unavailable'],1)
            self.assertEqual(index.search(s,'不同的问法'),[])
            self.assertIsNone(a.flow.db.execute('SELECT 1 FROM e419_vectors WHERE scope=? AND event_id=?',(s,event['id'])).fetchone())
            a.close()

    def test_semantic_partial_retry_progresses_past_twenty_events(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-model',PartiallyFailingEncoder);s=a.scope('1','s')
            a.save(s,'超长事件','不能编码')
            for n in range(23):a.save(s,'事件'+str(n),'普通记录'+str(n))
            index=a._index();a.embedder.fail=True
            first=index.synchronize(s)
            self.assertGreater(first['unavailable'],1)
            for _ in range(4):state=index.synchronize(s)
            self.assertEqual(state['unavailable'],1)
            self.assertEqual(state['indexed'],23);a.close()

    def test_semantic_index_isolates_failed_event_and_recovers(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-model',PartiallyFailingEncoder);s=a.scope('1','s')
            one=a.save(s,'书店计划','原计划周六去书店')
            two=a.save(s,'超长事件','一段不可编码的背景')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            a.semantic_source_context_enabled=True;q='之前买书的安排是什么'
            self.assertIn('关联历史',a.context_packet(s,q,budget=800))
            eid=next(e['id'] for e in a.flow._events(s) if e['title']=='超长事件')
            a.embedder.fail=True
            three=a.save(s,'超长事件','后续原话')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-02T00:00:00+00:00' WHERE id=?",(three,));a.flow.db.commit()
            partial=a.context_packet(s,q,budget=800)
            self.assertIn('候选不完整',partial);self.assertNotIn('关联历史',partial)
            self.assertEqual(a.index.last_sync['unavailable'],1)
            self.assertIsNone(a.flow.db.execute('SELECT 1 FROM e419_vectors WHERE scope=? AND event_id=?',(s,eid)).fetchone())
            a.embedder.fail=False
            self.assertIn('关联历史',a.context_packet(s,q,budget=800))
            self.assertEqual(a.index.last_sync['unavailable'],0);a.close()

    def test_semantic_source_context_is_opt_in_uncertain_and_scoped(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fixture-model',SemanticFixtureEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'书店计划','原计划周六去书店');new=a.save(s,'书店计划','改到周日去书店')
            for mid,stamp in ((old,'2025-01-01T00:00:00+00:00'),(new,'2025-01-02T00:00:00+00:00')):
                a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE id=?',(stamp,mid))
            a.flow.db.commit();a.relate_sources(s,new,old,'corrects')
            q='之前买书的安排是什么'
            self.assertIn('未确认的联想候选',a.context_packet(s,q,budget=800))
            a.semantic_source_context_enabled=True
            data=json.loads(a.context_packet(s,q,budget=800))
            self.assertEqual(data['匹配方式'],'本地语义相似')
            self.assertIn('尚未确认',data['说明'])
            self.assertEqual(len(data['关联历史']['原话']),2)
            self.assertEqual(data['关联历史']['关系'][0]['类型'],'修正')
            self.assertEqual(a.context_packet(other,q,budget=800),'')
            from datetime import datetime,timezone
            self.assertEqual(a.context_packet(s,q,budget=800,as_of=datetime(2025,1,2,tzinfo=timezone.utc)),'')
            self.assertNotIn('关联历史',a.context_packet(s,q,budget=150))
            self.assertNotIn('关联历史',a.context_packet(s,'不是之前买书的安排',budget=800))
            a.embedder.ambiguous=True;a.semantic_source_vectors.clear()
            self.assertNotIn('关联历史',a.context_packet(s,q,budget=800))
            a.close()

    def test_memory_inventory_is_scoped_and_counts_states(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');other=a.scope('2','s')
            mid=a.save(scope,'书店','计划去书店')
            a.capture(scope,'1','准备买书');a.capture(scope,'2','准备登山')
            a.link_capture(scope,1,'书店');a.save(other,'其他事件','不属于当前会话')
            a.save_source_brief(scope,'书店',mid,'去书店','计划去书店。')
            state=a.memory_inventory(scope)
            self.assertEqual(state,{'captured':2,'pending':1,'linked_captures':1,'imported':0,
                                    'reviewed_sources':2,'events':1,'brief_versions':1})
            self.assertEqual(a.memory_inventory(other)['events'],1)
            self.assertEqual(a.memory_inventory(other)['pending'],0)
            a.close()

    def test_multi_event_prepares_once_and_discards_external_changes(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s')
            a.save(scope,'书店计划','计划去书店');a.save(scope,'登山计划','计划去登山')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            a.multi_event_context_enabled=True;q='书店计划和登山计划怎么样';original=a._prepare_context_event;calls=[]
            def count(*args,**kwargs):
                calls.append(args[1]['id']);return original(*args,**kwargs)
            a._prepare_context_event=count
            packet=a.context_packet(scope,q,budget=800)
            self.assertEqual(len(calls),2);self.assertEqual(len(json.loads(packet)['多个事件']),2)
            a._prepare_context_event=original
            mutated=[False]
            def change_during_allocation(*args,**kwargs):
                plan=original(*args,**kwargs)
                def wrapped(*p,**k):
                    result=plan(*p,**k)
                    if not mutated[0]:
                        with _conn(a.path) as external:
                            external.execute("UPDATE messages SET recorded_at='2025-01-02T00:00:00+00:00' WHERE id=(SELECT id FROM messages LIMIT 1)")
                        mutated[0]=True
                    return result
                return wrapped if plan else None
            a._prepare_context_event=change_during_allocation
            self.assertEqual(a.context_packet(scope,q,budget=800),'')
            self.assertTrue(mutated[0]);a.close()

    def test_multi_event_reallocates_budget_for_complete_relation_groups(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s')
            old=a.save(scope,'书店计划','原计划周六去书店，准备事项'+'笔记'*25)
            new=a.save(scope,'书店计划','改到周日去书店，仍然准备'+'清单'*20)
            a.save(scope,'登山计划','打算去登山')
            for mid,time in [(old,'2025-01-01T00:00:00+00:00'),(new,'2025-01-02T00:00:00+00:00')]:
                a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE id=?',(time,mid))
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00' WHERE id NOT IN (?,?)",(old,new));a.flow.db.commit()
            a.relate_sources(scope,new,old,'corrects');a.multi_event_context_enabled=True
            q='书店计划和登山计划怎么样';events=[e for e,_ in a._named_events(scope,q,a.flow._events(scope))]
            cutoff=datetime(2026,1,1,tzinfo=timezone.utc)
            minimum=[a._context_event(scope,e,q,2000,cutoff=cutoff,minimum_only=True) for e in events]
            wrapper={'多个事件':[],'说明':'分别是不同事件的历史原话，不表示事件之间存在关联'}
            overhead=len(json.dumps(wrapper,ensure_ascii=False,separators=(',',':')))+1
            budget=overhead+sum(map(len,minimum))
            self.assertGreater(max(map(len,minimum)),(budget-overhead)//2)
            result=a.context_packet(scope,q,budget=budget,as_of=cutoff)
            self.assertTrue(result);self.assertLessEqual(len(result),budget)
            packets=json.loads(result)['多个事件'];book=next(x for x in packets if x['事件']=='书店计划')
            self.assertEqual(len(book['原话']),2);self.assertEqual(book['关系'][0]['类型'],'修正')
            self.assertEqual(a.context_packet(scope,q,budget=budget-1,as_of=cutoff),'')
            a.compact_context_enabled=True
            compact=a.context_packet(scope,q,budget=budget,as_of=cutoff)
            self.assertTrue(compact);self.assertLessEqual(len(compact),budget)
            a.close()

    def test_two_event_context_is_scoped_bounded_and_unambiguous(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');other=a.scope('2','s')
            old=a.save(scope,'书店计划','原计划周六去书店')
            new=a.save(scope,'书店计划','改到周日去书店')
            a.save(scope,'登山计划','打算去四姑娘山')
            for mid,stamp in [(old,'2025-01-01T00:00:00+00:00'),(new,'2025-01-02T00:00:00+00:00')]:
                a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE id=?',(stamp,mid))
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00' WHERE id NOT IN (?,?)",(old,new));a.flow.db.commit()
            a.relate_sources(scope,new,old,'corrects');q='书店计划和登山计划怎么样'
            self.assertEqual(a.context_packet(scope,q,budget=800),'')
            a.multi_event_context_enabled=True
            result=a.context_packet(scope,q,budget=800);packet=json.loads(result)
            self.assertLessEqual(len(result),800);self.assertEqual(len(packet['多个事件']),2)
            self.assertIn('不表示事件之间存在关联',packet['说明'])
            first=next(x for x in packet['多个事件'] if x['事件']=='书店计划')
            self.assertEqual(len(first['原话']),2);self.assertEqual(first['关系'][0]['类型'],'修正')
            self.assertEqual(a.context_packet(scope,q,budget=300),'')
            self.assertEqual(a.context_packet(other,q,budget=800),'')
            negative=a.context_packet(scope,'书店计划不是登山计划',budget=800)
            self.assertNotIn('多个事件',negative);self.assertNotIn('打算去四姑娘山',negative)
            self.assertEqual(a.context_packet(scope,'“书店计划”和登山计划',budget=800),'')
            self.assertEqual(a.context_packet(scope,q,budget=800,as_of=datetime(2025,1,2,tzinfo=timezone.utc)),'')
            eid=next(x['id'] for x in a.flow._events(scope) if x['title']=='登山计划')
            self.assertEqual(a.context_packet(scope,q,budget=800,exclude_ids={eid}),'')
            self.assertEqual(a.context_turn(scope,q,budget=800)[1],None)
            a.close()

    def test_two_event_context_requires_non_overlapping_explicit_names(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s')
            a.save(scope,'书店','提到过买书');a.save(scope,'周末书店','计划周末买书')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            a.multi_event_context_enabled=True
            self.assertEqual(a.context_packet(scope,'周末书店怎么样',budget=700),'')
            p=json.loads(a.context_packet(scope,'书店和周末书店分别怎么样',budget=700))
            self.assertEqual(len(p['多个事件']),2)
            a.close()

    def test_brief_overview_priority_is_smaller_optional_and_guarded(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');other=a.scope('2','s')
            ids=[a.save(scope,'登山计划','我提到四姑娘山'+str(i)+'，装备和准备事项：'+'装备'*45) for i in range(4)]
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");a.flow.db.commit()
            a.save_source_brief(scope,'登山计划',ids[0],'四姑娘山','我提过四姑娘山的登山计划。')
            a.reviewed_brief_context_enabled=True
            normal=a.context_packet(scope,'登山计划是什么',budget=450)
            self.assertIn('原话',json.loads(normal))
            a.brief_overview_priority_enabled=True
            brief=a.context_packet(scope,'登山计划是什么',budget=450)
            self.assertIn('人工摘要',json.loads(brief));self.assertLess(len(brief),len(normal))
            self.assertIn('原话',json.loads(a.context_packet(scope,'登山计划是什么',budget=2000)))
            self.assertNotIn('人工摘要',a.context_packet(scope,'登山计划最初是什么',budget=450))
            self.assertNotIn('人工摘要',a.context_packet(scope,'登山计划装备型号是什么',budget=450))
            self.assertEqual(a.context_packet(other,'登山计划是什么',budget=450),'')
            a.capture(scope,'change','登山计划改到周日')
            self.assertNotIn('人工摘要',a.context_packet(scope,'登山计划是什么',budget=450))
            self.assertEqual(len(a.show(scope,'登山计划')),4);a.close()

    def test_preference_rank_requires_evidence_and_respects_corrections(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');other=a.scope('2','s')
            m1=a.save(scope,'登山A','我喜欢四姑娘山登山路线')
            m2=a.save(scope,'登山A','我一直喜欢四姑娘山登山路线')
            m3=a.save(scope,'登山B','我喜欢四姑娘山登山路线')
            for mid,t in [(m1,'2025-01-01T00:00:00+00:00'),(m2,'2025-01-02T00:00:00+00:00'),(m3,'2025-01-01T00:00:00+00:00')]:
                a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE scope=? AND id=?',(t,scope,mid))
            a.flow.db.commit();events=a.flow._events(scope)
            query={'content':'四姑娘山登山路线','recorded_at':'2025-02-01T00:00:00+00:00'}
            base=a._weak_candidate_details(scope,query,events)
            a.preference_rank_enabled=True;a.weak_association_enabled=True
            ranked=a._weak_candidate_details(scope,query,events)
            self.assertEqual({x['event_id'] for x in base},{x['event_id'] for x in ranked})
            weights={x['title']:x['preference_boost'] for x in ranked}
            self.assertEqual(weights['登山A'],0.15);self.assertEqual(weights['登山B'],0.0)
            self.assertEqual(len(a.preference_clues(scope,'登山A')['evidence']),2)
            self.assertTrue(all(x['preference_boost']==0 for x in a._weak_candidate_details(scope,
                dict(query,content='以前四姑娘山登山路线'),events)))
            self.assertEqual(a._weak_candidate_details(scope,query,[]),[])
            packet=json.loads(a.context_packet(scope,query['content'],as_of=datetime(2025,2,1,tzinfo=timezone.utc)))
            self.assertIn('偏好字面线索',packet['排序提示'])
            with self.assertRaises(ValueError):a.exclude_preference_rank(other,'登山A')
            a.exclude_preference_rank(scope,'登山A');self.assertTrue(a.preference_clues(scope,'登山A')['excluded'])
            a.close();a=Store(d,'',FakeEncoder);a.preference_rank_enabled=True
            self.assertTrue(a.preference_clues(scope,'登山A')['excluded'])
            a.exclude_preference_rank(scope,'登山A',False)
            self.assertEqual(len(a.preference_clues(scope,'登山A')['evidence']),2)
            newer=a.save(scope,'登山A','我不再喜欢四姑娘山登山路线')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-03T00:00:00+00:00' WHERE id=?",(newer,));a.flow.db.commit()
            self.assertEqual(a.preference_clues(scope,'登山A')['evidence'],[])
            self.assertEqual(next(x for x in a._weak_candidate_details(scope,query,a.flow._events(scope)) if x['title']=='登山A')['preference_boost'],0.0)
            a.close()

    def test_preference_rank_excludes_repeated_quotes_and_corrected_sources(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s')
            ids=[a.save(scope,'登山','我喜欢登山'),a.save(scope,'登山','我喜欢登山'),
                 a.save(scope,'登山','他说“我喜欢登山”')]
            for i,mid in enumerate(ids,1):a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE id=?',
                 (f'2025-01-0{i}T00:00:00+00:00',mid))
            a.flow.db.commit()
            self.assertEqual(len(a.preference_clues(scope,'登山')['evidence']),1)
            a.relate_sources(scope,ids[1],ids[0],'corrects')
            self.assertEqual([x['id'] for x in a.preference_clues(scope,'登山')['evidence']],[ids[1]])
            a.close()

    def test_backup_verify_and_restore_isolated_copy(self):
        from astrbot_plugin_digital_memory_store.recovery import verify_backup,restore_to_new_directory
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data=root/'source';a=Store(data,'',FakeEncoder);scope=a.scope('1','s')
            a.save(scope,'出行','计划去书店');a.capture(scope,'2','后来去了')
            backup=data/'backups'/a.backup();a.close()
            report=verify_backup(backup)
            self.assertTrue(report['ok']);self.assertEqual(report['counts']['captured'],1)
            target=root/'restored';self.assertTrue(restore_to_new_directory(backup,target)['ok'])
            b=Store(target,'',FakeEncoder)
            self.assertEqual(b.show(scope,'出行')[0]['content'],'计划去书店')
            self.assertEqual(b.pending(scope)[0]['content'],'后来去了');b.close()
            with self.assertRaises(FileExistsError):restore_to_new_directory(backup,target)
            c=Store(target,'',FakeEncoder);self.assertEqual(len(c.show(scope,'出行')),1);c.close()

    def test_backup_detects_broken_capture_link_and_cleans_failed_output(self):
        from astrbot_plugin_digital_memory_store.recovery import verify_backup,restore_to_new_directory
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=Store(root/'db','',FakeEncoder);scope=a.scope('1','s')
            a.capture(scope,'1','计划去书店');a.link_capture(scope,1,'书店')
            backup=root/'db'/'backups'/a.backup()
            self.assertTrue(verify_backup(backup)['checks']['capture_links'])
            with _conn(backup) as db:
                db.execute("UPDATE dl_capture SET linked_source='missing'")
            report=verify_backup(backup)
            self.assertFalse(report['ok']);self.assertEqual(report['counts']['broken_capture_links'],1)
            with self.assertRaises(ValueError):restore_to_new_directory(backup,root/'refused')
            self.assertFalse((root/'refused').exists())
            a.flow.db.execute("UPDATE dl_capture SET linked_title='错误事件'");a.flow.db.commit()
            before=set((root/'db'/'backups').iterdir())
            with self.assertRaises(ValueError):a.backup()
            self.assertEqual(before,set((root/'db'/'backups').iterdir()));a.close()

    def test_recovery_rejects_mismatched_hash_and_unknown_database(self):
        from astrbot_plugin_digital_memory_store.recovery import verify_backup,restore_to_new_directory
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=Store(root/'db','',FakeEncoder);scope=a.scope('1','s')
            a.capture(scope,'1','原始内容');backup=root/'db'/'backups'/a.backup();a.close()
            with _conn(backup) as db:db.execute("UPDATE dl_capture SET content='被修改的内容'")
            report=verify_backup(backup)
            self.assertFalse(report['ok']);self.assertIn('capture_hash_mismatch',report['errors'])
            with self.assertRaises(ValueError):restore_to_new_directory(backup,root/'refused')
            self.assertFalse((root/'refused').exists())
            unknown=root/'unknown.sqlite3'
            with _conn(unknown) as db:db.execute('CREATE TABLE sample(x)')
            self.assertIn('unknown_application_id',verify_backup(unknown)['errors'])

    def test_new_event_group_selective_confirmation(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s')
            for mid,text in [('1','周日去书店'),('2','想买一本书'),('3','准备去登山'),('4','无关原话')]:
                a.capture(scope,mid,text,recorded_at='2025-01-01T00:00:00+00:00')
            sources=a.next_new_group_sources(scope)
            self.assertEqual([x['seq'] for x in sources],[1,2,3,4])
            self.assertEqual([x['seq'] for x in a.next_new_group_sources(scope,2)],[3,4])
            raw=json.dumps({'groups':[{'title':'书店','items':[{'seq':1,'quote':'去书店'},
                    {'seq':2,'quote':'买一本书'}]},{'title':'登山','items':[{'seq':3,'quote':'去登山'}]}]},ensure_ascii=False)
            did=a.save_new_group_draft(scope,sources,raw)['draft_id']
            for selection in ([1,1],[4],[1,4],[]):
                with self.assertRaises(ValueError):a.confirm_new_group_draft(scope,did,selection)
            self.assertEqual(a.confirm_new_group_draft(scope,did,[1])['completed'],[1])
            self.assertEqual(a.show(scope,'登山'),[])
            self.assertEqual([x['seq'] for x in a.pending(scope)],[2,3,4])
            self.assertEqual(a.confirm_new_group_draft(scope,did,[1])['completed'],[1])
            self.assertEqual(a.confirm_new_group_draft(scope,did,[2,3])['completed'],[2,3])
            self.assertEqual([x['seq'] for x in a.pending(scope)],[4]);a.close()

    def test_new_event_group_recovers_after_ingest_before_review(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s')
            a.capture(scope,'1','明天去书店',recorded_at='2025-01-01T00:00:00+00:00')
            raw=json.dumps({'groups':[{'title':'书店计划','items':[{'seq':1,'quote':'去书店'}]}]},ensure_ascii=False)
            draft=a.save_new_group_draft(scope,a.new_group_sources(scope,[1]),raw)
            original=a.flow.review
            def fail(**kwargs):raise RuntimeError('interrupted after ingest')
            a.flow.review=fail
            self.assertEqual(a.confirm_new_group_draft(scope,draft['draft_id'])['failed'],1)
            a.flow.review=original
            self.assertEqual(a.confirm_new_group_draft(scope,draft['draft_id'])['completed'],[1])
            self.assertEqual(len(a.show(scope,'书店计划')),1)
            a.close()

    def test_new_event_groups_recovery_and_title_conflicts(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');other=a.scope('2','s')
            for mid,text in [('1','周日去书店'),('2','想买一本书'),('3','准备去登山')]:
                a.capture(scope,mid,text,recorded_at='2025-01-01T00:00:00+00:00')
            sources=a.new_group_sources(scope,[1,2,3])
            groups={'groups':[{'title':'书店计划','items':[{'seq':1,'quote':'周日去书店'},
                    {'seq':2,'quote':'买一本书'}]},{'title':'登山计划','items':[{'seq':3,'quote':'去登山'}]}]}
            raw=json.dumps(groups,ensure_ascii=False);draft=a.save_new_group_draft(scope,sources,raw);did=draft['draft_id']
            with self.assertRaises(ValueError):a.confirm_new_group_draft(other,did)
            link=a.link_capture
            def fail(scope,seq,title):
                if seq==2:raise RuntimeError('interrupted')
                return link(scope,seq,title)
            a.link_capture=fail
            self.assertEqual(a.confirm_new_group_draft(scope,did),{'completed':[],'failed':2})
            self.assertEqual(len(a.pending(scope)),3)
            self.assertEqual(len(a.show(scope,'书店计划')),0)
            a.link_capture=link
            # 模拟旧版在中断前已提交一个事件和一条关联，升级后继续复用。
            eid='E-legacyfixture'
            a.flow.db.execute('INSERT INTO events(scope,id,title) VALUES(?,?,?)',(scope,eid,'书店计划'))
            a.flow.db.execute('INSERT INTO dl_new_group_events VALUES(?,?,?,?)',(scope,did,'书店计划',eid))
            a.flow.db.commit();a.link_capture(scope,1,'书店计划')
            self.assertEqual(a.confirm_new_group_draft(scope,did),{'completed':[1,2,3],'failed':None})
            self.assertEqual(a.confirm_new_group_draft(scope,did),{'completed':[1,2,3],'failed':None})
            self.assertEqual(len(a.show(scope,'书店计划')),2)
            a.save(scope,'书店计划','其他人增加的记录')
            with self.assertRaises(ValueError):a.confirm_new_group_draft(scope,did)
            a.capture(scope,'4','新的计划',recorded_at='2025-01-02T00:00:00+00:00')
            bad={'groups':[{'title':'登山计划','items':[{'seq':4,'quote':'新的计划'}]}]}
            with self.assertRaises(ValueError):a.save_new_group_draft(scope,a.new_group_sources(scope,[4]),json.dumps(bad,ensure_ascii=False))
            bad['groups'][0]['title']='新事件'
            d2=a.save_new_group_draft(scope,a.new_group_sources(scope,[4]),json.dumps(bad,ensure_ascii=False))
            a.save(scope,'新事件','别的原话')
            with self.assertRaises(ValueError):a.confirm_new_group_draft(scope,d2['draft_id'])
            self.assertEqual([x['seq'] for x in a.pending(scope)],[4]);a.close()

    def test_new_group_validator_rejects_fabricated_evidence(self):
        import json
        from astrbot_plugin_digital_memory_store.new_groups import validate_new_groups
        sources=[{'seq':1,'content':'明天去书店'}]
        data={'groups':[{'title':'书店','items':[{'seq':1,'quote':'根本没说过'}]}]}
        with self.assertRaises(ValueError):validate_new_groups(json.dumps(data,ensure_ascii=False),sources)
        with self.assertRaises(ValueError):validate_new_groups('{"groups":[],"groups":[]}',sources)

    def test_history_import_atomic_idempotent_and_provenance(self):
        import json
        from astrbot_plugin_digital_memory_store.history_import import parse_jsonl,import_records
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','test:FriendMessage:1');other=a.scope('2','test:FriendMessage:1')
            source=Path(d)/'history.jsonl'
            lines=[{'message_id':'a','speaker':'user','content':'周末去书店','recorded_at':'2025-01-01T12:00:00+08:00'},
                   {'message_id':'b','speaker':'user','content':'买一本书','recorded_at':'2025-01-02T12:00:00+08:00'}]
            source.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in lines),encoding='utf8')
            records=parse_jsonl(source)
            self.assertEqual(import_records(a,scope,records,'export')['inserted'],2)
            self.assertEqual(import_records(a,scope,records,'export')['duplicates'],2)
            self.assertEqual(a.pending(scope)[0]['recorded_at'],'2025-01-01T04:00:00+00:00')
            self.assertEqual(a.pending(scope)[0]['imported'],1);self.assertEqual(a.pending(other),[])
            changed=[dict(records[0],content='其他内容'),dict(records[1],message_id='new')]
            with self.assertRaises(ValueError):import_records(a,scope,changed,'export')
            self.assertEqual(len(a.pending(scope)),2)
            a.link_capture(scope,1,'书店');self.assertTrue(a.show(scope,'书店')[0]['imported'])
            packet=json.loads(a.context_packet(scope,'书店是什么',budget=700))
            self.assertIn('导入时间说明',packet)
            a.capture(scope,'live','后来的一条')
            with self.assertRaises(ValueError):import_records(a,scope,[dict(records[1],message_id='new')],'export')
            a.close()

    def test_history_import_rejects_bad_rows_before_write(self):
        import json
        from astrbot_plugin_digital_memory_store.history_import import parse_jsonl
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'test.jsonl';ok={'message_id':'a','speaker':'user','content':'原文',
                'recorded_at':'2025-01-01T00:00:00+00:00'}
            for bad in (dict(ok,speaker='assistant'),dict(ok,recorded_at='2025-01-01T00:00:00'),
                        dict(ok,content='x'*401),dict(ok,message_id='a')):
                path.write_text(json.dumps(ok)+'\n'+json.dumps(bad),encoding='utf8')
                with self.assertRaises(ValueError):parse_jsonl(path)

    def test_segment_cache_scope_expiry_and_source_binding(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');other=a.scope('2','s')
            a.save(scope,'书店','计划周日去书店')
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'");a.flow.db.commit()
            snap=a.model_segment_snapshot(scope,'书店');source=snap['sources'][0]
            raw=json.dumps({'summary':'书店计划','source_id':source['id'],'quote':'周日去书店'},ensure_ascii=False)
            a.save_segment_cache(scope,snap,0,'provider-a',raw)
            self.assertEqual(a.read_segment_cache(scope,snap,0,'provider-a'),raw)
            self.assertIsNone(a.read_segment_cache(scope,snap,0,'provider-b'))
            self.assertEqual(a.segment_cache_count(other),0)
            a.flow.db.execute("UPDATE dl_segment_cache SET created_at='2000-01-01T00:00:00+00:00'");a.flow.db.commit()
            self.assertIsNone(a.read_segment_cache(scope,snap,0,'provider-a'))
            a.save_segment_cache(scope,snap,0,'provider-a',raw)
            a.save(scope,'书店','后来还有一条')
            with self.assertRaises(ValueError):a.read_segment_cache(scope,snap,0,'provider-a')
            self.assertEqual(a.clear_segment_cache(other),0)
            self.assertEqual(a.clear_segment_cache(scope),1)
            a.close()

    def test_segmented_draft_coverage_relations_and_stale(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s')
            ids=[a.save(scope,'长行程','行程记录'+str(i)) for i in range(25)]
            for i,mid in enumerate(ids):
                a.flow.db.execute("UPDATE messages SET recorded_at=? WHERE scope=? AND id=?",
                    (f'2026-01-{i+1:02}T00:00:00+00:00',scope,mid))
            a.flow.db.commit();a.relate_sources(scope,ids[24],ids[0],'related')
            snap=a.model_segment_snapshot(scope,'长行程')
            self.assertEqual(len(snap['segments']),2)
            self.assertEqual(set(sum(snap['segments'],[])),set(ids))
            self.assertTrue(any(ids[0] in part and ids[24] in part for part in snap['segments']))
            raw=[]
            for i in range(len(snap['segments'])):
                sub=a.model_segment_part(snap,i);source=sub['sources'][0]
                raw.append(json.dumps({'summary':'这一段的摘要','source_id':source['id'],'quote':'行程记录'},ensure_ascii=False))
            first=json.loads(raw[0]);final=json.dumps(first,ensure_ascii=False)
            with self.assertRaises(ValueError):a.save_segmented_brief_draft(scope,snap,raw[:-1],final)
            bad=dict(first,quote='记录0')
            with self.assertRaises(ValueError):a.save_segmented_brief_draft(scope,snap,raw,json.dumps(bad,ensure_ascii=False))
            draft=a.save_segmented_brief_draft(scope,snap,raw,final);did=draft['draft_id']
            self.assertEqual(len(draft['segment_results']),2);self.assertIsNone(a.read_source_brief(scope,'长行程'))
            a.confirm_model_brief_draft(scope,did)
            self.assertEqual(a.read_source_brief(scope,'长行程')['origin'],'model_reviewed')
            a.save(scope,'长行程','后续记录')
            self.assertFalse(a.read_model_brief_draft(scope,did)['current'])
            with self.assertRaises(ValueError):a.confirm_model_brief_draft(scope,did)
            a.close()

    def test_extended_brief_complete_sources_and_legacy_origin(self):
        import json
        from astrbot_plugin_digital_memory_store.model_drafts import make_brief_prompt
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s')
            ids=[a.save(scope,'长事件','第'+str(i)+'条完整记录') for i in range(6)]
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'");a.flow.db.commit()
            snap=a.model_brief_snapshot(scope,'长事件')
            self.assertEqual(len(snap['sources']),6)
            payload=json.loads(make_brief_prompt(snap).split('\n',1)[1])
            self.assertEqual([x['content'] for x in payload['原文']],[x['content'] for x in snap['sources']])
            output={'summary':'这是模型生成的摘要','source_id':ids[0],'quote':'完整记录'}
            draft=a.save_model_brief_draft(scope,snap,json.dumps(output,ensure_ascii=False))
            a.confirm_model_brief_draft(scope,draft['draft_id'])
            self.assertEqual(a.read_source_brief(scope,'长事件')['origin'],'model_reviewed')
            a.close()
            # 模拟上一版本数据库升级：关联草稿可回填，其他旧摘要保持未知。
            db=sqlite3.connect(str(Path(d)/'manual_test_memory.sqlite3'))
            db.execute('ALTER TABLE dl_source_briefs DROP COLUMN origin');db.commit();db.close()
            a=Store(d,'',FakeEncoder)
            self.assertEqual(a.read_source_brief(scope,'长事件')['origin'],'model_reviewed')
            a.save_source_brief(scope,'长事件',ids[0],'完整记录','人工整理内容')
            self.assertEqual(a.read_source_brief(scope,'长事件')['origin'],'human_written')
            long_id=a.save(scope,'远足计划','准备去爬山，'+('检查装备'*70))
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00' WHERE id=?",(long_id,));a.flow.db.commit()
            long_snap=a.model_brief_snapshot(scope,'远足计划')
            long_draft=a.save_model_brief_draft(scope,long_snap,json.dumps({'summary':'准备去爬山。',
                'source_id':long_id,'quote':'准备去爬山'},ensure_ascii=False))
            a.confirm_model_brief_draft(scope,long_draft['draft_id'])
            a.flow.db.execute("UPDATE dl_source_briefs SET created_at='2026-01-01T00:00:00+00:00' WHERE event_id=?",(long_snap['event_id'],));a.flow.db.commit()
            a.reviewed_brief_context_enabled=True
            packet=json.loads(a.context_packet(scope,'远足计划是什么',budget=300))
            self.assertIn('模型生成、人工确认',packet['说明']);self.assertIn('摘要',packet)
            for i in range(6,21):a.save(scope,'长事件','第'+str(i)+'条完整记录')
            with self.assertRaises(ValueError):a.model_brief_snapshot(scope,'长事件')
            self.assertFalse(a.read_model_brief_draft(scope,draft['draft_id'])['current'])
            a.close()

    def test_model_brief_confirmation_validation_and_staleness(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');other=a.scope('2','s')
            a.save(scope,'书店','计划周日去书店')
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'");a.flow.db.commit()
            snapshot=a.model_brief_snapshot(scope,'书店');source=snapshot['sources'][0]
            output={'summary':'计划周日去书店。','source_id':source['id'],'quote':'周日去书店'}
            with self.assertRaises(ValueError):
                a.save_model_brief_draft(scope,snapshot,json.dumps(dict(output,quote='从未说过'),ensure_ascii=False))
            draft=a.save_model_brief_draft(scope,snapshot,json.dumps(output,ensure_ascii=False));did=draft['draft_id']
            self.assertIsNone(a.read_source_brief(scope,'书店'))
            with self.assertRaises(ValueError):a.confirm_model_brief_draft(other,did)
            brief=a.confirm_model_brief_draft(scope,did)
            self.assertEqual(a.confirm_model_brief_draft(scope,did),brief)
            self.assertEqual(a.read_source_brief(scope,'书店')['brief'],output['summary'])
            a.save(scope,'书店','后来还想看看杂志')
            self.assertFalse(a.read_model_brief_draft(scope,did)['current'])
            with self.assertRaises(ValueError):a.confirm_model_brief_draft(scope,did)
            a.close()

    def test_group_draft_selective_confirmation_and_full_sources(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s')
            a.save(scope,'书店','以前去过书店')
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'");a.flow.db.commit()
            a.capture(scope,'1','计划周日去书店',recorded_at='2026-02-01T00:00:00+00:00')
            a.capture(scope,'2',' 也想去书店 ',recorded_at='2026-02-01T00:00:00+00:00')
            a.capture(scope,'3','没有被分组的原话',recorded_at='2026-02-01T00:00:00+00:00')
            snap=a.model_group_snapshot(scope,[1,2,3],['书店'])
            result={'groups':[{'title':'书店','items':[{'seq':1,'quote':'周日'}, {'seq':2,'quote':'书店'}]}]}
            draft=a.save_group_draft(scope,snap,json.dumps(result,ensure_ascii=False));did=draft['draft_id']
            self.assertEqual([x['content'] for x in a.read_group_draft(scope,did)['sources']],
                             ['计划周日去书店',' 也想去书店 ','没有被分组的原话'])
            for selection in ([1,1],[3],[1,3],[]):
                with self.assertRaises(ValueError):a.confirm_group_draft(scope,did,selection)
            self.assertEqual(len(a.pending(scope)),3)
            self.assertEqual(a.confirm_group_draft(scope,did,[1])['completed'],[1])
            self.assertEqual(a.confirm_group_draft(scope,did,[1])['completed'],[1])
            self.assertEqual([x['seq'] for x in a.pending(scope)],[2,3])
            self.assertEqual(a.read_group_draft(scope,did)['sources'][0]['state'],'linked')
            self.assertEqual(a.confirm_group_draft(scope,did,[2])['completed'],[2])
            self.assertIn(' 也想去书店 ',[r['content'] for r in a.show(scope,'书店')])
            a.close()

    def test_group_draft_partial_retry_and_history_guard(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.save(s,'书店','以前去过书店');a.save(s,'登山','以前爬过山')
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'");a.flow.db.commit()
            a.capture(s,'1','周日去书店',recorded_at='2026-02-01T00:00:00+00:00')
            a.capture(s,'2','准备去登山',recorded_at='2026-02-01T00:00:00+00:00')
            snap=a.model_group_snapshot(s,[1,2],['书店','登山'])
            raw=json.dumps({'groups':[{'title':'书店','items':[{'seq':1,'quote':'去书店'}]},
                                      {'title':'登山','items':[{'seq':2,'quote':'去登山'}]}]},ensure_ascii=False)
            draft=a.save_group_draft(s,snap,raw);did=draft['draft_id']
            with self.assertRaises(ValueError):a.confirm_group_draft(other,did)
            link=a.link_capture
            def interrupted(scope,seq,title):
                if seq==2:raise RuntimeError('interrupted')
                return link(scope,seq,title)
            a.link_capture=interrupted
            self.assertEqual(a.confirm_group_draft(s,did),{'completed':[],'failed':2})
            self.assertEqual(len(a.pending(s)),2)
            a.link_capture=link
            self.assertEqual(a.confirm_group_draft(s,did),{'completed':[1,2],'failed':None})
            self.assertEqual(a.confirm_group_draft(s,did),{'completed':[1,2],'failed':None})
            self.assertEqual(a.pending(s),[])
            a.save(s,'登山','后续增加的记录')
            with self.assertRaises(ValueError):a.confirm_group_draft(s,did)
            a.close()

    def test_model_group_output_is_bounded_and_source_grounded(self):
        import json
        from astrbot_plugin_digital_memory_store.model_groups import validate_group_output
        snapshot={'sources':[{'seq':1,'content':'计划周日去书店'},{'seq':2,'content':'准备去爬山'}],
                  'events':[{'title':'书店计划'},{'title':'登山计划'}]}
        result={'groups':[{'title':'书店计划','items':[{'seq':1,'quote':'周日去书店'}]}]}
        self.assertEqual(validate_group_output(json.dumps(result,ensure_ascii=False),snapshot),result)
        result['groups'][0]['items'][0]['quote']='从未说过'
        with self.assertRaises(ValueError):validate_group_output(json.dumps(result,ensure_ascii=False),snapshot)
        result['groups'][0]['items'][0]['quote']='周日'
        result['groups'][0]['title']='编造的事件'
        with self.assertRaises(ValueError):validate_group_output(json.dumps(result,ensure_ascii=False),snapshot)
        with self.assertRaises(ValueError):validate_group_output('{"groups":[],"groups":[]}',snapshot)

    def test_group_suggestions_are_scoped_bounded_and_unconfirmed(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.save(s,'书店计划','之前去过书店')
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'");a.flow.db.commit()
            for mid,text in [('1','书店计划周六去'),('2','改到周日'),('3','不是书店计划'),('4','改到周一'),('5','书店计划下周去'),('6','书店计划要提前预约')]:
                a.capture(s,mid,text,recorded_at='2026-02-01T00:00:00+00:00')
            a.capture(other,'x','书店计划也改了',recorded_at='2026-02-01T00:00:00+00:00')
            result=a.group_pending_suggestions(s)
            self.assertEqual([[x['seq'] for x in g['items']] for g in result['groups']],[[1,2],[5,6]])
            self.assertEqual(result['groups'][0]['items'][1]['method'],'相邻可能更新')
            self.assertEqual(len(a.pending(s)),6)
            self.assertEqual(a.group_pending_suggestions(other)['groups'],[])
            a.flow.db.execute("UPDATE dl_capture SET content='篡改' WHERE scope=? AND seq=2",(s,));a.flow.db.commit()
            self.assertEqual([[x['seq'] for x in g['items']] for g in a.group_pending_suggestions(s)['groups']],[[5,6]])
            a.close()

    def test_linked_update_warning_review_and_isolation(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'书店','原计划周六去书店')
            new=a.save(s,'书店','计划改到周日')
            for i,mid in enumerate((old,new),1):
                a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE scope=? AND id=?',(f'2026-01-0{i}T00:00:00+00:00',s,mid))
            a.flow.db.commit()
            clues=a.linked_update_clues(s,'书店')['items']
            self.assertEqual(clues[0]['new_id'],new);self.assertEqual(clues[0]['old_id'],old)
            self.assertIn('关系提醒',json.loads(a.context_packet(s,'书店怎么样')))
            with self.assertRaises(ValueError):a.dismiss_linked_update(other,'书店',new)
            a.relate_sources(s,new,old,'related')
            self.assertEqual(len(a.linked_update_clues(s,'书店')['items']),1)
            a.dismiss_linked_update(s,'书店',new)
            self.assertEqual(a.linked_update_clues(s,'书店')['items'],[])
            self.assertNotIn('关系提醒',json.loads(a.context_packet(s,'书店怎么样')))
            self.assertEqual(len(a.show(s,'书店')),2)
            a.close()
            a=Store(d,'',FakeEncoder)
            self.assertEqual(a.linked_update_clues(s,'书店')['items'],[])
            newer=a.save(s,'书店','又改到周一')
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-03T00:00:00+00:00' WHERE scope=? AND id=?",(s,newer));a.flow.db.commit()
            self.assertEqual(a.linked_update_clues(s,'书店')['items'][0]['new_id'],newer)
            a.relate_sources(s,newer,new,'corrects')
            self.assertEqual(a.linked_update_clues(s,'书店')['items'],[])
            a.close()

    def test_detail_priority_retains_correction_and_time_selection(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            old=a.save(s,'登山计划','准备黑色登山背包')
            new=a.save(s,'登山计划','登山背包换成蓝色')
            latest=a.save(s,'登山计划','午饭打算吃面条')
            for i,mid in enumerate((old,new,latest),1):
                a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE scope=? AND id=?',(f'2026-01-0{i}T00:00:00+00:00',s,mid))
            a.flow.db.commit();a.relate_sources(s,new,old,'corrects')
            baseline=json.loads(a.context_packet(s,'登山计划的登山背包呢',budget=300))
            self.assertIn('午饭',baseline['原话'][-1]['内容'])
            a.detail_priority_enabled=True
            result=json.loads(a.context_packet(s,'登山计划的登山背包呢',budget=300))
            self.assertEqual(len(result['原话']),2)
            self.assertIn('黑色',result['原话'][0]['内容']);self.assertIn('蓝色',result['原话'][1]['内容'])
            self.assertEqual(result['关系'][0]['类型'],'修正')
            self.assertIn('字面重合',result['选择依据'])
            self.assertEqual(a.context_packet(s,'登山计划的登山背包呢',budget=130),'')
            fallback=json.loads(a.context_packet(s,'登山计划怎么样',budget=300))
            self.assertNotIn('选择依据',fallback)
            self.assertIn('午饭',fallback['原话'][-1]['内容'])
            negative=json.loads(a.context_packet(s,'登山计划不是登山背包',budget=300))
            self.assertNotIn('选择依据',negative)
            a.close()

    def test_context_uses_request_time_not_execution_time(self):
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            a.save(s,'书店','计划周末去书店')
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-02T00:00:00+00:00' WHERE scope=?",(s,));a.flow.db.commit()
            before=datetime(2026,1,1,tzinfo=timezone.utc);after=datetime(2026,1,3,tzinfo=timezone.utc)
            self.assertEqual(a.context_packet(s,'书店怎么样',as_of=before),'')
            self.assertIn('计划周末去书店',a.context_packet(s,'书店怎么样',as_of=after))
            self.assertEqual(a.context_packet(s,'书店怎么样',as_of=datetime(2026,1,3)),'')
            a.close()

    def test_context_excludes_current_capture_and_future_event_sources(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            a.save(s,'登山计划','计划攀登四姑娘山大峰')
            a.capture(s,'m1','四姑娘山大峰下周出发',recorded_at='2026-01-02T00:00:00+00:00')
            a.link_capture(s,1,'登山计划')
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00' WHERE scope=?",(s,));a.flow.db.commit()
            self.assertEqual(a.context_packet(s,'登山计划怎么样',current_message_id='m1'),'')
            self.assertIn('四姑娘山',a.context_packet(s,'登山计划怎么样',current_message_id='other'))
            packet,focus=a.context_turn(s,'登山计划怎么样',current_message_id='other')
            self.assertTrue(packet);self.assertIsNotNone(focus)
            self.assertEqual(a.context_turn(s,'后来呢',focus,current_message_id='m1'),('',None))
            a.weak_association_enabled=True;a.association_context_enabled=True
            weak=a.context_packet(s,'四姑娘山大峰怎么样',current_message_id='m1')
            self.assertNotIn('关联历史',weak)
            a.flow.db.execute("UPDATE messages SET recorded_at='2099-01-01T00:00:00+00:00' WHERE scope=?",(s,));a.flow.db.commit()
            self.assertEqual(a.context_packet(s,'登山计划怎么样',current_message_id='other'),'')
            a.close()

    def test_manual_update_link_recovery_and_source_checks(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'书店','原计划周六去书店')
            a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE scope=? AND id=?',('2026-01-01T00:00:00+00:00',s,old));a.flow.db.commit()
            a.capture(s,'m1','改成周日去');seq=a.pending(s)[0]['seq']
            with self.assertRaises(ValueError):a.link_capture_relation(other,seq,'书店',old,'corrects')
            with self.assertRaises(ValueError):a.link_capture_relation(s,seq,'书店','wrong','corrects')
            self.assertEqual(len(a.pending(s)),1)
            original=a.relate_sources
            def fail(*args):raise RuntimeError('interrupted')
            a.relate_sources=fail
            with self.assertRaises(RuntimeError):a.link_capture_relation(s,seq,'书店',old,'corrects')
            self.assertEqual(len(a.pending(s)),1)
            a.relate_sources=original
            linked=a.link_capture_relation(s,seq,'书店',old,'corrects')
            self.assertEqual(a.link_capture_relation(s,seq,'书店',old,'corrects'),linked)
            self.assertEqual(a.flow.db.execute('SELECT count(*) FROM relations').fetchone()[0],1)
            with self.assertRaises(ValueError):a.link_capture_relation(s,seq,'书店',old,'supersedes')
            self.assertEqual(len(a.show(s,'书店')),2)
            a.capture(s,'m2','下周去')
            seq2=a.pending(s)[0]['seq']
            a.flow.db.execute("UPDATE dl_capture SET recorded_at='2025-01-01T00:00:00+00:00' WHERE scope=? AND seq=?",(s,seq2));a.flow.db.commit()
            with self.assertRaises(ValueError):a.link_capture_relation(s,seq2,'书店',old,'related')
            self.assertEqual(len(a.pending(s)),1)
            a.close()

    def test_batch_link_preflight_scope_integrity_and_retry(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');other=a.scope('2','s')
            a.capture(scope,'1','周末去书店');a.capture(scope,'2','改到周日');a.capture(other,'3','其他人的记录')
            with self.assertRaises(ValueError):a.link_capture_batch(scope,[1,3],'书店')
            self.assertEqual(len(a.pending(scope)),2)
            a.flow.db.execute("UPDATE dl_capture SET content='被修改' WHERE scope=? AND seq=2",(scope,));a.flow.db.commit()
            with self.assertRaises(ValueError):a.link_capture_batch(scope,[1,2],'书店')
            self.assertEqual(len(a.pending(scope)),2)
            a.flow.db.execute("UPDATE dl_capture SET content='改到周日' WHERE scope=? AND seq=2",(scope,));a.flow.db.commit()
            original=a.link_capture
            def fail_once(s,seq,title):
                if seq==2:raise RuntimeError('simulated interruption')
                return original(s,seq,title)
            a.link_capture=fail_once
            result=a.link_capture_batch(scope,[2,1],'书店')
            self.assertEqual(result['completed'],[]);self.assertEqual(result['failed'],2)
            self.assertEqual(len(a.pending(scope)),2)
            a.link_capture=original
            self.assertEqual(a.link_capture_batch(scope,[1,2],'书店')['completed'],[1,2])
            self.assertEqual(a.pending(scope),[])
            self.assertEqual(len(a.pending(other)),1)
            with self.assertRaises(ValueError):a.link_capture_batch(scope,[1,2],'其他事件')
            a.close()

    def test_pending_update_can_trace_to_reviewed_source_without_event_name(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'一月登山','以前准备四姑娘山大峰')
            later=a.save(s,'一月登山','之后又聊了装备')
            for i,mid in enumerate((old,later)):
                a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',(f'2026-01-0{i+1}T00:00:00+00:00',s,mid))
            a.flow.db.commit()
            a.capture(s,'anchor','四姑娘山大峰的事情')
            a.capture(s,'change','改到周日')
            a.capture(other,'other','取消四姑娘山大峰')
            a.weak_association_enabled=True
            rows=a.update_clues(s)
            self.assertEqual(rows[1]['matches'][0]['title'],'一月登山')
            self.assertEqual(rows[1]['matches'][0]['old_id'],old)
            self.assertIn('历史原文词组匹配',rows[1]['matches'][0]['method'])
            context=json.loads(a.context_packet(s,'一月登山怎么样？'))
            self.assertIn('待审提醒',context)
            self.assertNotIn('改到周日',json.dumps(context,ensure_ascii=False))
            self.assertEqual(len(a.pending(s)),2)
            a.close()

    def test_ambiguous_update_does_not_become_a_confirmed_event(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            for title in ('登山准备','去年旅行'):
                a.save(s,title,'曾经提到四姑娘山大峰')
            a.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00' where scope=?",(s,));a.flow.db.commit()
            a.capture(s,'c','四姑娘山大峰改到周日')
            a.weak_association_enabled=True
            self.assertEqual(a.update_clues(s)[0]['matches'],[])
            self.assertEqual(len(a.pending(s)),1)
            self.assertIn('待审提醒',a.context_packet(s,'登山准备'))
            self.assertIn('待审提醒',a.context_packet(s,'去年旅行'))
            a.close()
    def test_pending_chain_keeps_multiple_updates_and_rejects_overflow(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');a.pending_context_enabled=True
            for mid,text in [('old','周末书店原计划周六去'),('change','改到周日去'),('cancel','取消了')]:a.capture(s,mid,text)
            a.flow.db.execute("update dl_capture set recorded_at='2026-01-01T00:00:00+00:00' where scope=?",(s,));a.flow.db.commit()
            query='周末书店计划怎么样？'
            result=json.loads(a.context_packet(s,query,current_message_id='current'))
            self.assertEqual(result['待审相关序号'],[1,2,3])
            self.assertIn('周日',result['后续可能更新']['原话'])
            self.assertIn('取消',result['进一步可能更新'][0]['原话'])
            first_two=a.context_packet(s,query,current_message_id='cancel')
            self.assertNotIn('进一步可能更新',first_two)
            self.assertEqual(a.context_packet(s,query,current_message_id='current',budget=len(first_two)),'')
            self.assertEqual(a.context_packet(s,query,current_message_id='current',exclude_ids={'pending:3'}),'')
            for mid,text in [('four','改成下周'),('five','推迟一天'),('six','取消了')]:a.capture(s,mid,text)
            a.flow.db.execute("update dl_capture set recorded_at='2026-01-01T00:00:00+00:00' where scope=?",(s,));a.flow.db.commit()
            self.assertEqual(a.context_packet(s,query,current_message_id='current'),'')
            a.close()

    def test_multiple_update_clues_refer_to_original_event(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            mid=a.save(s,'书店计划','原来周六去')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T00:00:00+00:00',s,mid));a.flow.db.commit()
            for msg,text in [('a','书店计划在周末'),('b','改到周日'),('c','取消了'),('d','天气真好'),('e','不去了')]:a.capture(s,msg,text)
            rows=a.update_clues(s)
            self.assertEqual(rows[2]['matches'][0]['title'],'书店计划')
            self.assertIn('连续更新',rows[2]['matches'][0]['method'])
            self.assertEqual(rows[4]['matches'],[])
            a.close()
    def test_pending_recall_keeps_possible_followup_update_together(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');a.pending_context_enabled=True
            a.capture(s,'old','周末书店原计划周六去')
            a.capture(s,'change','改到周日去')
            a.flow.db.execute("update dl_capture set recorded_at='2026-01-01T00:00:00+00:00' where scope=?",(s,));a.flow.db.commit()
            packet=json.loads(a.context_packet(s,'周末书店计划怎么样？',current_message_id='current'))
            self.assertIn('周六',packet['未整理历史原话'])
            self.assertIn('周日',packet['后续可能更新']['原话'])
            self.assertEqual(packet['待审相关序号'],[1,2])
            self.assertIn('尚未确认',packet['说明'])
            self.assertEqual(a.context_packet(s,'周末书店计划怎么样？',current_message_id='current',exclude_ids={'pending:2'}),'')
            single=a.context_packet(s,'周末书店计划怎么样？',current_message_id='change')
            self.assertIn('周六',single)
            self.assertEqual(a.context_packet(s,'周末书店计划怎么样？',current_message_id='current',budget=len(single)),'')
            self.assertEqual(len(a.pending(s)),2)
            a.close()

    def test_pending_recall_wont_group_across_intervening_message(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');a.pending_context_enabled=True
            for mid,text in [('old','周末书店原计划周六去'),('other','天气真不错'),('change','改到周日去')]:a.capture(s,mid,text)
            a.flow.db.execute("update dl_capture set recorded_at='2026-01-01T00:00:00+00:00' where scope=?",(s,));a.flow.db.commit()
            packet=json.loads(a.context_packet(s,'周末书店计划怎么样？',current_message_id='current'))
            self.assertNotIn('后续可能更新',packet)
            a.close()
    def test_pending_context_excludes_current_message_scope_and_ambiguity(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.pending_context_enabled=True
            a.capture(s,'old','以前考虑四姑娘山大峰')
            a.capture(s,'current','四姑娘山大峰路线怎样？')
            a.capture(other,'other','以前考虑四姑娘山大峰')
            a.flow.db.execute("update dl_capture set recorded_at='2026-01-01T00:00:00+00:00' where scope=?",(s,));a.flow.db.commit()
            query='四姑娘山大峰路线怎样？'
            payload=json.loads(a.context_packet(s,query,current_message_id='current'))
            self.assertEqual(payload['未整理历史原话'],'以前考虑四姑娘山大峰')
            self.assertIn('尚未确认',payload['说明'])
            self.assertEqual(a.context_packet(s,query),'')
            self.assertEqual(a.context_packet(s,query,current_message_id='current',exclude_ids={'pending:1'}),'')
            self.assertEqual(a.context_packet(other,query,current_message_id='other'),'')
            self.assertEqual(a.context_packet(s,'不是四姑娘山大峰',current_message_id='current'),'')
            a.capture(s,'old2','我记得四姑娘山大峰')
            a.flow.db.execute("update dl_capture set recorded_at='2026-01-01T00:00:00+00:00' where scope=?",(s,));a.flow.db.commit()
            self.assertEqual(a.context_packet(s,query,current_message_id='current'),'')
            self.assertEqual(len(a.pending(s)),3)
            a.close()

    def test_pending_index_invalidation_after_link_and_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');a.pending_context_enabled=True
            a.capture(s,'old','以前计划四姑娘山大峰')
            a.flow.db.execute("update dl_capture set recorded_at='2026-01-01T00:00:00+00:00' where scope=?",(s,));a.flow.db.commit()
            query='四姑娘山大峰路线怎样？'
            self.assertIn('未整理历史原话',a.context_packet(s,query,current_message_id='current'))
            seq=a.pending(s)[0]['seq']
            a.link_capture(s,seq,'登山')
            self.assertEqual(a.context_packet(s,query,current_message_id='current'),'')
            a.capture(s,'second','从前提及四姑娘山大峰')
            a.flow.db.execute("update dl_capture set content='数据损坏' where scope=? and state='pending'",(s,));a.flow.db.commit()
            self.assertEqual(a.context_packet(s,query,current_message_id='current'),'')
            a.close()
    def test_incremental_index_finds_beyond_old_cap_and_tracks_changes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            for i in range(205):a.save(s,'较长普通事件名称'+str(i).zfill(4),'无关的记录')
            mid=a.save(s,'山事','之前准备四姑娘山大峰')
            a.save(other,'其他用户','四姑娘山大峰')
            a.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00' where scope=?",(s,));a.flow.db.commit()
            a.capture(s,'q','四姑娘山大峰路线？');seq=a.pending(s)[0]['seq']
            result=a.explain_weak_candidates(s,seq)
            self.assertEqual([x['title'] for x in result],['山事'])
            a.weak_association_enabled=True;a.association_context_enabled=True
            self.assertIn('关联历史',a.context_packet(s,'四姑娘山大峰路线？'))
            self.assertEqual(a.flow.db.execute('select count(*) from dl_lex_events where scope=?',(s,)).fetchone()[0],206)
            self.assertEqual(a.flow.db.execute('select count(*) from dl_lex_dirty where scope=?',(s,)).fetchone()[0],0)
            # A changed source invalidates only its event, then the next query repairs its postings.
            text='之前准备香格里拉徒步';sha=hashlib.sha256(text.encode()).hexdigest()
            a.flow.db.execute('update messages set content=? where scope=? and id=?',(text,s,mid))
            a.flow.db.execute('update e417_ingest set source_sha256=? where scope=? and message_id=?',(sha,s,mid));a.flow.db.commit()
            self.assertEqual(a.flow.db.execute('select count(*) from dl_lex_dirty where scope=?',(s,)).fetchone()[0],1)
            self.assertEqual(a.explain_weak_candidates(s,seq),[])
            a.capture(s,'q2','香格里拉徒步路线？');seq2=a.pending(s)[-1]['seq']
            self.assertEqual(a.explain_weak_candidates(s,seq2)[0]['title'],'山事')
            a.close();b=Store(d,'',FakeEncoder)
            self.assertEqual(b.explain_weak_candidates(s,seq2)[0]['title'],'山事')
            b.close()

    def test_index_failure_rolls_back_and_old_database_is_seeded(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            mid=a.save(s,'山事','四姑娘山大峰')
            a.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00' where scope=?",(s,));a.flow.db.commit()
            a.capture(s,'q','四姑娘山大峰路线？');seq=a.pending(s)[0]['seq']
            self.assertTrue(a.explain_weak_candidates(s,seq))
            before=a.flow.db.execute('select fingerprint from dl_lex_events where scope=?',(s,)).fetchone()[0]
            a.flow.db.execute("update messages set recorded_at='2026-01-02T00:00:00+00:00' where scope=? and id=?",(s,mid));a.flow.db.commit()
            original=a.lexical_index.tokenize
            def fail(_):raise RuntimeError('test interrupted rebuild')
            a.lexical_index.tokenize=fail
            with self.assertRaises(RuntimeError):a.explain_weak_candidates(s,seq)
            self.assertEqual(a.flow.db.execute('select fingerprint from dl_lex_events where scope=?',(s,)).fetchone()[0],before)
            self.assertEqual(a.flow.db.execute('select count(*) from dl_lex_dirty where scope=?',(s,)).fetchone()[0],1)
            a.lexical_index.tokenize=original
            self.assertTrue(a.explain_weak_candidates(s,seq))
            with a.flow.db:
                a.flow.db.execute('delete from dl_lex_terms where scope=?',(s,))
                a.flow.db.execute('delete from dl_lex_events where scope=?',(s,))
                a.flow.db.execute('delete from dl_lex_dirty where scope=?',(s,))
            a.close();b=Store(d,'',FakeEncoder)
            self.assertTrue(b.explain_weak_candidates(s,seq));b.close()

    def test_index_retains_older_sources_within_long_event(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            first=a.save(s,'户外安排','当年聊起四姑娘山大峰')
            for i in range(11):a.save(s,'户外安排','其他历史'+str(i))
            a.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00' where scope=?",(s,));a.flow.db.commit()
            a.capture(s,'q','四姑娘山大峰路线？')
            result=a.explain_weak_candidates(s,a.pending(s)[0]['seq'])
            self.assertEqual(result[0]['title'],'户外安排')
            self.assertTrue(any(x['source_id']==first for x in result[0]['clues']))
            a.close()
    def test_rejected_id_excludes_only_association_not_explicit_reference(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            mid=a.save(s,'登山安排','考虑四姑娘山大峰')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T00:00:00+00:00',s,mid));a.flow.db.commit()
            a.weak_association_enabled=True;a.association_context_enabled=True
            eid=a.flow._events(s)[0]['id']
            self.assertIn('关联历史',a.context_packet(s,'四姑娘山路线怎样？'))
            self.assertEqual(a.context_packet(s,'四姑娘山路线怎样？',exclude_ids={eid}),'')
            self.assertIn('考虑四姑娘山大峰',a.context_packet(s,'登山安排',exclude_ids={eid}))
            a.close()
    def test_unconfirmed_association_context_is_optin_and_focused(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'一月登山','之前打算去四姑娘山大峰')
            new=a.save(s,'一月登山','其他较新的背景'+'字'*130)
            for i,mid in enumerate((old,new)):
                a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',(f'2026-01-0{i+1}T00:00:00+00:00',s,mid))
            a.flow.db.commit();a.weak_association_enabled=True
            labels=a.context_packet(s,'四姑娘山大峰路线怎样？',budget=450)
            self.assertIn('未确认的联想候选',labels);self.assertNotIn('之前打算',labels)
            a.association_context_enabled=True
            packet=json.loads(a.context_packet(s,'四姑娘山大峰路线怎样？',budget=450))
            self.assertIn('未确认的联想事件',packet)
            self.assertIn('尚未确认',packet['说明'])
            self.assertTrue(any('四姑娘山大峰' in x['内容'] for x in packet['关联历史']['原话']))
            self.assertEqual(a.context_packet(other,'四姑娘山大峰路线怎样？'),'')
            another=a.save(s,'其他登山','我也考虑四姑娘山大峰')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T00:00:00+00:00',s,another));a.flow.db.commit()
            ambiguous=json.loads(a.context_packet(s,'四姑娘山大峰路线怎样？'))
            self.assertIn('未确认的联想候选',ambiguous);self.assertNotIn('关联历史',ambiguous)
            a.close()

    def test_split_clues_require_same_source_and_preserve_ambiguity(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            first=a.save(s,'读书安排','原计划周六去书店')
            second=a.save(s,'读书安排','改到周日去书店')
            for mid,time in ((first,'2025-01-01T00:00:00+00:00'),(second,'2025-01-02T00:00:00+00:00')):
                a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE id=?',(time,mid))
            a.flow.db.commit();a.relate_sources(s,second,first,'corrects')
            a.weak_association_enabled=True;a.association_context_enabled=True
            result=json.loads(a.context_packet(s,'周六书店安排怎么样',budget=800))
            self.assertIn('未确认的联想事件',result)
            self.assertEqual(len(result['关联历史']['原话']),2)
            self.assertEqual(result['关联历史']['关系'][0]['类型'],'修正')
            self.assertNotIn('关联历史',a.context_packet(s,'不是周六书店安排',budget=800))
            self.assertEqual(a.context_packet(other,'周六书店安排怎么样',budget=800),'')
            one=a.save(other,'休闲事件','原计划周六出门')
            two=a.save(other,'休闲事件','我去过书店')
            for mid in (one,two):
                a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00' WHERE id=?",(mid,))
            a.flow.db.commit()
            separate=a.context_packet(other,'周六书店安排怎么样',budget=800)
            self.assertIn('未确认的联想候选',separate);self.assertNotIn('关联历史',separate)
            third=a.save(s,'另一个事件','周六书店碰面')
            a.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00' WHERE id=?",(third,));a.flow.db.commit()
            ambiguous=a.context_packet(s,'周六书店安排怎么样',budget=800)
            self.assertIn('未确认的联想候选',ambiguous);self.assertNotIn('关联历史',ambiguous)
            a.close()

    def test_association_context_keeps_correction_group_whole(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            old=a.save(s,'登山安排','四姑娘山大峰原计划周六')
            new=a.save(s,'登山安排','改为周日出发')
            for i,mid in enumerate((old,new)):
                a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',(f'2026-01-0{i+1}T00:00:00+00:00',s,mid))
            a.flow.db.commit();a.relate_sources(s,new,old,'corrects')
            a.weak_association_enabled=True;a.association_context_enabled=True
            result=json.loads(a.context_packet(s,'四姑娘山大峰去的时间？',budget=500))
            self.assertEqual(len(result['关联历史']['原话']),2)
            self.assertEqual(result['关联历史']['关系'][0]['类型'],'修正')
            small=a.context_packet(s,'四姑娘山大峰去的时间？',budget=150)
            self.assertNotIn('关联历史',small)
            self.assertNotIn('周六',small);self.assertNotIn('周日',small)
            a.close()
    def test_extracted_clues_point_to_older_reviewed_sources(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            mid=a.save(s,'一月登山','此前提到四姑娘山大峰')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T00:00:00+00:00',s,mid));a.flow.db.commit()
            a.save(other,'私密事件','四姑娘山大峰')
            a.capture(s,'q','四姑娘山大峰路线怎样？');seq=a.pending(s)[0]['seq']
            items=a.explain_weak_candidates(s,seq)
            self.assertEqual(len(items),1);self.assertEqual(items[0]['title'],'一月登山')
            clue=items[0]['clues'][0]
            self.assertIn(clue['text'],'四姑娘山大峰路线怎样？')
            self.assertIn(clue['text'],'此前提到四姑娘山大峰');self.assertEqual(clue['source_id'],mid)
            self.assertEqual(len(a.pending(s)),1)
            with self.assertRaises(ValueError):a.explain_weak_candidates(other,seq)
            a.flow.db.execute('update messages set content=? where scope=? and id=?',('来源已损坏',s,mid));a.flow.db.commit()
            self.assertEqual(a.explain_weak_candidates(s,seq),[])
            a.close()
    def test_event_alias_recall_ambiguity_and_focus_invalidation(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'一月登山','计划去大峰')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T00:00:00+00:00',s,old));a.flow.db.commit()
            self.assertTrue(a.event_alias(s,'一月登山','四姑娘山'))
            self.assertFalse(a.event_alias(s,'一月登山','四姑娘山'))
            packet,focus=a.context_turn(s,'四姑娘山怎么样？')
            self.assertIn('计划去大峰',packet);self.assertIsNotNone(focus)
            self.assertEqual(a.context_packet(other,'四姑娘山怎么样？'),'')
            a.capture(s,'m','四姑娘山改到周日')
            self.assertEqual(a.propose(s,a.pending(s)[0]['seq'])['candidates'][0]['method'],'人工别名字面匹配')
            self.assertIn('待审提醒',a.context_packet(s,'四姑娘山'))
            a.save(s,'四姑娘山','完全不同的事件')
            self.assertEqual(a.context_packet(s,'四姑娘山'),'')
            self.assertTrue(a.event_alias(s,'一月登山','四姑娘山',remove=True))
            self.assertEqual(a.context_turn(s,'后来呢？',focus),('',None))
            with self.assertRaises(ValueError):a.event_alias(s,'一月登山','四姑娘山')
            a.close()
    def test_weak_context_only_sends_unconfirmed_titles_and_is_scoped(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'一月登山','以前提到四姑娘山大峰')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T00:00:00+00:00',s,old));a.flow.db.commit()
            a.weak_association_enabled=True
            packet=json.loads(a.context_packet(s,'四姑娘山路线怎么样？'))
            self.assertEqual(packet['未确认的联想候选'],['一月登山'])
            self.assertEqual(packet['匹配方式'],'本地词组弱匹配')
            self.assertNotIn('以前提到',json.dumps(packet,ensure_ascii=False))
            self.assertEqual(a.context_packet(other,'四姑娘山路线怎么样？'),'')
            future=a.save(s,'一月登山','又提到四姑娘山')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2099-01-01T00:00:00+00:00',s,future));a.flow.db.commit()
            self.assertEqual(a.context_packet(s,'四姑娘山路线怎么样？'),'')
            a.close()
    def test_weak_association_is_optin_scoped_historical_and_unconfirmed(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'一月登山','此前说过四姑娘山准备')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T00:00:00+00:00',s,old));a.flow.db.commit()
            a.save(other,'其他用户','四姑娘山准备')
            a.capture(s,'u1','四姑娘山路线怎么样？');seq=a.pending(s)[0]['seq']
            self.assertEqual(a.propose(s,seq)['candidates'],[])
            found=a.propose(s,seq,weak=True)
            self.assertEqual([(x['title'],x['method']) for x in found['candidates']],[('一月登山','原文词组弱匹配')])
            self.assertEqual(len(a.pending(s)),1);self.assertEqual(len(a.show(s,'一月登山')),1)
            newer=a.save(s,'一月登山','四姑娘山后来有变化')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2099-01-01T00:00:00+00:00',s,newer));a.flow.db.commit()
            self.assertEqual(a.propose(s,seq,weak=True)['candidates'],[])
            a.weak_association_enabled=True
            a.capture(s,'u2','四姑娘山路线准备');self.assertEqual(len(a.pending(s)),2)
            a.close()
    def test_occurrence_annotations_preserve_source_and_history(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            mid=a.save(s,'出行计划','打算去爬山')
            original=dict(a.flow.db.execute('select * from messages where scope=? and id=?',(s,mid)).fetchone())
            self.assertTrue(a.annotate_date(s,mid,'2027-01-03'))
            self.assertFalse(a.annotate_date(s,mid,'2027-01-03'))
            self.assertEqual(a.timeline(s,'发生')['records'][0]['occurred_at'],'2027-01-03')
            self.assertTrue(a.annotate_date(s,mid,'2027-01-06'))
            self.assertEqual([x['occurred_date'] for x in a.date_history(s,mid)],['2027-01-06','2027-01-03'])
            self.assertTrue(a.annotate_date(s,mid,'未知'))
            self.assertEqual(a.timeline(s,'发生')['records'],[])
            self.assertEqual(original,dict(a.flow.db.execute('select * from messages where scope=? and id=?',(s,mid)).fetchone()))
            with self.assertRaises(ValueError):a.annotate_date(other,mid,'2027-01-03')
            with self.assertRaises(ValueError):a.annotate_date(s,mid,'2027-02-29')
            a.close();b=Store(d,'',FakeEncoder)
            self.assertEqual([x['occurred_date'] for x in b.date_history(s,mid)],[None,'2027-01-06','2027-01-03'])
            b.close()

    def test_annotation_does_not_follow_changed_source(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');mid=a.save(s,'计划','原文')
            a.annotate_date(s,mid,'2027-01-03')
            content='后来的文本';digest=hashlib.sha256(content.encode()).hexdigest()
            a.flow.db.execute('update messages set content=? where scope=? and id=?',(content,s,mid))
            a.flow.db.execute('update e417_ingest set source_sha256=? where scope=? and message_id=?',(digest,s,mid));a.flow.db.commit()
            self.assertFalse(a.date_history(s,mid)[0]['source_matches'])
            self.assertEqual(a.timeline(s,'发生')['records'],[])
            self.assertTrue(a.annotate_date(s,mid,'2027-01-03'))
            self.assertEqual(len(a.timeline(s,'发生')['records']),1)
            a.close()
    def test_timeline_pages_scope_integrity_and_event_catalog(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            mids=[a.save(s,'计划'+str(i%2),'记录'+str(i)) for i in range(12)]
            a.save(other,'他人事件','他人记录')
            pages=[];cursor=''
            while True:
                p=a.timeline(s,'记录',cursor,limit=5);pages.extend(p['records']);cursor=p['next_cursor']
                if not cursor:break
            self.assertEqual(len(pages),12);self.assertEqual(len({x['id'] for x in pages}),12)
            self.assertFalse(any(x['title']=='他人事件' for x in pages))
            self.assertEqual(a.timeline(s,'发生')['records'],[])
            self.assertEqual(len(a.event_catalog(s)['events']),2)
            a.flow.db.execute('update messages set content=? where scope=? and id=?',('已篡改',s,mids[0]));a.flow.db.commit()
            all_records=a.timeline(s,'记录',limit=10)['records']+a.timeline(s,'记录',a.timeline(s,'记录',limit=10)['next_cursor'])['records']
            damaged=next(x for x in all_records if x['id']==mids[0])
            self.assertFalse(damaged['integrity']);self.assertIsNone(damaged['content'])
            self.assertTrue(any(x['stale'] for x in a.event_catalog(s)['events']))
            with self.assertRaises(ValueError):a.timeline(s,'发生','bad')
            a.close()
    def test_followup_update_clue_is_scoped_and_breaks_on_intervening_message(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'书店计划','周六去书店')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T00:00:00+00:00',s,old));a.flow.db.commit()
            a.capture(s,'a','之前说的书店计划')
            a.capture(other,'x','改到周二')
            a.capture(s,'b','改到周日，秘密')
            rows=a.update_clues(s);self.assertEqual(rows[1]['matches'][0]['method'],'前一条采集记录的标题')
            packet=json.loads(a.context_packet(s,'书店计划'))
            self.assertIn('待审提醒',packet);self.assertNotIn('秘密',json.dumps(packet,ensure_ascii=False))
            self.assertFalse(a.update_clues(other)[0]['matches'])
            a.capture(s,'c','说说天气');a.capture(s,'d','改到周一')
            self.assertFalse(a.update_clues(s)[3]['matches'])
            self.assertEqual(len(a.pending(s)),4);self.assertEqual(len(a.show(s,'书店计划')),1)
            a.close()

    def test_followup_clue_expires_and_ignores_ambiguous_anchor(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            for title in ('书店计划','出行计划'):
                mid=a.save(s,title,'旧记录')
                a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T00:00:00+00:00',s,mid))
                a.flow.db.commit()
            a.flow.db.commit()
            a.capture(s,'a','书店计划和出行计划');a.capture(s,'b','改到周日')
            self.assertFalse(a.update_clues(s)[1]['matches'])
            a.capture(s,'c','书店计划');a.capture(s,'d','改到周一')
            seq=a.pending(s)[-2]['seq']
            a.flow.db.execute("update dl_capture set recorded_at='2026-01-01T00:00:00+00:00' where scope=? and seq=?",(s,seq));a.flow.db.commit()
            self.assertFalse(a.update_clues(s)[3]['matches'])
            a.close()
    def test_update_clues_warn_without_promoting_or_leaking_pending_text(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'书店计划','原来周六去书店')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T00:00:00+00:00',s,old));a.flow.db.commit()
            a.capture(s,'x','书店计划改到周日，私密详情')
            a.capture(other,'y','书店计划取消')
            clues=a.update_clues(s)
            self.assertEqual(len(clues),1);self.assertEqual(clues[0]['matches'][0]['old_id'],old)
            packet=json.loads(a.context_packet(s,'书店计划是什么？'))
            self.assertIn('尚未审核',packet['待审提醒'])
            self.assertNotIn('私密详情',json.dumps(packet,ensure_ascii=False))
            self.assertEqual(len(a.pending(s)),1);self.assertEqual(len(a.show(s,'书店计划')),1)
            a.link_capture(s,clues[0]['seq'],'书店计划')
            packet=json.loads(a.context_packet(s,'书店计划是什么？'))
            self.assertNotIn('待审提醒',packet)
            self.assertEqual(a.update_clues(s),[])
            a.close()
    def test_budget_selection_keeps_relation_group_and_time_priority(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            ids=[a.save(s,'书店计划',x) for x in ('旧的准备'+'甲'*70,'原定周六'+'乙'*30,'后来周日'+'丙'*30)]
            for i,mid in enumerate(ids):
                a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',
                    (f'2026-01-0{i+1}T00:00:00+00:00',s,mid))
            a.flow.db.commit();a.relate_sources(s,ids[2],ids[1],'corrects')
            latest=json.loads(a.context_packet(s,'书店计划现在怎样',budget=310))
            self.assertEqual(len(latest['原话']),2)
            self.assertIn('周六',latest['原话'][0]['内容']);self.assertIn('周日',latest['原话'][1]['内容'])
            self.assertEqual(latest['关系'][0]['类型'],'修正');self.assertIn('2/3',latest['覆盖'])
            earliest=json.loads(a.context_packet(s,'书店计划最初怎样',budget=310))
            self.assertIn('旧的准备',earliest['原话'][0]['内容'])
            self.assertEqual(any('周六' in r['内容'] for r in earliest['原话']),
                             any('周日' in r['内容'] for r in earliest['原话']))
            self.assertLessEqual(len(a.context_packet(s,'书店计划',budget=310)),310)
            self.assertEqual(a.context_packet(s,'书店计划现在怎样',budget=190),'')
            a.close()
    def test_source_brief_fallback_preserves_original_and_invalidates(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');other=a.scope('2','s')
            raw='计划去四姑娘山大峰，准备事项：'+'装备'*170
            mid=a.save(scope,'登山计划',raw)
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00' WHERE scope=?",(scope,));a.flow.db.commit()
            a.save_source_brief(scope,'登山计划',mid,'四姑娘山大峰','我提过登山计划，具体装备仍需查原话。')
            self.assertEqual(a.context_packet(scope,'登山计划是什么',budget=300),'')
            a.reviewed_brief_context_enabled=True
            packet=json.loads(a.context_packet(scope,'登山计划是什么',budget=300))
            self.assertIn('人工摘要',packet);self.assertEqual(packet['依据摘录'],'四姑娘山大峰')
            self.assertIn('可能遗漏或误解',packet['说明'])
            self.assertEqual(a.context_packet(scope,'登山计划最初是什么',budget=300),'')
            self.assertEqual(a.context_packet(scope,'登山计划装备具体型号是什么',budget=300),'')
            self.assertEqual(a.context_packet(scope,'登山计划是什么',budget=300,as_of=datetime(2026,1,2,tzinfo=timezone.utc)),'')
            self.assertEqual(a.context_packet(other,'登山计划是什么',budget=300),'')
            with self.assertRaises(ValueError):a.save_source_brief(scope,'登山计划',mid,'根本没说过','错误摘要')
            self.assertEqual(a.show(scope,'登山计划')[0]['content'],raw)
            a.annotate_date(scope,mid,'2027-01-03')
            self.assertFalse(a.read_source_brief(scope,'登山计划')['current'])
            self.assertEqual(a.context_packet(scope,'登山计划是什么',budget=300),'')
            a.save_source_brief(scope,'登山计划',mid,'四姑娘山大峰','我提过登山计划，具体装备仍需查原话。')
            a.capture(scope,'change','登山计划改到周日')
            self.assertEqual(a.context_packet(scope,'登山计划是什么',budget=300),'')
            a.link_capture(scope,1,'登山计划')
            self.assertFalse(a.read_source_brief(scope,'登山计划')['current'])
            self.assertNotIn('人工摘要',a.context_packet(scope,'登山计划是什么',budget=300))
            a.close()

    def test_context_selects_manual_occurrence_date_without_inventing_time(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');other=a.scope('2','s')
            old=a.save(scope,'出行计划','一月带蓝色背包')
            new=a.save(scope,'出行计划','二月计划吃面')
            for i,mid in enumerate((old,new),1):
                a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE scope=? AND id=?',(f'2026-01-0{i}T00:00:00+00:00',scope,mid))
            a.flow.db.commit();a.annotate_date(scope,old,'2026-01-05');a.annotate_date(scope,new,'2026-02-10')
            a.flow.db.execute("UPDATE dl_date_annotations SET annotated_at='2026-02-01T00:00:00+00:00'");a.flow.db.commit()
            now=datetime(2026,3,1,tzinfo=timezone.utc);q='出行计划2026-01-05发生了什么'
            a.occurred_date_context_enabled=True
            packet=json.loads(a.context_packet(scope,q,budget=600,as_of=now))
            self.assertEqual([x['内容'] for x in packet['原话']],['一月带蓝色背包'])
            self.assertEqual(packet['日期标注']['条目'],[[1,'2026-01-05']])
            chinese=json.loads(a.context_packet(scope,'出行计划2026年1月5日发生了什么',budget=600,as_of=now))
            self.assertEqual(chinese['原话'][0]['内容'],'一月带蓝色背包')
            self.assertEqual(a.context_packet(scope,'出行计划2026-01-06怎么样',as_of=now),'')
            self.assertEqual(a.context_packet(scope,'出行计划2026-02-30怎么样',as_of=now),'')
            self.assertEqual(a.context_packet(scope,'出行计划2026-01-05到2026-02-10',as_of=now),'')
            self.assertEqual(a.context_packet(scope,'出行计划2026-01-05到2026年2月',as_of=now),'')
            self.assertEqual(a.context_packet(scope,q,as_of=datetime(2026,1,30,tzinfo=timezone.utc)),'')
            self.assertEqual(a.context_packet(other,q,as_of=now),'')
            a.annotate_date(scope,old,'未知')
            a.flow.db.execute("UPDATE dl_date_annotations SET annotated_at='2026-02-02T00:00:00+00:00' WHERE occurred_date IS NULL");a.flow.db.commit()
            self.assertEqual(a.context_packet(scope,q,as_of=now),'')
            a.close()

    def test_recency_boost_is_scoped_bounded_and_respects_history_queries(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);scope=a.scope('1','s');other=a.scope('2','s')
            for title in ('事件甲','事件乙'):a.save(scope,title,'计划去四姑娘山登山路线')
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'");a.flow.db.commit()
            events=a.flow._events(scope)
            row={'content':'四姑娘山登山路线','recorded_at':'2026-03-01T00:00:00+00:00'}
            baseline=a._weak_candidate_details(scope,row,events)
            old,new=baseline[0]['event_id'],baseline[1]['event_id']
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-02-28T00:00:00+00:00' WHERE scope=? AND id IN (SELECT message_id FROM e417_phase WHERE scope=? AND event_id=?)",(scope,scope,new));a.flow.db.commit()
            self.assertEqual(a._weak_candidate_details(scope,row,events)[0]['event_id'],old)
            a.recency_rank_enabled=True;a.weak_association_enabled=True;a.association_context_enabled=True
            recent=a._weak_candidate_details(scope,row,events)
            self.assertEqual(recent[0]['event_id'],new)
            self.assertTrue(0<recent[0]['recency_boost']<=0.25)
            self.assertEqual(recent[1]['recency_boost'],0.0)
            history=a._weak_candidate_details(scope,{**row,'content':'以前四姑娘山登山路线'},events)
            self.assertEqual(history[0]['event_id'],old);self.assertTrue(all(x['recency_boost']==0 for x in history))
            packet=json.loads(a.context_packet(scope,row['content'],as_of=datetime(2026,3,1,tzinfo=timezone.utc)))
            self.assertEqual(len(packet['未确认的联想候选']),2);self.assertNotIn('关联历史',packet)
            self.assertIn('近期记录',packet['排序提示'])
            self.assertEqual(a.context_packet(other,row['content'],as_of=datetime(2026,3,1,tzinfo=timezone.utc)),'')
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-03-02T00:00:00+00:00' WHERE scope=? AND id IN (SELECT message_id FROM e417_phase WHERE scope=? AND event_id=?)",(scope,scope,new));a.flow.db.commit()
            self.assertEqual([x['event_id'] for x in a._weak_candidate_details(scope,row,events)],[old])
            a.close()

    def test_manual_priority_only_reranks_valid_weak_candidates(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            for title in ('旅行A','旅行B'):
                a.save(s,title,'以前计划去四姑娘山登山路线')
            a.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'");a.flow.db.commit()
            row={'content':'四姑娘山登山路线','recorded_at':'2099-01-01T00:00:00+00:00'}
            events=a.flow._events(s);base=a._weak_candidate_details(s,row,events)
            self.assertEqual(len(base),2);lower=base[1]['title']
            a.event_priority(s,lower,'high')
            self.assertEqual([x['event_id'] for x in a._weak_candidate_details(s,row,events)],[x['event_id'] for x in base])
            a.event_priority_enabled=True;a.weak_association_enabled=True;a.association_context_enabled=True
            ranked=a._weak_candidate_details(s,row,events)
            self.assertEqual(ranked[0]['title'],lower);self.assertEqual(ranked[0]['priority_boost'],0.25)
            self.assertEqual(set(x['event_id'] for x in base),set(x['event_id'] for x in ranked))
            packet=json.loads(a.context_packet(s,'四姑娘山登山路线怎么样'))
            self.assertIn('排序提示',packet);self.assertNotIn('关联历史',packet)
            with self.assertRaises(ValueError):a.event_priority(other,lower,'high')
            self.assertEqual(a._weak_candidate_details(s,{'content':'完全不相干','recorded_at':row['recorded_at']},events),[])
            a.close();a=Store(d,'',FakeEncoder)
            self.assertEqual(a.event_priority(s,lower),'high')
            a.event_priority(s,lower,'normal');self.assertEqual(a.event_priority(s,lower),'normal')
            a.close()

    def test_compact_context_preserves_whole_sources_and_relation(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old_text='原计划周六去书店'+'甲'*45
            new_text='后来改到周日去书店'+'乙'*45
            old=a.save(s,'书店计划',old_text);new=a.save(s,'书店计划',new_text)
            for i,mid in enumerate((old,new),1):
                a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE scope=? AND id=?',(f'2026-01-0{i}T00:00:00+00:00',s,mid))
            a.flow.db.commit();a.relate_sources(s,new,old,'corrects')
            case=None
            for budget in range(170,500):
                a.compact_context_enabled=False;normal=a.context_packet(s,'书店计划',budget=budget)
                a.compact_context_enabled=True;compact=a.context_packet(s,'书店计划',budget=budget)
                if not normal and compact:
                    case=(budget,json.loads(compact),compact);break
            self.assertIsNotNone(case)
            budget,data,output=case
            self.assertLessEqual(len(output),budget)
            self.assertEqual([x[1] for x in data['原话']],[old_text,new_text])
            self.assertEqual(data['关系'],[[2,'修正',1]])
            self.assertEqual(a.context_packet(other,'书店计划',budget=budget),'')
            self.assertEqual(a.context_packet(s,'书店计划',budget=100),'')
            a.close()

    def test_followup_detail_requires_source_overlap_and_respects_topic_change(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.save(s,'登山计划','准备了蓝色登山背包')
            a.save(s,'书店计划','计划周末去书店')
            text,focus=a.context_turn(s,'登山计划怎么样？')
            self.assertIsNotNone(focus)
            text,next_focus=a.context_turn(s,'那个背包是什么颜色？',focus)
            self.assertIn('指代未确认',text);self.assertIn('蓝色登山背包',text)
            self.assertEqual(a.context_turn(other,'那个背包呢？',focus),('',None))
            result,next2=a.context_turn(s,'那个火箭多少钱？',focus)
            self.assertNotIn('蓝色登山背包',result);self.assertIsNone(next2)
            changed,next2=a.context_turn(s,'书店计划怎么样？',focus)
            self.assertIn('周末去书店',changed);self.assertNotIn('指代未确认',changed)
            denied,next2=a.context_turn(s,'那个背包不是蓝色？',focus)
            self.assertNotIn('指代未确认',denied);self.assertIsNone(next2)
            a.save(s,'登山计划','更新了其他细节')
            self.assertEqual(a.context_turn(s,'那个背包呢？',focus),('',None))
            a.close()

    def test_followup_is_bounded_scoped_and_invalidated(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.save(s,'书店计划','周六去书店')
            text,focus=a.context_turn(s,'书店计划是什么？')
            self.assertIn('周六去书店',text);self.assertIsNotNone(focus)
            text,next_focus=a.context_turn(s,'后来呢？',focus)
            self.assertIn('指代未确认',text);self.assertIn('周六去书店',text)
            self.assertEqual(a.context_turn(other,'后来呢？',focus),('',None))
            _,last=a.context_turn(s,'最后呢？',next_focus)
            self.assertEqual(a.context_turn(s,'后来呢？',last),('',None))
            a.save(s,'书店计划','后来改成周日')
            self.assertEqual(a.context_turn(s,'后来呢？',focus),('',None))
            a.close()
    def test_confirm_contrast_resume_and_stale_guard(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'书店','原计划周六去书店')
            a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE scope=? AND id=?',('2026-01-01T00:00:00+00:00',s,old));a.flow.db.commit()
            a.capture(s,'m1','改成周日去')
            seq=a.pending(s)[0]['seq'];snap=a.contrast_snapshot(s,seq,'书店')
            draft=a.save_contrast_draft(s,snap,json.dumps({'relation':'correction','explanation':'日期有变化',
                'old_id':old,'old_quote':'周六','new_quote':'周日'},ensure_ascii=False))
            did=draft['draft_id']
            with self.assertRaises(ValueError):a.confirm_contrast(other,did,'corrects')
            relate=a.relate_sources
            def fail(*args):raise RuntimeError('interrupted')
            a.relate_sources=fail
            with self.assertRaises(RuntimeError):a.confirm_contrast(s,did,'corrects')
            self.assertEqual(len(a.pending(s)),1)
            a.relate_sources=relate
            result=a.confirm_contrast(s,did,'corrects')
            self.assertEqual(result['older_id'],old)
            a.confirm_contrast(s,did,'corrects')
            self.assertEqual(a.flow.db.execute('select count(*) from relations').fetchone()[0],1)
            with self.assertRaises(ValueError):a.confirm_contrast(s,did,'supersedes')
            self.assertEqual(len(a.show(s,'书店')),2)
            a.capture(s,'m2','后来不去了');seq2=a.pending(s)[0]['seq']
            snap2=a.contrast_snapshot(s,seq2,'书店')
            old2=next(r for r in snap2['history'] if '周六' in r['content'])
            draft2=a.save_contrast_draft(s,snap2,json.dumps({'relation':'update','explanation':'有变化',
                'old_id':old2['id'],'old_quote':'周六','new_quote':'不去'},ensure_ascii=False))
            a.save(s,'书店','额外记录')
            with self.assertRaises(ValueError):a.confirm_contrast(s,draft2['draft_id'],'related')
            self.assertEqual(len(a.pending(s)),1)
            a.close()
    def test_multiturn_candidates_respect_scope_time_and_review(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'fake',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'周末书店','原计划去书店找资料')
            a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE scope=? AND id=?',('2026-01-01T00:00:00+00:00',s,old));a.flow.db.commit()
            a.capture(s,'a','之前计划是什么？');a.capture(s,'b','就是周末书店那次')
            seqs=[x['seq'] for x in a.pending(s)]
            future=a.save(s,'新的书店','以后再谈')
            a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE scope=? AND id=?',('2099-01-01T00:00:00+00:00',s,future));a.flow.db.commit()
            result=a.multi_suggest(s,seqs,semantic=True)
            self.assertEqual([x['title'] for x in result['candidates']],['周末书店'])
            self.assertFalse(result['semantic_error'])
            self.assertEqual(len(a.pending(s)),2)
            self.assertEqual(a.flow.db.execute('select count(*) from messages where scope=?',(s,)).fetchone()[0],2)
            with self.assertRaises(ValueError):a.multi_suggest(other,seqs)
            with self.assertRaises(ValueError):a.multi_suggest(s,list(reversed(seqs)))
            a.link_capture(s,seqs[0],'周末书店')
            with self.assertRaises(ValueError):a.multi_suggest(s,seqs)
            a.close()
    def test_contrast_is_source_grounded_and_non_mutating(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'书店','原计划周六去书店')
            a.flow.db.execute('UPDATE messages SET recorded_at=? WHERE scope=? AND id=?',('2026-01-01T00:00:00+00:00',s,old));a.flow.db.commit()
            a.capture(s,'m1','计划改成周日去')
            seq=a.pending(s)[0]['seq'];snap=a.contrast_snapshot(s,seq,'书店')
            result={'relation':'correction','explanation':'日期有变化','old_id':old,'old_quote':'周六','new_quote':'周日'}
            draft=a.save_contrast_draft(s,snap,json.dumps(result,ensure_ascii=False))
            self.assertEqual(draft['status'],'UNREVIEWED')
            self.assertEqual(a.flow.db.execute('select count(*) from relations').fetchone()[0],0)
            self.assertEqual(len(a.pending(s)),1)
            bad=dict(result,new_quote='不存在')
            with self.assertRaises(ValueError):a.save_contrast_draft(s,snap,json.dumps(bad,ensure_ascii=False))
            with self.assertRaises(ValueError):a.read_contrast_draft(other,draft['draft_id'])
            a.save(s,'书店','相关的另一条记录')
            self.assertEqual(a.read_contrast_draft(s,draft['draft_id'])['status'],'STALE')
            a.close()
    def test_model_draft_requires_real_substrings_and_stales_after_link(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.capture(s,'1','周末打算去书店找资料')
            seq=a.pending(s)[0]['seq'];src=a.model_sources(s,[seq])
            raw=json.dumps({'title':'周末书店','summary':'计划去书店','evidence':[{'seq':seq,'quote':'去书店'}]},ensure_ascii=False)
            draft=a.save_model_draft(s,src,raw)
            self.assertEqual(draft['status'],'UNREVIEWED')
            self.assertFalse(a.show(s,'周末书店'))
            with self.assertRaises(ValueError):a.read_model_draft(other,draft['draft_id'])
            bad=json.dumps({'title':'X','summary':'X','evidence':[{'seq':seq,'quote':'没有出现'}]},ensure_ascii=False)
            with self.assertRaises(ValueError):a.save_model_draft(s,src,bad)
            a.link_capture(s,seq,'周末书店')
            self.assertEqual(a.read_model_draft(s,draft['draft_id'])['status'],'STALE')
            with self.assertRaises(ValueError):a.save_model_draft(s,src,raw)
            a.close()
    def test_natural_sentence_proposal_and_extract_staleness(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.capture(s,'1','周末打算去书店找资料')
            seq=a.pending(s)[0]['seq'];p=a.propose(s,seq)
            self.assertEqual(p['new_event_candidate']['title'],'周末打算去书店找资料')
            self.assertEqual(p['new_event_candidate']['status'],'UNCONFIRMED')
            self.assertFalse(a.show(s,'周末打算去书店找资料'))
            a.link_capture(s,seq,'周末书店')
            draft=a.create_extract_draft(s,'周末书店')
            self.assertEqual(draft['status'],'CURRENT')
            self.assertEqual(draft['records'][0]['content'],'周末打算去书店找资料')
            with self.assertRaises(ValueError):a.read_extract_draft(other,draft['draft_id'])
            a.save(s,'周末书店','后来准备周日再去')
            self.assertEqual(a.read_extract_draft(s,draft['draft_id'])['status'],'STALE')
            a.close();b=Store(d,'',FakeEncoder)
            self.assertEqual(b.read_extract_draft(s,draft['draft_id'])['status'],'STALE')
            b.close()
    def test_question_is_not_named(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');a.capture(s,'q','你准备去书店吗？')
            self.assertIsNone(a.propose(s,a.pending(s)[0]['seq'])['new_event_candidate']);a.close()
    def test_extract_marks_partial_and_relation_change(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            old=a.save(s,'行程','记录0')
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T10:00:00+00:00',s,old));a.flow.db.commit()
            for i in range(1,6):new=a.save(s,'行程','记录'+str(i))
            draft=a.create_extract_draft(s,'行程')
            self.assertTrue(draft['partial']);self.assertEqual(draft['source_total'],6)
            self.assertEqual(len(draft['records']),5)
            a.relate_sources(s,new,old,'corrects')
            self.assertEqual(a.read_extract_draft(s,draft['draft_id'])['status'],'STALE')
            a.close()
    def test_pending_cursor_pages_do_not_hide_old_messages(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            for i in range(12):a.capture(s,str(i),'消息'+str(i))
            first=a.pending(s);second=a.pending(s,after=first[-1]['seq'])
            self.assertEqual(len(first),10);self.assertEqual(len(second),2)
            self.assertEqual(len({x['seq'] for x in first+second}),12)
            a.close()
    def test_correction_preserves_sources_and_suppresses_unlabelled_context(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            old=a.save(s,'周末书店','原计划周六去书店')
            # Distinct chronological times simulate independent messages.
            a.flow.db.execute('update messages set recorded_at=? where scope=? and id=?',('2026-01-01T10:00:00+00:00',s,old));a.flow.db.commit()
            new=a.save(s,'周末书店','计划改成周日去书店')
            self.assertTrue(a.relate_sources(s,new,old,'corrects'))
            self.assertFalse(a.relate_sources(s,new,old,'corrects'))
            with self.assertRaises(ValueError):a.relate_sources(other,new,old,'corrects')
            rows=a.show(s,'周末书店')
            self.assertEqual([r['content'] for r in rows],['原计划周六去书店','计划改成周日去书店'])
            self.assertEqual(rows[0]['relations'][0]['kind'],'corrects')
            packet=a.context_packet(s,'周末书店计划是什么')
            self.assertIn('原计划周六去书店',packet)
            self.assertIn('计划改成周日去书店',packet)
            self.assertIn('修正',packet)
            self.assertEqual(a.context_packet(s,'周末书店计划是什么',budget=80),'')
            with self.assertRaises(Exception):a.relate_sources(s,old,new,'corrects')
            a.close();b=Store(d,'',FakeEncoder)
            self.assertEqual(len(b.show(s,'周末书店')),2)
            self.assertEqual(b.show(s,'周末书店')[1]['relations'][0]['older'],old)
            b.close()
    def test_batch_explicit_new_candidate_and_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            a.capture(s,'1','【事件：明天爬山】明天打算去登山')
            a.capture(s,'2','周末去哪儿还没想好')
            a.capture(other,'3','【事件：私密事件】测试')
            rows=a.organize_batch(s,limit=1)
            self.assertEqual(rows[0]['new_event_candidate']['title'],'明天爬山')
            self.assertEqual(rows[0]['status'],'UNCONFIRMED')
            self.assertFalse(a.show(s,'明天爬山'))
            rows=a.organize_batch(s,after=rows[0]['seq'])
            self.assertEqual(len(rows),1)
            self.assertIsNone(rows[0]['new_event_candidate'])
            self.assertEqual(len(a.pending(s)),2)
            seq=next(x['seq'] for x in a.pending(s) if x['content'].startswith('【事件：明天爬山】'));a.link_capture(s,seq,'明天爬山')
            self.assertEqual(a.show(s,'明天爬山')[0]['content'],'【事件：明天爬山】明天打算去登山')
            a.close()
    def test_suggestions_not_evidence_and_semantic_failure(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'synthetic',FakeEncoder);s=a.scope('1','session');other=a.scope('2','session')
            a.save(s,'周末书店','计划到书店找资料')
            a.capture(s,'m1','周末书店后来怎么样了')
            seq=a.pending(s)[0]['seq']
            suggestion=a.propose(s,seq,semantic=True)
            self.assertEqual(suggestion['candidates'][0]['title'],'周末书店')
            self.assertEqual(suggestion['status'],'UNCONFIRMED')
            self.assertEqual(len(a.show(s,'周末书店')),1)
            self.assertEqual(len(a.pending(s)),1)
            self.assertNotIn('周末书店后来怎么样了',a.context_packet(s,'周末书店'))
            with self.assertRaises(ValueError):a.propose(other,seq)
            self.assertEqual(a.flow.db.execute('select count(*) from dl_suggestions').fetchone()[0],1)
            a.link_capture(s,seq,'周末书店')
            with self.assertRaises(ValueError):a.propose(s,seq)
            a.close()
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','session');a.capture(s,'m','今天去哪儿')
            result=a.propose(s,a.pending(s)[0]['seq'],semantic=True)
            self.assertTrue(result['semantic_error']);self.assertEqual(result['candidates'],[])
            a.close()
    def test_persist_search_isolation_and_backup(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'synthetic',FakeEncoder)
            scope=a.scope('123','session1'); other=a.scope('123','session2')
            a.save(scope,'周末书店','周六想去书店找资料')
            self.assertEqual(a.search(scope,'书店'),['周末书店'])
            self.assertEqual(a.search(other,'书店'),[])
            backup=a.backup();a.close()
            b=Store(d,'synthetic',FakeEncoder)
            self.assertEqual(b.show(scope,'周末书店')[0]['content'],'周六想去书店找资料')
            self.assertEqual(b.show(other,'周末书店'),[])
            self.assertEqual(b.search(scope,'书店'),['周末书店'])
            b.close()
            with _conn(Path(d)/'backups'/backup) as conn:
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(conn.execute('select count(*) from messages').fetchone()[0],1)
    def test_model_required_no_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s');a.save(s,'书','去书店')
            with self.assertRaisesRegex(ValueError,'no_model_path'):a.search(s,'书')
            self.assertTrue(a.show(s,'书'));a.close()
    def test_capture_review_dedup_scope_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'synthetic',FakeEncoder);s=a.scope('1','s');other=a.scope('2','s')
            self.assertTrue(a.capture(s,'m1','周六去书店'))
            self.assertFalse(a.capture(s,'m1','周六去书店'))
            with self.assertRaises(ValueError):a.capture(s,'m1','另一条消息')
            row=a.pending(s)[0];self.assertFalse(a.pending(other))
            self.assertFalse(a.show(s,'书店'))
            with self.assertRaises(ValueError):a.link_capture(other,row['seq'],'书店')
            mid=a.link_capture(s,row['seq'],'书店')
            self.assertEqual(a.link_capture(s,row['seq'],'书店'),mid)
            self.assertEqual(a.show(s,'书店')[0]['content'],'周六去书店')
            a.close();b=Store(d,'synthetic',FakeEncoder)
            self.assertFalse(b.pending(s));self.assertEqual(b.show(s,'书店')[0]['content'],'周六去书店')
            b.capture(s,'m2',' 周日 ')
            b.link_capture(s,b.pending(s)[0]['seq'],'周日')
            self.assertEqual(b.show(s,'周日')[0]['content'],' 周日 ')
            self.assertFalse(b.pending(s));b.close()

    def test_delete_capture_and_scope_remove_plaintext(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d,'',FakeEncoder);s=a.scope('1','s')
            self.assertTrue(a.capture(s,'m1','alpha secret'))
            self.assertTrue(a.capture(s,'m2','beta secret'))
            self.assertTrue(a.delete_capture(s,1))
            self.assertEqual(a.flow.db.execute(
                'SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],1)
            self.assertNotIn(b'alpha secret', (Path(d)/'manual_test_memory.sqlite3').read_bytes())
            self.assertEqual(a.delete_scope(s),1)
            self.assertEqual(a.flow.db.execute(
                'SELECT COUNT(*) FROM dl_capture WHERE scope=?',(s,)).fetchone()[0],0)
            self.assertNotIn(b'beta secret', (Path(d)/'manual_test_memory.sqlite3').read_bytes())
            a.close()

if __name__=='__main__': unittest.main()
