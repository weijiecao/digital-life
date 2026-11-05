import asyncio
import importlib
import sys
import tempfile
import threading
import types
import unittest
from test_store import FakeEncoder

class Filter:
    class EventMessageType: PRIVATE_MESSAGE=1
    @staticmethod
    def command(*a,**kw):return lambda f:f
    @staticmethod
    def event_message_type(*a,**kw):return lambda f:f
    @staticmethod
    def on_llm_request(*a,**kw):return lambda f:f
class Star:
    def __init__(self,context):self.context=context
event=types.ModuleType('astrbot.api.event');event.filter=Filter;event.AstrMessageEvent=object
star=types.ModuleType('astrbot.api.star');star.Context=object;star.Star=Star
path=types.ModuleType('astrbot.core.utils.astrbot_path');path.get_astrbot_data_path=lambda:'.'
message=types.ModuleType('astrbot.core.agent.message')
class TextPart:
    def __init__(self,text):self.text=text;self.temporary=False
    def mark_as_temp(self):self.temporary=True;return self
message.TextPart=TextPart
sys.modules.update({'astrbot':types.ModuleType('astrbot'),'astrbot.api':types.ModuleType('astrbot.api'),
 'astrbot.api.event':event,'astrbot.api.star':star,'astrbot.core':types.ModuleType('astrbot.core'),
 'astrbot.core.utils':types.ModuleType('astrbot.core.utils'),'astrbot.core.utils.astrbot_path':path,
 'astrbot.core.agent':types.ModuleType('astrbot.core.agent'),'astrbot.core.agent.message':message})
m=importlib.import_module('astrbot_plugin_digital_memory_store.main')
orig=m.Store
m.Store=lambda root,model:orig(root,model,FakeEncoder)
class Event:
    def __init__(self,text,group=None):
        self.message_str=text;self.unified_msg_origin='test:FriendMessage:42'
        self.message_obj=types.SimpleNamespace(group_id=group,self_id='bot',sender=types.SimpleNamespace(user_id='42'))
    def plain_result(self,text):return text
class Plain:
    def __init__(self,text):self.text=text
async def run(p,text,group=None):return [v async for v in p.dlm(Event(text,group))]
class Smoke(unittest.TestCase):
    def test_long_private_message_is_captured_and_overlimit_marks_gap(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','automatic_memory_enabled':True,
                'model_dir':'fixture'})
            text='书店计划的完整背景'+('补充信息'*300)
            event=Event(text);event.message_obj.message_id='long';event.message_obj.message=[Plain(text)]
            await p.capture_private(event)
            scope=p.store.scope('42',event.unified_msg_origin)
            loop=asyncio.get_running_loop()
            stored=await loop.run_in_executor(p.pool,lambda:p.store.flow.db.execute('SELECT content FROM dl_capture WHERE scope=?',(scope,)).fetchone()[0])
            self.assertEqual(stored,text)
            oversized=Event('长'*4001);oversized.message_obj.message_id='too-long';oversized.message_obj.message=[Plain(oversized.message_str)]
            await p.capture_private(oversized)
            if p.capture_gap_tasks:await asyncio.gather(*list(p.capture_gap_tasks))
            self.assertEqual(await loop.run_in_executor(p.pool,p.store.capture_gap_count,scope),1)
            self.assertEqual(p.capture_skipped,1)
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_long_topic_followup_rechecks_full_excerpt_and_invalidates_change(self):
        import hashlib,json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as root:
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','automatic_memory_enabled':True,
                'followup_enabled':True,'model_dir':'fixture','context_budget_chars':2000})
            origin='test:FriendMessage:42';p._ensure_store();scope=p.store.scope('42',origin);db=p.store.flow.db;key=p.store._index().model_key
            texts=['书店计划原定周六','书店计划改到周日','书店计划说明二','书店计划改到周一',
                   '书店计划备注四','书店计划备注五','书店计划备注六','书店计划备注七','书店计划备注八']
            with db:
                db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(scope,'topic',key,'2025-01-01T00:00:00+00:00'))
                for i,text in enumerate(texts,1):
                    sha=hashlib.sha256(text.encode()).hexdigest();stamp=f'2025-01-0{i}T12:00:00+00:00'
                    db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',(i,scope,'f'+str(i),text,sha,stamp))
                    db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',(scope,i,key,'topic',sha,stamp,'测试'))
                    db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(scope,i,key,sha,'[1.0,0.0]'))
            cutoff=datetime(2025,2,1,tzinfo=timezone.utc)
            packet,focus,ids=p._context_work('42',origin,'书',current_message_id='q1',as_of=cutoff)
            self.assertEqual(json.loads(packet)['线索方式'],'系统推测事件')
            self.assertEqual(len(ids),7);self.assertEqual(focus['topic_query'],'书')
            self.assertIn('共 9 条',json.loads(packet)['覆盖说明'])
            follow,_,_=p._context_work('42',origin,'后来呢',focus=focus,current_message_id='q2',as_of=cutoff)
            self.assertEqual(json.loads(follow)['待审相关序号'],[1,2,3,4,7,8,9])
            self.assertIn('共 9 条',json.loads(follow)['覆盖说明'])
            p.store.capture(scope,'10','书店计划取消了',recorded_at='2025-01-10T12:00:00+00:00')
            follow,_,_=p._context_work('42',origin,'后来呢',focus=focus,current_message_id='q3',as_of=cutoff)
            self.assertFalse(follow)
            p.store.close();p.pool.shutdown()

    def test_combined_model_rule_chain_keeps_plugin_followup(self):
        import hashlib,json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_model import prepare
        with tempfile.TemporaryDirectory() as root:
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','automatic_memory_enabled':True,
                'auto_model_enabled':True,'draft_provider_id':'test','followup_enabled':True,'model_dir':'fixture','context_budget_chars':1500})
            origin='test:FriendMessage:42';p._ensure_store();scope=p.store.scope('42',origin);db=p.store.flow.db;key=p.store._index().model_key
            texts=['我原来住在青岛','我现在住在深圳南山区','我现在住在深圳南山区改到福田']
            def insert(i,topic,status):
                text=texts[i-1];sha=hashlib.sha256(text.encode()).hexdigest();stamp=f'2025-01-0{i}T12:00:00+00:00'
                db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',(i,scope,'f'+str(i),text,sha,stamp))
                db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',(scope,i,key,topic,sha,stamp,'测试'))
                db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(scope,i,key,sha,'[1.0,0.0]'))
                db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',(scope,i,status,p.auto_model_policy))
            with db:
                for topic in ('old','new'):db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(scope,topic,key,'2025-01-01T00:00:00+00:00'))
                insert(1,'old','done');insert(2,'new','pending')
            snapshot=prepare(db,policy=p.auto_model_policy,model_key=key)
            self.assertTrue(p._auto_model_finish(snapshot,'{"verdict":"related","quote_a":"青岛","quote_b":"深圳","relation":"update"}'))
            with db:insert(3,'new','done')
            cutoff=datetime(2025,2,1,tzinfo=timezone.utc)
            packet,focus,ids=p._context_work('42',origin,'书',current_message_id='q1',as_of=cutoff)
            self.assertEqual(json.loads(packet)['线索方式'],'跨事件组合变化线索')
            self.assertEqual(ids,('pending:1','pending:2','pending:3'));self.assertEqual(focus['cross_query'],'书')
            follow,_,_=p._context_work('42',origin,'后来呢',focus=focus,current_message_id='q2',as_of=cutoff)
            self.assertEqual(json.loads(follow)['待审相关序号'],[1,2,3]);p.store.close();p.pool.shutdown()

    def test_cross_model_chain_over_five_keeps_plugin_followup(self):
        import hashlib,json,math
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_model import prepare
        with tempfile.TemporaryDirectory() as root:
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','automatic_memory_enabled':True,
                'auto_model_enabled':True,'draft_provider_id':'test','followup_enabled':True,'model_dir':'fixture','context_budget_chars':2000})
            origin='test:FriendMessage:42';p._ensure_store();scope=p.store.scope('42',origin);db=p.store.flow.db;key=p.store._index().model_key
            places=['青岛','深圳','成都','武汉','厦门','济南']
            with db:
                for i,place in enumerate(places,1):
                    text='现在生活在'+place;sha=hashlib.sha256(text.encode()).hexdigest()
                    stamp=f'2025-01-0{i}T12:00:00+00:00';topic='t'+str(i);angle=math.radians(18*(i-1))
                    db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',(i,scope,'f'+str(i),text,sha,stamp))
                    db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(scope,topic,key,stamp))
                    db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',(scope,i,key,topic,sha,stamp,'测试'))
                    db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(scope,i,key,sha,json.dumps([math.cos(angle),math.sin(angle)])))
                    db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',(scope,i,'done' if i==1 else 'pending',p.auto_model_policy))
            for i in range(1,len(places)):
                with db:db.execute('DELETE FROM dl_auto_model_limit')
                snapshot=prepare(db,policy=p.auto_model_policy,model_key=key)
                self.assertEqual([x['seq'] for x in snapshot['sources']],[i,i+1])
                raw=json.dumps({'verdict':'related','quote_a':places[i-1],'quote_b':places[i],'relation':'update'},ensure_ascii=False)
                self.assertTrue(p._auto_model_finish(snapshot,raw))
            cutoff=datetime(2025,2,1,tzinfo=timezone.utc)
            packet,focus,ids=p._context_work('42',origin,'书',current_message_id='q1',as_of=cutoff)
            self.assertEqual(json.loads(packet)['待审相关序号'],[1,2,3,4,5,6]);self.assertEqual(len(ids),6)
            self.assertEqual(focus['cross_query'],'书')
            follow,_,_=p._context_work('42',origin,'后来呢',focus=focus,current_message_id='q2',as_of=cutoff)
            self.assertEqual(json.loads(follow)['待审相关序号'],[1,2,3,4,5,6]);p.store.close();p.pool.shutdown()

    def test_cross_event_model_hint_enters_plugin_context_and_followup(self):
        import hashlib,json
        from datetime import datetime,timezone
        from astrbot_plugin_digital_memory_store.auto_model import prepare
        with tempfile.TemporaryDirectory() as root:
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','automatic_memory_enabled':True,
                'auto_model_enabled':True,'draft_provider_id':'test','followup_enabled':True,'model_dir':'fixture','context_budget_chars':1000})
            origin='test:FriendMessage:42';p._ensure_store();scope=p.store.scope('42',origin);db=p.store.flow.db;key=p.store._index().model_key
            with db:
                for i,text in enumerate(['原来住在青岛东部','上午看了一部电影','今年搬到了深圳南山'],1):
                    sha=hashlib.sha256(text.encode()).hexdigest();stamp=f'2025-01-0{i}T12:00:00+00:00';topic='t'+str(i)
                    db.execute('INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?,?)',(i,scope,'f'+str(i),text,sha,stamp))
                    db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(scope,topic,key,stamp))
                    db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',(scope,i,key,topic,sha,stamp,'测试'))
                    db.execute('INSERT INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',(scope,i,key,sha,'[0.0,1.0]' if i==2 else '[1.0,0.0]'))
                    db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,status,policy) VALUES(?,?,?,?)',(scope,i,'pending' if i==3 else 'done',p.auto_model_policy))
            snapshot=prepare(db,policy=p.auto_model_policy,model_key=key)
            self.assertTrue(p._auto_model_finish(snapshot,'{"verdict":"related","quote_a":"青岛","quote_b":"深圳","relation":"update"}'))
            cutoff=datetime(2025,2,1,tzinfo=timezone.utc)
            packet,focus,ids=p._context_work('42',origin,'书',current_message_id='q1',as_of=cutoff)
            self.assertEqual(json.loads(packet)['线索方式'],'模型跨事件可能变化')
            self.assertEqual(ids,('pending:1','pending:3'));self.assertEqual(focus['cross_query'],'书')
            follow,_,_=p._context_work('42',origin,'后来呢',focus=focus,current_message_id='q2',as_of=cutoff)
            self.assertEqual(json.loads(follow)['待审相关序号'],[1,3]);p.store.close();p.pool.shutdown()

    def test_cross_topic_followup_rechecks_and_invalidates_on_new_change(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as root:
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','automatic_memory_enabled':True,
                'followup_enabled':True,'model_dir':'fixture','context_budget_chars':1000})
            origin='test:FriendMessage:42';p._ensure_store();scope=p.store.scope('42',origin)
            p.store.capture(scope,'1','书店计划原定周六',recorded_at='2025-01-02T00:00:00+00:00')
            p.store.capture(scope,'2','书店计划改到周日',recorded_at='2025-01-03T00:00:00+00:00')
            p._auto_work();p.store.flow.db.execute("UPDATE dl_auto_topic_members SET topic_id='other' WHERE scope=? AND seq=2",(scope,));p.store.flow.db.commit()
            cutoff=datetime(2025,2,1,tzinfo=timezone.utc)
            packet,focus,_=p._context_work('42',origin,'书店计划怎么样',current_message_id='q1',as_of=cutoff)
            self.assertEqual(json.loads(packet)['待审相关序号'],[1,2]);self.assertEqual(focus['cross_query'],'书店计划怎么样')
            follow,focus2,_=p._context_work('42',origin,'后来呢',focus=focus,current_message_id='q2',as_of=cutoff)
            self.assertEqual(json.loads(follow)['待审相关序号'],[1,2]);self.assertEqual(focus2['remaining'],1)
            p.store.capture(scope,'3','书店计划取消了',recorded_at='2025-01-04T00:00:00+00:00');p._auto_work()
            follow,updated,_=p._context_work('42',origin,'后来呢',focus=focus2,current_message_id='q3',as_of=cutoff)
            self.assertEqual(json.loads(follow)['待审相关序号'],[1,2,3])
            self.assertEqual([x['seq'] for x in updated['auto_sources']],[1,2,3])
            self.assertEqual(updated['remaining'],0);p.store.close();p.pool.shutdown()

    def test_gap_recovery_applies_cross_topic_change_protection(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as root:
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','automatic_memory_enabled':True,
                'model_dir':'fixture','auto_gap_recovery_enabled':True,'context_budget_chars':1000})
            origin='test:FriendMessage:42';p._ensure_store();scope=p.store.scope('42',origin)
            p.store.record_capture_gap(scope,'2025-01-01T00:00:00+00:00')
            p.store.capture(scope,'1','书店计划原定周六',recorded_at='2025-01-02T00:00:00+00:00')
            p.store.capture(scope,'2','书店计划改到周日',recorded_at='2025-01-03T00:00:00+00:00')
            p._auto_work()
            p.store.flow.db.execute("UPDATE dl_auto_topic_members SET topic_id='other' WHERE scope=? AND seq=2",(scope,));p.store.flow.db.commit()
            packet,_,ids=p._context_work('42',origin,'书店计划怎么样',current_message_id='q',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            data=json.loads(packet);self.assertEqual(ids,('pending:1','pending:2'))
            self.assertEqual(data['线索方式'],'跨事件文字重合与变化词');self.assertIn('历史可能不完整',data['采集情况'])
            p.store.close();p.pool.shutdown()

    def test_automatic_profile_captures_and_injects_from_hook(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','automatic_memory_enabled':True})
            loop=asyncio.get_running_loop()
            first=Event('我喜欢吃苹果');first.message_obj.message_id='m1';first.message_obj.message=[Plain(first.message_str)]
            await p.capture_private(first)
            def age_and_process():
                p.store.flow.db.execute("UPDATE dl_capture SET recorded_at='2025-01-01T00:00:00+00:00' WHERE seq=1")
                p.store.flow.db.commit();p._auto_work()
            await loop.run_in_executor(p.pool,age_and_process)
            query=Event('你之前喜欢吃苹果吗');query.message_obj.message_id='m2';query.message_obj.message=[Plain(query.message_str)]
            await p.capture_private(query)
            request=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(query,request)
            self.assertEqual(len(request.extra_user_content_parts),1)
            self.assertIn('我喜欢吃苹果',request.extra_user_content_parts[0].text)
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_automatic_memory_followup_without_user_commands(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as root:
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','automatic_memory_enabled':True})
            origin='test:FriendMessage:42';now=datetime(2025,2,1,tzinfo=timezone.utc)
            p._capture_work('42',origin,'1','我喜欢吃苹果','2025-01-01T00:00:00+00:00');p._auto_work()
            packet,focus,_=p._context_work('42',origin,'你之前喜欢吃苹果吗',current_message_id='q1',as_of=now)
            self.assertEqual(json.loads(packet)['未整理历史原话'],'我喜欢吃苹果')
            follow,focus2,_=p._context_work('42',origin,'后来呢',focus=focus,current_message_id='q2',as_of=now)
            self.assertEqual(json.loads(follow)['可能相关原话'][0]['原话'],'我喜欢吃苹果')
            self.assertEqual(focus2['remaining'],1)
            again,focus3,_=p._context_work('42',origin,'后来呢',focus=focus2,current_message_id='q3',as_of=now)
            self.assertTrue(again);self.assertEqual(focus3['remaining'],0)
            final,_,_=p._context_work('42',origin,'后来呢',focus=focus3,current_message_id='q4',as_of=now)
            self.assertFalse(final);p.store.close();p.pool.shutdown()

    def test_automatic_profile_captures_and_recalls_without_commands(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as root:
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','automatic_memory_enabled':True})
            origin='test:FriendMessage:42'
            self.assertTrue(p.capture_enabled and p.auto_organize_enabled and p.context_enabled)
            self.assertFalse(p.auto_model_enabled)
            p._capture_work('42',origin,'1','我喜欢吃苹果','2025-01-01T00:00:00+00:00');p._auto_work()
            packet,_,ids=p._context_work('42',origin,'你之前喜欢吃苹果吗',current_message_id='q',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            self.assertEqual(json.loads(packet)['未整理历史原话'],'我喜欢吃苹果')
            self.assertEqual(ids,('pending:1',));p.store.close();p.pool.shutdown()

    def test_background_model_pair_requires_no_user_review(self):
        async def go(root):
            from astrbot_plugin_digital_memory_store.auto_organize import read_group
            m.get_astrbot_data_path=lambda:root
            class Context:
                async def llm_generate(self,**kwargs):
                    return types.SimpleNamespace(completion_text='{"related":true,"quote_a":"图书馆","quote_b":"几本小说"}')
            cfg={'enabled':True,'capture_enabled':True,'auto_organize_enabled':True,
                 'auto_model_enabled':True,'draft_provider_id':'test'}
            p=m.DigitalMemoryStore(Context(),cfg);loop=asyncio.get_running_loop();origin='test:FriendMessage:42'
            await loop.run_in_executor(p.pool,p._capture_work,'42',origin,'1','周末去图书馆','2025-01-01T12:00:00+00:00')
            await loop.run_in_executor(p.pool,p._capture_work,'42',origin,'2','我去挑几本小说','2025-01-01T12:01:00+00:00')
            await p.initialize()
            async def wait_group():
                while True:
                    result=await loop.run_in_executor(p.pool,lambda:read_group(p.store.flow.db,p.store.scope('42',origin),1))
                    if result:return result
                    await asyncio.sleep(.02)
            self.assertEqual((await asyncio.wait_for(wait_group(),4))['basis'],'模型提出的可能关联')
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_gap_recovery_uses_only_post_gap_group_after_restart(self):
        import json
        from datetime import datetime,timezone
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            cfg={'enabled':True,'auto_organize_enabled':True,'auto_group_context_enabled':True,'auto_gap_recovery_enabled':True}
            p=m.DigitalMemoryStore(None,cfg);loop=asyncio.get_running_loop();origin='test:FriendMessage:42'
            def setup():
                p._ensure_store();scope=p.store.scope('42',origin)
                p.store.save(scope,'书店','原计划周六去书店')
                p.store.record_capture_gap(scope,'2025-01-02T00:00:00+00:00')
                p.store.capture(scope,'1','计划去青岛海边散步',recorded_at='2025-01-03T00:00:00+00:00')
                p.store.capture(scope,'2','青岛海边散步之后去吃饭',recorded_at='2025-01-04T00:00:00+00:00')
                p._auto_work()
            await loop.run_in_executor(p.pool,setup);await p.terminate()
            q=m.DigitalMemoryStore(None,cfg)
            def query(text):return q._context_work('42',origin,text,current_message_id='q',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            old=await loop.run_in_executor(q.pool,query,'书店怎么样')
            self.assertEqual(old[0],'')
            packet,_,ids=await loop.run_in_executor(q.pool,query,'青岛海边散步怎么样')
            self.assertIn('历史可能不完整',json.loads(packet)['采集情况'])
            self.assertEqual(ids,('pending:1','pending:2'));await q.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_gap_marker_retries_without_user_action(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'auto_gap_recovery_enabled':True});identity=('42','test:FriendMessage:42')
            original=p._record_capture_gap_work;attempts=[]
            def fail_once(*args):
                attempts.append(1)
                if len(attempts)==1:raise OSError('temporary')
                return original(*args)
            p._record_capture_gap_work=fail_once;p._note_capture_gap(identity)
            async def wait_for_persist():
                while identity in p.capture_gaps:await asyncio.sleep(.02)
            await asyncio.wait_for(wait_for_persist(),3)
            self.assertEqual(len(attempts),2)
            scope=p.store.scope(*identity)
            self.assertIsNotNone(await asyncio.get_running_loop().run_in_executor(p.pool,p.store.capture_gap_boundary,scope))
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_auto_group_context_is_wired_to_plugin_and_marked_unconfirmed(self):
        import json
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as root:
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'auto_organize_enabled':True,'auto_group_context_enabled':True})
            origin='test:FriendMessage:42'
            p._capture_work('42',origin,'1','计划去青岛海边散步','2025-01-01T00:00:00+00:00')
            p._capture_work('42',origin,'2','青岛海边散步之后去吃饭','2025-01-02T00:00:00+00:00')
            p._auto_work()
            packet,_,ids=p._context_work('42',origin,'青岛海边散步怎么样',current_message_id='q',as_of=datetime(2025,2,1,tzinfo=timezone.utc))
            self.assertIn('未确认',json.loads(packet)['说明'])
            self.assertEqual(ids,('pending:1','pending:2'))
            p.store.close();p.pool.shutdown()

    def test_auto_worker_processes_queued_history_after_restart_without_new_message(self):
        async def go(root):
            from astrbot_plugin_digital_memory_store.auto_organize import read_group
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'auto_organize_enabled':True})
            loop=asyncio.get_running_loop()
            await loop.run_in_executor(p.pool,p._capture_work,'42','test:FriendMessage:42','1','计划去青岛海边散步','2025-01-01T00:00:00+00:00')
            await loop.run_in_executor(p.pool,p._capture_work,'42','test:FriendMessage:42','2','青岛海边散步之后去吃饭','2025-01-02T00:00:00+00:00')
            await p.terminate()
            q=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'auto_organize_enabled':True})
            await q.initialize()
            async def check():
                while True:
                    def read():
                        q._ensure_store();scope=q.store.scope('42','test:FriendMessage:42')
                        return read_group(q.store.flow.db,scope,1)
                    result=await loop.run_in_executor(q.pool,read)
                    if result:return result
                    await asyncio.sleep(.01)
            self.assertEqual((await asyncio.wait_for(check(),3))['members'],[1,2])
            await q.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_capture_worker_builds_internal_candidates_without_user_actions(self):
        from astrbot_plugin_digital_memory_store.auto_organize import read_localization
        with tempfile.TemporaryDirectory() as root:
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'auto_organize_enabled':True})
            p._capture_work('42','test:FriendMessage:42','1','计划去青岛海边散步','2025-01-01T00:00:00+00:00')
            p._capture_work('42','test:FriendMessage:42','2','青岛海边散步之后去吃饭','2025-01-02T00:00:00+00:00')
            p._auto_work()
            scope=p.store.scope('42','test:FriendMessage:42')
            self.assertEqual(read_localization(p.store.flow.db,scope,1)['state'],'candidate')
            self.assertEqual(p.store.flow.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
            p.store.close();p.pool.shutdown()

    def test_acknowledging_gap_does_not_erase_new_gap(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','context_enabled':True})
            await run(p,'/dlm 记录 书店|原计划周六去书店')
            identity=('42','test:FriendMessage:42');p._note_capture_gap(identity)
            await run(p,'/dlm 采集状态')
            started=asyncio.Event();release=threading.Event();loop=asyncio.get_running_loop()
            original=p._acknowledge_capture_gap_work
            def blocked(*args):
                loop.call_soon_threadsafe(started.set)
                if not release.wait(2):raise RuntimeError('test timeout')
                return original(*args)
            p._acknowledge_capture_gap_work=blocked
            task=asyncio.create_task(run(p,'/dlm 确认采集缺口'))
            await asyncio.wait_for(started.wait(),1)
            p._note_capture_gap(identity);release.set()
            result=(await task)[0]
            self.assertIn('新的采集缺口',result);self.assertIn(identity,p.capture_gaps)
            self.assertIn('未核对缺口：1',(await run(p,'/dlm 采集状态'))[0]);await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_capture_gap_blocks_only_affected_scope_across_restart(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','capture_enabled':True,'context_enabled':True})
            await run(p,'/dlm 记录 书店|原计划周六去书店')
            def prepare():
                other=p.store.scope('43','test:FriendMessage:43')
                p.store.save(other,'登山','计划去登山')
                p.store.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,prepare)
            def fail(*args):raise OSError('simulated write failure')
            p._capture_work=fail
            event=Event('书店改到周日');event.message_obj.message_id='m1';event.message_obj.message=[Plain(event.message_str)]
            await p.capture_private(event)
            status=(await run(p,'/dlm 采集状态'))[0]
            self.assertIn('未核对缺口：1',status);self.assertIn('记忆注入已暂停',status)
            self.assertIn('未核对采集缺口',(await run(p,'/dlm 上下文状态'))[0])
            self.assertIn('预览已暂停',(await run(p,'/dlm 上下文预览 书店'))[0])
            request=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(Event('书店怎么样'),request);self.assertEqual(request.extra_user_content_parts,[])
            other=await asyncio.get_running_loop().run_in_executor(p.pool,p._context_work,'43','test:FriendMessage:43','登山')
            self.assertIn('计划去登山',other[0]);await p.terminate()
            q=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','context_enabled':True})
            request=types.SimpleNamespace(extra_user_content_parts=[])
            await q.add_memory_context(Event('书店怎么样'),request);self.assertEqual(request.extra_user_content_parts,[])
            self.assertIn('缺失原话不会自动恢复',(await run(q,'/dlm 确认采集缺口'))[0])
            request=types.SimpleNamespace(extra_user_content_parts=[])
            await q.add_memory_context(Event('书店怎么样'),request)
            self.assertEqual(len(request.extra_user_content_parts),1);await q.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_rule_update_review_and_batch_command(self):
        async def go(root):
            import re
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 书店|原计划周六去书店')
            await run(p,'/dlm 记录 登山|原计划一月去登山')
            def prepare():
                scope=p.store.scope('42','test:FriendMessage:42')
                p.store.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");p.store.flow.db.commit()
                p.store.capture(scope,'1','书店改到周日',recorded_at='2025-02-01T00:00:00+00:00')
                p.store.capture(scope,'2','登山改到二月',recorded_at='2025-02-01T00:00:00+00:00')
            await asyncio.get_running_loop().run_in_executor(p.pool,prepare)
            text=(await run(p,'/dlm 更新核对'))[0]
            self.assertIn('规则说明',text);self.assertIn('原计划周六去书店',text);self.assertNotIn('模型说明',text)
            ids=re.findall(r'C-[0-9a-f]{16}',text);self.assertEqual(len(ids),2)
            original=p.store.relate_sources;calls=[]
            def fail_second(*args):
                calls.append(1)
                if len(calls)==2:raise RuntimeError('interrupted')
                return original(*args)
            p.store.relate_sources=fail_second
            failed=(await run(p,'/dlm 批量确认对照 '+','.join(ids)+'|修正'))[0]
            self.assertIn('已回滚本次操作',failed)
            self.assertIn('书店改到周日',(await run(p,'/dlm 待审'))[0])
            p.store.relate_sources=original
            response=(await run(p,'/dlm 批量确认对照 '+','.join(ids)+'|修正'))[0]
            self.assertIn('已确认',response)
            self.assertIn('没有更多待审记录',(await run(p,'/dlm 待审'))[0]);await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_rule_group_draft_command_and_partial_confirmation(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 书店|原来去过书店')
            def prepare():
                scope=p.store.scope('42','test:FriendMessage:42')
                p.store.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");p.store.flow.db.commit()
                for i,text in enumerate(('书店周六去','书店准备买书','书店和朋友去'),1):
                    p.store.capture(scope,str(i),text,recorded_at='2025-02-01T00:00:00+00:00')
            await asyncio.get_running_loop().run_in_executor(p.pool,prepare)
            reply=(await run(p,'/dlm 规则分组草稿'))[0]
            self.assertIn('规则分组草稿 FG-',reply);self.assertIn('书店准备买书',reply)
            import re
            draft=re.search(r'FG-[0-9a-f]+',reply).group()
            self.assertIn('已关联序号：1,2',(await run(p,'/dlm 确认分组 '+draft+'|1,2'))[0])
            self.assertIn('书店和朋友去',(await run(p,'/dlm 待审'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_context_budget_configuration_and_preview(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','context_enabled':True,
                'context_budget_chars':150})
            await run(p,'/dlm 记录 登山计划|准备去四姑娘山，详细装备：'+'装备'*110)
            def age():
                p.store.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            self.assertIn('字符预算：150',(await run(p,'/dlm 上下文状态'))[0])
            self.assertIn('已审核用户原话 1 条',(await run(p,'/dlm 记忆状态'))[0])
            short=(await run(p,'/dlm 上下文预览 登山计划是什么'))[0]
            large=(await run(p,'/dlm 上下文预览 登山计划是什么|800'))[0]
            self.assertIn('预算 150 字符',short);self.assertIn('无可用内容',short)
            self.assertIn('预算 800 字符',large);self.assertIn('四姑娘山',large)
            self.assertEqual(p.context_budget_chars,150)
            self.assertIn('150至2000',(await run(p,'/dlm 上下文预览 登山计划|2001'))[0])
            req=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(Event('登山计划是什么'),req);self.assertFalse(req.extra_user_content_parts)
            await p.terminate()
            q=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','context_enabled':True,
                'context_budget_chars':800})
            req=types.SimpleNamespace(extra_user_content_parts=[])
            await q.add_memory_context(Event('登山计划是什么'),req)
            self.assertEqual(len(req.extra_user_content_parts),1)
            self.assertIn('四姑娘山',req.extra_user_content_parts[0].text)
            self.assertLessEqual(len(req.extra_user_content_parts[0].text.split('\n',1)[1]),800)
            await q.terminate()
            bad=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','context_budget_chars':True})
            self.assertEqual(bad.context_budget_chars,450)
            self.assertIn('配置无效',(await run(bad,'/dlm 上下文状态'))[0]);await bad.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_multi_event_context_hook_and_default_off(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','context_enabled':True,
                'multi_event_context_enabled':True,'followup_enabled':True})
            await run(p,'/dlm 记录 书店计划|周末去书店')
            await run(p,'/dlm 记录 登山计划|准备去登山')
            def age():
                p.store.flow.db.execute("UPDATE messages SET recorded_at='2025-01-01T00:00:00+00:00'");p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            req=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(Event('书店计划和登山计划怎么样'),req)
            self.assertEqual(len(req.extra_user_content_parts),1)
            self.assertIn('周末去书店',req.extra_user_content_parts[0].text)
            self.assertIn('准备去登山',req.extra_user_content_parts[0].text)
            self.assertFalse(p.followup_focus)
            await p.terminate()
            q=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','context_enabled':True})
            req=types.SimpleNamespace(extra_user_content_parts=[])
            await q.add_memory_context(Event('书店计划和登山计划怎么样'),req)
            self.assertFalse(req.extra_user_content_parts);await q.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_context_preview_compares_current_options_without_model_calls(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42',
                'reviewed_brief_context_enabled':True,'brief_overview_priority_enabled':True})
            await run(p,'/dlm 记录 登山计划|我准备去四姑娘山')
            output=(await run(p,'/dlm 上下文预览 登山计划是什么'))[0]
            self.assertIn('原话优先',output);self.assertIn('概览摘要优先',output)
            self.assertIn('不是 Token 或费用',output);self.assertIn('不会请求模型',output)
            self.assertIn('实际聊天上下文关闭',output)
            self.assertTrue(p.store.brief_overview_priority_enabled)
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_preference_controls_and_opt_in(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','preference_rank_enabled':True})
            await run(p,'/dlm 记录 登山|我喜欢登山')
            self.assertTrue(p.store.preference_rank_enabled)
            self.assertIn('我喜欢登山',(await run(p,'/dlm 偏好线索 登山'))[0])
            self.assertIn('已排除',(await run(p,'/dlm 偏好排除 登山'))[0])
            self.assertIn('已排除',(await run(p,'/dlm 偏好线索 登山'))[0])
            self.assertIn('已恢复',(await run(p,'/dlm 偏好恢复 登山'))[0])
            await p.terminate()
            q=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            await run(q,'/dlm 事件');self.assertFalse(q.store.preference_rank_enabled);await q.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_auto_batch_selection_and_pagination(self):
        import json
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            class Ctx:
                selections=[]
                async def llm_generate(self,chat_provider_id,prompt):
                    items=json.loads(prompt.split('\n',1)[1]);self.selections.append([x['seq'] for x in items])
                    return types.SimpleNamespace(completion_text='{"groups":[]}')
            ctx=Ctx();p=m.DigitalMemoryStore(ctx,{'enabled':True,'allowed_sender_ids':'42',
                'model_drafts_enabled':True,'draft_provider_id':'fake'})
            await run(p,'/dlm 事件')
            def capture():
                scope=p.store.scope('42','test:FriendMessage:42')
                for i in range(22):p.store.capture(scope,str(i),'用户原话'+str(i),recorded_at='2025-01-01T00:00:00+00:00')
            await asyncio.get_running_loop().run_in_executor(p.pool,capture)
            first=(await run(p,'/dlm 模型新分组 待审'))[0]
            self.assertEqual(ctx.selections[0],list(range(1,21)))
            self.assertIn('待审|20',first)
            await run(p,'/dlm 模型新分组 待审|20')
            self.assertEqual(ctx.selections[1],[21,22])
            self.assertIn('没有待审',(await run(p,'/dlm 模型新分组 待审|22'))[0])
            self.assertEqual(len(ctx.selections),2)
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_new_group_command_single_confirmation(self):
        import json,re
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            class Ctx:
                async def llm_generate(self,chat_provider_id,prompt):
                    result={'groups':[{'title':'书店计划','items':[{'seq':1,'quote':'周日去书店'},
                               {'seq':2,'quote':'买一本书'}]}]}
                    return types.SimpleNamespace(completion_text=json.dumps(result,ensure_ascii=False))
            p=m.DigitalMemoryStore(Ctx(),{'enabled':True,'allowed_sender_ids':'42',
                'model_drafts_enabled':True,'draft_provider_id':'fake'})
            await run(p,'/dlm 事件')
            def capture():
                scope=p.store.scope('42','test:FriendMessage:42')
                p.store.capture(scope,'1','周日去书店',recorded_at='2025-01-01T00:00:00+00:00')
                p.store.capture(scope,'2','想买一本书',recorded_at='2025-01-01T00:00:00+00:00')
            await asyncio.get_running_loop().run_in_executor(p.pool,capture)
            result=(await run(p,'/dlm 模型新分组 1,2'))[0]
            did=re.search(r'NG-[0-9a-f]{16}',result).group()
            self.assertIn('周日去书店',result);self.assertIn('想买一本书',result)
            self.assertIn('已关联序号：1,2',(await run(p,'/dlm 确认新分组 '+did))[0])
            self.assertIn('已关联序号：1,2',(await run(p,'/dlm 确认新分组 '+did))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_segment_cache_retry_and_clear(self):
        import json
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            class Ctx:
                calls=0
                async def llm_generate(self,chat_provider_id,prompt):
                    self.calls+=1;data=json.loads(prompt.split('\n',1)[1])
                    if self.calls==2:return types.SimpleNamespace(completion_text='bad')
                    if '原文' in data:
                        result={'summary':'这一段','source_id':data['原文'][0]['id'],'quote':'行程记录'}
                    else:result=data['分段摘要'][0]
                    return types.SimpleNamespace(completion_text=json.dumps(result,ensure_ascii=False))
            ctx=Ctx();p=m.DigitalMemoryStore(ctx,{'enabled':True,'allowed_sender_ids':'42',
                'model_drafts_enabled':True,'draft_provider_id':'fake','segment_cache_enabled':True})
            for i in range(21):await run(p,'/dlm 记录 长行程|行程记录'+str(i))
            self.assertIn('未完成',(await run(p,'/dlm 分段摘要 长行程'))[0]);self.assertEqual(ctx.calls,2)
            self.assertIn('保存 1 条',(await run(p,'/dlm 摘要缓存状态'))[0])
            result=(await run(p,'/dlm 分段摘要 长行程'))[0]
            self.assertIn('复用缓存',result);self.assertEqual(ctx.calls,4)
            result=(await run(p,'/dlm 分段摘要 长行程'))[0]
            self.assertIn('复用缓存',result);self.assertEqual(ctx.calls,5)
            self.assertIn('2 条',(await run(p,'/dlm 清理摘要缓存'))[0])
            await run(p,'/dlm 分段摘要 长行程');self.assertEqual(ctx.calls,8)
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_segmented_brief_end_to_end_and_failure(self):
        import json,re
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            class Ctx:
                calls=0;broken=False
                async def llm_generate(self,chat_provider_id,prompt):
                    self.calls+=1;data=json.loads(prompt.split('\n',1)[1])
                    if self.broken:return types.SimpleNamespace(completion_text='invalid')
                    if '原文' in data:
                        source=data['原文'][0]
                        result={'summary':'分段概览','source_id':source['id'],'quote':'行程记录'}
                    else:result=data['分段摘要'][0]
                    return types.SimpleNamespace(completion_text=json.dumps(result,ensure_ascii=False))
            ctx=Ctx();p=m.DigitalMemoryStore(ctx,{'enabled':True,'allowed_sender_ids':'42',
                'model_drafts_enabled':True,'draft_provider_id':'fake'})
            for i in range(21):await run(p,'/dlm 记录 长行程|行程记录'+str(i))
            result=(await run(p,'/dlm 分段摘要 长行程'))[0]
            did=re.search(r'BS-[0-9a-f]{16}',result).group()
            self.assertEqual(ctx.calls,3);self.assertIn('分段 1 摘要',result)
            self.assertIn('第 5/5 页',(await run(p,'/dlm 摘要草稿 '+did+'|5'))[0])
            self.assertIn('已确认',(await run(p,'/dlm 确认摘要 '+did))[0])
            ctx.broken=True
            self.assertIn('未完成',(await run(p,'/dlm 分段摘要 长行程'))[0])
            self.assertIn('模型生成、人工确认',(await run(p,'/dlm 人工摘要查看 长行程'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_model_brief_pagination_and_origin_label(self):
        import json,re
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            class Ctx:
                async def llm_generate(self,chat_provider_id,prompt):
                    payload=json.loads(prompt.split('\n',1)[1]);source=payload['原文'][0]
                    return types.SimpleNamespace(completion_text=json.dumps({'summary':'一些长事件记录',
                        'source_id':source['id'],'quote':'完整记录'},ensure_ascii=False))
            p=m.DigitalMemoryStore(Ctx(),{'enabled':True,'allowed_sender_ids':'42',
                'model_drafts_enabled':True,'draft_provider_id':'fake'})
            for i in range(7):await run(p,'/dlm 记录 长事件|第'+str(i)+'条完整记录')
            result=(await run(p,'/dlm 模型摘要 长事件'))[0]
            did=re.search(r'BS-[0-9a-f]{16}',result).group()
            self.assertIn('第 1/2 页',result)
            page=(await run(p,'/dlm 摘要草稿 '+did+'|2'))[0]
            self.assertIn('第 2/2 页',page)
            for i in range(7):self.assertIn('第'+str(i)+'条完整记录',result+'\n'+page)
            self.assertIn('超出范围',(await run(p,'/dlm 摘要草稿 '+did+'|3'))[0])
            self.assertIn('已确认',(await run(p,'/dlm 确认摘要 '+did))[0])
            self.assertIn('模型生成、人工确认',(await run(p,'/dlm 人工摘要查看 长事件'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_model_brief_command_review_flow(self):
        import json,re
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            class Ctx:
                async def llm_generate(self,chat_provider_id,prompt):
                    payload=json.loads(prompt.split('\n',1)[1]);source=payload['原文'][0]
                    return types.SimpleNamespace(completion_text=json.dumps({'summary':'计划周日去书店。',
                        'source_id':source['id'],'quote':'周日去书店'},ensure_ascii=False))
            p=m.DigitalMemoryStore(Ctx(),{'enabled':True,'allowed_sender_ids':'42',
                'model_drafts_enabled':True,'draft_provider_id':'fake'})
            await run(p,'/dlm 记录 书店|计划周日去书店')
            result=(await run(p,'/dlm 模型摘要 书店'))[0]
            did=re.search(r'BS-[0-9a-f]{16}',result).group()
            self.assertIn('计划周日去书店',result)
            self.assertIn('没有人工摘要',(await run(p,'/dlm 人工摘要查看 书店'))[0])
            self.assertIn('未确认',(await run(p,'/dlm 摘要草稿 '+did))[0])
            self.assertIn('已确认',(await run(p,'/dlm 确认摘要 '+did))[0])
            self.assertIn('计划周日去书店',(await run(p,'/dlm 人工摘要查看 书店'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_model_group_confirmation_command(self):
        import json,re
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            class Ctx:
                async def llm_generate(self,chat_provider_id,prompt):
                    return types.SimpleNamespace(completion_text=json.dumps({'groups':[{'title':'书店','items':[{'seq':1,'quote':'周日去书店'}]}]},ensure_ascii=False))
            p=m.DigitalMemoryStore(Ctx(),{'enabled':True,'capture_enabled':True,'allowed_sender_ids':'42',
                'model_drafts_enabled':True,'draft_provider_id':'fake'})
            await run(p,'/dlm 记录 书店|之前去过书店')
            def age():
                p.store.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            e=Event('计划周日去书店');e.message_obj.message_id='1';e.message_obj.message=[Plain(e.message_str)]
            await p.capture_private(e)
            result=(await run(p,'/dlm 模型分组 1|书店'))[0]
            did=re.search(r'FG-[0-9a-f]{16}',result).group()
            self.assertIn('计划周日去书店',result)
            self.assertIn(did,(await run(p,'/dlm 分组草稿 '+did))[0])
            self.assertIn('已关联序号：1',(await run(p,'/dlm 确认分组 '+did+'|1'))[0])
            self.assertIn('已关联序号：1',(await run(p,'/dlm 确认分组 '+did))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_model_group_opt_in_and_stale_snapshot(self):
        import json
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            class Ctx:
                calls=0;mutate=False;p=None
                async def llm_generate(self,chat_provider_id,prompt):
                    self.calls+=1
                    if self.mutate:await run(self.p,'/dlm 记录 书店计划|后来还提到书')
                    return types.SimpleNamespace(completion_text=json.dumps({'groups':[{'title':'书店计划','items':[{'seq':1,'quote':'周日去书店'}]}]},ensure_ascii=False))
            ctx=Ctx();p=m.DigitalMemoryStore(ctx,{'enabled':True,'capture_enabled':True,'allowed_sender_ids':'42','draft_provider_id':'fake'})
            ctx.p=p
            await run(p,'/dlm 记录 书店计划|之前去过书店')
            def age():
                p.store.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            e=Event('计划周日去书店');e.message_obj.message_id='1';e.message_obj.message=[Plain(e.message_str)]
            await p.capture_private(e)
            self.assertIn('未开启',(await run(p,'/dlm 模型分组 1|书店计划'))[0]);self.assertEqual(ctx.calls,0)
            p.model_drafts_enabled=True
            result=(await run(p,'/dlm 模型分组 1|书店计划'))[0]
            self.assertIn('/dlm 批量关联 1|书店计划',result)
            self.assertIn('1 [',(await run(p,'/dlm 待审'))[0])
            ctx.mutate=True
            self.assertIn('未完成',(await run(p,'/dlm 模型分组 1|书店计划'))[0])
            self.assertIn('1 [',(await run(p,'/dlm 待审'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_group_suggestion_command_offers_batch_link(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 书店计划|之前去过书店')
            def age():
                p.store.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            for mid,text in [('1','书店计划周六去'),('2','改到周日')]:
                e=Event(text);e.message_obj.message_id=mid;e.message_obj.message=[Plain(text)]
                await p.capture_private(e)
            result=(await run(p,'/dlm 分组建议'))[0]
            self.assertIn('书店计划周六去',result);self.assertIn('改到周日',result)
            self.assertIn('/dlm 批量关联 1,2|书店计划',result)
            self.assertIn('1 [',(await run(p,'/dlm 待审'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_linked_update_review_command(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 书店|原计划周六去书店')
            await run(p,'/dlm 记录 书店|计划改到周日')
            def age():
                rows=p.store.flow._snapshot(p.store.scope('42','test:FriendMessage:42'),p.store.flow._events(p.store.scope('42','test:FriendMessage:42'))[0]['id'])['rows']
                for i,row in enumerate(rows):
                    p.store.flow.db.execute('UPDATE messages SET recorded_at=? WHERE scope=? AND id=?',(f'2026-01-0{i+1}T00:00:00+00:00',p.store.scope('42','test:FriendMessage:42'),row['id']))
                # 按内容设置时间，避免同秒随机 ID 决定先后。
                p.store.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00' WHERE content='原计划周六去书店'")
                p.store.flow.db.execute("UPDATE messages SET recorded_at='2026-01-02T00:00:00+00:00' WHERE content='计划改到周日'")
                p.store.flow.db.commit()
                return p.store.linked_update_clues(p.store.scope('42','test:FriendMessage:42'),'书店')['items'][0]['new_id']
            mid=await asyncio.get_running_loop().run_in_executor(p.pool,age)
            self.assertIn(mid,(await run(p,'/dlm 待确认关系 书店'))[0])
            self.assertIn('原话和已有关系均保留',(await run(p,'/dlm 排除更新提示 书店|'+mid))[0])
            self.assertIn('没有更多',(await run(p,'/dlm 待确认关系 书店'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_detail_priority_configuration_is_opt_in(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','detail_priority_enabled':True})
            await run(p,'/dlm 记录 登山计划|准备蓝色登山背包')
            self.assertTrue(p.store.detail_priority_enabled)
            await p.terminate()
            q=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            await run(q,'/dlm 事件')
            self.assertFalse(q.store.detail_priority_enabled)
            await q.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_capture_during_context_discards_snapshot(self):
        async def go():
            p=m.DigitalMemoryStore(None,{'enabled':True,'context_enabled':True,'capture_enabled':True,'allowed_sender_ids':'42'})
            loop=asyncio.get_running_loop();started=asyncio.Event();release=threading.Event()
            def slow_context(*args):
                loop.call_soon_threadsafe(started.set)
                if not release.wait(3):raise RuntimeError('test timeout')
                return ('old memory',None,None)
            p._context_work=slow_context;p._capture_work=lambda *args: True
            req=types.SimpleNamespace(extra_user_content_parts=[])
            ctx=asyncio.create_task(p.add_memory_context(Event('书店现在怎样'),req))
            await asyncio.wait_for(started.wait(),2)
            e=Event('书店取消了');e.message_obj.message_id='new';e.message_obj.message=[Plain(e.message_str)]
            capture=asyncio.create_task(p.capture_private(e));await asyncio.sleep(0)
            release.set();await asyncio.gather(ctx,capture)
            self.assertEqual(req.extra_user_content_parts,[])
            await p.terminate()
        asyncio.run(go())

    def test_other_scope_capture_does_not_invalidate_context(self):
        async def go():
            p=m.DigitalMemoryStore(None,{'enabled':True,'context_enabled':True,'capture_enabled':True,'allowed_sender_ids':'42'})
            loop=asyncio.get_running_loop();started=asyncio.Event();release=threading.Event()
            def slow_context(*args):
                loop.call_soon_threadsafe(started.set)
                if not release.wait(3):raise RuntimeError('test timeout')
                return ('memory for scope 42',None,None)
            p._context_work=slow_context;p._capture_work=lambda *args:True
            req=types.SimpleNamespace(extra_user_content_parts=[])
            ctx=asyncio.create_task(p.add_memory_context(Event('书店怎么样'),req))
            await asyncio.wait_for(started.wait(),2)
            e=Event('其他会话消息');e.unified_msg_origin='test:FriendMessage:other'
            e.message_obj.message_id='other';e.message_obj.message=[Plain(e.message_str)]
            capture=asyncio.create_task(p.capture_private(e));await asyncio.sleep(0)
            release.set();await asyncio.gather(ctx,capture)
            self.assertEqual(len(req.extra_user_content_parts),1)
            self.assertEqual(p.capture_pending_by_scope,{})
            await p.terminate()
        asyncio.run(go())

    def test_other_scope_request_does_not_cancel_active_context(self):
        async def go():
            p=m.DigitalMemoryStore(None,{'enabled':True,'context_enabled':True,'allowed_sender_ids':'42'})
            loop=asyncio.get_running_loop();started=asyncio.Event();release=threading.Event()
            def slow_context(*args):
                loop.call_soon_threadsafe(started.set)
                if not release.wait(3):raise RuntimeError('test timeout')
                return ('memory',None,None)
            p._context_work=slow_context
            req=types.SimpleNamespace(extra_user_content_parts=[])
            ctx=asyncio.create_task(p.add_memory_context(Event('书店怎么样'),req))
            await asyncio.wait_for(started.wait(),2)
            other=Event('其他话题');other.unified_msg_origin='test:FriendMessage:other'
            other_req=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(other,other_req)
            release.set();await ctx
            self.assertEqual(len(req.extra_user_content_parts),1)
            self.assertEqual(other_req.extra_user_content_parts,[])
            await p.terminate()
        asyncio.run(go())

    def test_pending_capture_skips_context(self):
        async def go():
            p=m.DigitalMemoryStore(None,{'enabled':True,'context_enabled':True,'allowed_sender_ids':'42'})
            p.capture_pending_by_scope[('42','test:FriendMessage:42')]=1
            req=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(Event('书店怎么样'),req)
            self.assertEqual(req.extra_user_content_parts,[])
            self.assertIsNone(p.pending_context)
            await p.terminate()
        asyncio.run(go())

    def test_manual_update_command_without_model(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 书店|原计划周六去书店')
            def age():
                p.store.flow.db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            e=Event('书店改到周日');e.message_obj.message_id='1';e.message_obj.message=[Plain(e.message_str)]
            await p.capture_private(e)
            clue=(await run(p,'/dlm 更新线索'))[0]
            old_id=await asyncio.get_running_loop().run_in_executor(p.pool,lambda:p.store.update_clues(p.store.scope('42',e.unified_msg_origin))[0]['matches'][0]['old_id'])
            self.assertIn('关联更新',clue)
            result=(await run(p,'/dlm 关联更新 1|书店|'+old_id+'|修正'))[0]
            self.assertIn('旧原文保留',result)
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_batch_link_command(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'allowed_sender_ids':'42'})
            for mid,text in [('1','周末去书店'),('2','改到周日')]:
                e=Event(text);e.message_obj.message_id=mid;e.message_obj.message=[Plain(text)]
                await p.capture_private(e)
            self.assertIn('已关联序号：1,2',(await run(p,'/dlm 批量关联 1，2|书店'))[0])
            self.assertIn('未开始',(await run(p,'/dlm 批量关联 1,1|书店'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_capture_buffer_overflow_cancellation_and_shutdown(self):
        async def go():
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'allowed_sender_ids':'42'})
            p._record_capture_gap_work=lambda sender,origin:None
            p.capture_queue=asyncio.Queue(maxsize=1)
            loop=asyncio.get_running_loop();started=asyncio.Event();release=threading.Event();written=[]
            def work(sender,origin,mid,text,received_at):
                if mid=='1':
                    loop.call_soon_threadsafe(started.set)
                    if not release.wait(3):raise RuntimeError('test timeout')
                written.append((mid,text,received_at))
                return True
            p._capture_work=work
            def event_for(mid):
                e=Event('消息'+mid);e.message_obj.message_id=mid;e.message_obj.message=[Plain(e.message_str)]
                return e
            first=asyncio.create_task(p.capture_private(event_for('1')))
            await asyncio.wait_for(started.wait(),2)
            second=asyncio.create_task(p.capture_private(event_for('2')))
            await asyncio.sleep(0)
            second.cancel()
            with self.assertRaises(asyncio.CancelledError):await second
            await p.capture_private(event_for('3'))
            self.assertEqual(p.capture_skipped,1)
            self.assertIn(('42','test:FriendMessage:42'),p.capture_gaps)
            closing=asyncio.create_task(p.terminate())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            release.set()
            await asyncio.gather(first,closing)
            self.assertEqual([x[0] for x in written],['1','2'])
            self.assertTrue(all(x[2].endswith('+00:00') for x in written))
        asyncio.run(go())

    def test_update_clues_use_source_text_without_manual_alias(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'weak_association_enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 登山准备|以前去过四姑娘山大峰')
            def age():
                p.store.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            e=Event('四姑娘山大峰改到周日');e.message_obj.message_id='m1';e.message_obj.message=[Plain(e.message_str)]
            await p.capture_private(e)
            result=(await run(p,'/dlm 更新线索'))[0]
            self.assertIn('登山准备',result);self.assertIn('历史原文词组匹配',result)
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_pending_pair_rejected_as_a_unit(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'context_enabled':True,
                'pending_context_enabled':True,'allowed_sender_ids':'42'})
            for mid,text in [('old','周末书店原计划周六去'),('new','改到周日去'),('cancel','取消了')]:
                e=Event(text);e.message_obj.message_id=mid;e.message_obj.message=[Plain(text)]
                await p.capture_private(e)
            def age():
                p.store.flow.db.execute("update dl_capture set recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            def event(text,mid):
                e=Event(text);e.message_obj.message_id=mid;return e
            def req():return types.SimpleNamespace(extra_user_content_parts=[])
            current=event('周末书店计划怎么样？','current')
            first=req();await p.add_memory_context(current,first)
            self.assertIn('后续可能更新',first.extra_user_content_parts[0].text)
            self.assertIn('周日',first.extra_user_content_parts[0].text)
            self.assertIn('取消了',first.extra_user_content_parts[0].text)
            await p.add_memory_context(event('不是那次','deny'),req())
            again=req();await p.add_memory_context(current,again)
            self.assertEqual(again.extra_user_content_parts,[])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_pending_context_works_without_manual_event_link(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'context_enabled':True,
                'pending_context_enabled':True,'allowed_sender_ids':'42'})
            old=Event('以前计划去四姑娘山大峰');old.message_obj.message_id='old';old.message_obj.message=[Plain(old.message_str)]
            await p.capture_private(old)
            def age():
                p.store.flow.db.execute("update dl_capture set recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            current=Event('四姑娘山大峰路线怎样？');current.message_obj.message_id='current';current.message_obj.message=[Plain(current.message_str)]
            await p.capture_private(current)
            req=lambda:types.SimpleNamespace(extra_user_content_parts=[])
            response=req();await p.add_memory_context(current,response)
            self.assertIn('未整理历史原话',response.extra_user_content_parts[0].text)
            self.assertIn('以前计划去四姑娘山大峰',response.extra_user_content_parts[0].text)
            self.assertIn('尚未确认',response.extra_user_content_parts[0].text)
            denied=Event('不是那次');denied.message_obj.message_id='denial'
            await p.add_memory_context(denied,req())
            again=req();await p.add_memory_context(current,again)
            self.assertEqual(again.extra_user_content_parts,[])
            self.assertIn('1 [',(await run(p,'/dlm 待审'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_reject_recent_association_temporarily_suppresses_it(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'context_enabled':True,'weak_association_enabled':True,
                'association_context_enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 一月登山|以前提到四姑娘山大峰')
            def age():
                p.store.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            req=lambda:types.SimpleNamespace(extra_user_content_parts=[])
            first=req();await p.add_memory_context(Event('四姑娘山路线怎样？'),first)
            self.assertIn('关联历史',first.extra_user_content_parts[0].text)
            await p.add_memory_context(Event('不是那次'),req())
            again=req();await p.add_memory_context(Event('四姑娘山路线怎样？'),again)
            self.assertEqual(again.extra_user_content_parts,[])
            explicit=req();await p.add_memory_context(Event('一月登山是什么？'),explicit)
            self.assertIn('以前提到',explicit.extra_user_content_parts[0].text)
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_alias_commands_enable_explicit_event_context(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'context_enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 登山计划|计划去四姑娘山')
            self.assertIn('已添加',(await run(p,'/dlm 别名 登山计划|大峰'))[0])
            self.assertIn('大峰',(await run(p,'/dlm 别名查看 登山计划'))[0])
            req=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(Event('大峰怎么样？'),req)
            self.assertIn('计划去四姑娘山',req.extra_user_content_parts[0].text)
            self.assertIn('已删除',(await run(p,'/dlm 别名删除 登山计划|大峰'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_weak_context_hook_without_model_returns_candidate_only(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'context_enabled':True,'weak_association_enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 一月登山|以前提到四姑娘山大峰')
            def age():
                p.store.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            req=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(Event('四姑娘山路线怎样？'),req)
            self.assertEqual(len(req.extra_user_content_parts),1)
            self.assertIn('未确认的联想候选',req.extra_user_content_parts[0].text)
            self.assertIn('一月登山',req.extra_user_content_parts[0].text)
            self.assertNotIn('以前提到',req.extra_user_content_parts[0].text)
            p.association_context_enabled=True
            req2=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(Event('四姑娘山路线怎样？'),req2)
            self.assertIn('关联历史',req2.extra_user_content_parts[0].text)
            self.assertIn('以前提到',req2.extra_user_content_parts[0].text)
            self.assertIn('尚未确认',req2.extra_user_content_parts[0].text)
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_weak_association_explicit_and_automatic_modes(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'weak_association_enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 一月登山|计划四姑娘山大峰')
            def age():
                p.store.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            e=Event('四姑娘山路线怎样？');e.message_obj.message_id='w1';e.message_obj.message=[Plain(e.message_str)]
            await p.capture_private(e)
            self.assertIn('一月登山',(await run(p,'/dlm 整理'))[0])
            self.assertIn('原文词组弱匹配',(await run(p,'/dlm 弱联想 1'))[0])
            clue=(await run(p,'/dlm 线索 1'))[0]
            self.assertIn('四姑娘山',clue);self.assertIn('来源',clue);self.assertIn('未确认',clue)
            self.assertIn('1 [',(await run(p,'/dlm 待审'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_timeline_and_event_catalog_commands(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 书店|周六去书店')
            self.assertIn('书店',(await run(p,'/dlm 事件'))[0])
            self.assertIn('周六去书店',(await run(p,'/dlm 时间线 记录'))[0])
            self.assertIn('没有更多',(await run(p,'/dlm 时间线 发生'))[0])
            view=(await run(p,'/dlm 查看 书店'))[0]
            mid=view.split('[')[1].split('|')[0].strip()
            self.assertIn('已保存',(await run(p,'/dlm 日期 '+mid+'|2027-01-03'))[0])
            self.assertIn('2027-01-03',(await run(p,'/dlm 时间线 发生'))[0])
            self.assertIn('2027-01-03',(await run(p,'/dlm 日期历史 '+mid))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_update_clue_command(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 书店计划|周六去书店')
            def age():
                p.store.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            e=Event('书店计划改到周日');e.message_obj.message_id='u1';e.message_obj.message=[Plain(e.message_str)]
            await p.capture_private(e)
            result=(await run(p,'/dlm 更新线索'))[0]
            self.assertIn('书店计划',result);self.assertIn('未确认',result);self.assertIn('周六',result)
            e2=Event('改到周一');e2.message_obj.message_id='u2';e2.message_obj.message=[Plain(e2.message_str)]
            await p.capture_private(e2)
            self.assertIn('前一条采集记录的标题',(await run(p,'/dlm 更新线索'))[0])
            self.assertIn('1 [',(await run(p,'/dlm 待审'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_source_brief_commands_and_opt_in(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','reviewed_brief_context_enabled':True})
            await run(p,'/dlm 记录 登山计划|计划去四姑娘山')
            def get_id():return p.store.show(p.store.scope('42','test:FriendMessage:42'),'登山计划')[0]['id']
            mid=await asyncio.get_running_loop().run_in_executor(p.pool,get_id)
            self.assertIn('已保存人工摘要',(await run(p,'/dlm 人工摘要保存 登山计划|'+mid+'|四姑娘山|提过登山计划'))[0])
            text=(await run(p,'/dlm 人工摘要查看 登山计划'))[0]
            self.assertIn('来源未变化',text);self.assertIn('四姑娘山',text)
            self.assertTrue(p.store.reviewed_brief_context_enabled)
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_occurred_date_context_is_opt_in(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','occurred_date_context_enabled':True})
            await run(p,'/dlm 记录 出行计划|一月去爬山')
            self.assertTrue(p.store.occurred_date_context_enabled)
            await p.terminate()
            q=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            await run(q,'/dlm 事件')
            self.assertFalse(q.store.occurred_date_context_enabled)
            await q.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_recency_rank_configuration_is_opt_in(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','recency_rank_enabled':True})
            await run(p,'/dlm 记录 登山计划|喜欢登山')
            self.assertTrue(p.store.recency_rank_enabled)
            await p.terminate()
            q=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            await run(q,'/dlm 事件')
            self.assertFalse(q.store.recency_rank_enabled)
            await q.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_event_priority_commands_and_opt_in(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','event_priority_enabled':True})
            await run(p,'/dlm 记录 登山计划|喜欢登山')
            self.assertIn('已设置',(await run(p,'/dlm 优先级 登山计划|重要'))[0])
            self.assertIn('重要',(await run(p,'/dlm 优先级查看 登山计划'))[0])
            self.assertTrue(p.store.event_priority_enabled)
            self.assertIn('已设置',(await run(p,'/dlm 优先级 登山计划|普通'))[0])
            self.assertIn('普通',(await run(p,'/dlm 优先级查看 登山计划'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_compact_context_config_is_opt_in(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','compact_context_enabled':True})
            await run(p,'/dlm 记录 书店|周末去书店')
            self.assertTrue(p.store.compact_context_enabled)
            await p.terminate()
            q=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            await run(q,'/dlm 事件')
            self.assertFalse(q.store.compact_context_enabled)
            await q.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_reviewed_correction_and_followup_end_to_end(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'context_enabled':True,'followup_enabled':True,
                'capture_enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 登山计划|之前准备黑色登山背包')
            def age_and_get():
                db=p.store.flow.db;db.execute("UPDATE messages SET recorded_at='2026-01-01T00:00:00+00:00'");db.commit()
                return p.store.show(p.store.scope('42','test:FriendMessage:42'),'登山计划')[0]['id']
            old=await asyncio.get_running_loop().run_in_executor(p.pool,age_and_get)
            e=Event('改成蓝色登山背包');e.message_obj.message_id='change';e.message_obj.message=[Plain(e.message_str)]
            await p.capture_private(e)
            result=(await run(p,'/dlm 关联更新 1|登山计划|'+old+'|修正'))[0]
            self.assertIn('旧原文保留',result)
            req=lambda:types.SimpleNamespace(extra_user_content_parts=[])
            first=req();await p.add_memory_context(Event('登山计划怎么样？'),first)
            self.assertIn('黑色登山背包',first.extra_user_content_parts[0].text)
            self.assertIn('蓝色登山背包',first.extra_user_content_parts[0].text)
            self.assertIn('修正',first.extra_user_content_parts[0].text)
            follow=req();await p.add_memory_context(Event('那个背包是什么颜色？'),follow)
            self.assertIn('指代未确认',follow.extra_user_content_parts[0].text)
            self.assertIn('蓝色登山背包',follow.extra_user_content_parts[0].text)
            await p.add_memory_context(Event('换个话题'),req())
            after=req();await p.add_memory_context(Event('那个背包呢？'),after)
            self.assertEqual(after.extra_user_content_parts,[])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_followup_optin_and_intervening_turn(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'context_enabled':True,'followup_enabled':True,'allowed_sender_ids':'42','model_dir':'fake'})
            await run(p,'/dlm 记录 书店计划|周六去书店')
            def req():return types.SimpleNamespace(extra_user_content_parts=[])
            first=req();await p.add_memory_context(Event('书店计划是什么？'),first)
            second=req();await p.add_memory_context(Event('后来呢？'),second)
            self.assertIn('指代未确认',second.extra_user_content_parts[0].text)
            await p.add_memory_context(Event('谈点别的'),req())
            after=req();await p.add_memory_context(Event('后来呢？'),after)
            self.assertEqual(after.extra_user_content_parts,[])
            p.followup_enabled=False
            await p.add_memory_context(Event('书店计划'),req())
            disabled=req();await p.add_memory_context(Event('后来呢？'),disabled)
            self.assertNotIn('指代未确认',str(disabled.extra_user_content_parts))
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_multiturn_command_keeps_records_pending(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 书店|周六去书店')
            def age_old():
                p.store.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age_old)
            for mid,content in [('m1','之前的计划？'),('m2','书店那次')]:
                e=Event(content);e.message_obj.message_id=mid;e.message_obj.message=[Plain(content)]
                await p.capture_private(e)
            result=(await run(p,'/dlm 多轮建议 1，2'))[0]
            self.assertIn('书店',result);self.assertIn('未确认',result)
            self.assertIn('1 [',(await run(p,'/dlm 待审'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_contrast_calls_fake_model_only_after_optin(self):
        import json
        class Context:
            def __init__(self):self.calls=0
            async def llm_generate(self,chat_provider_id,prompt):
                self.calls+=1; data=json.loads(prompt.split('\n')[-1]);old=data['旧原文'][0]
                return types.SimpleNamespace(completion_text=json.dumps({'relation':'correction','explanation':'时间变化',
                    'old_id':old['id'],'old_quote':'周六','new_quote':'周日'},ensure_ascii=False))
        async def go(root):
            m.get_astrbot_data_path=lambda:root;ctx=Context()
            p=m.DigitalMemoryStore(ctx,{'enabled':True,'capture_enabled':True,'allowed_sender_ids':'42'})
            await run(p,'/dlm 记录 书店|周六去书店')
            loop=asyncio.get_running_loop()
            def age_old():
                p.store.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await loop.run_in_executor(p.pool,age_old)
            e=Event('改到周日');e.message_obj.message_id='m1';e.message_obj.message=[Plain(e.message_str)]
            await p.capture_private(e)
            self.assertIn('未开启',(await run(p,'/dlm 模型对照 1|书店'))[0]);self.assertEqual(ctx.calls,0)
            p.model_drafts_enabled=True;p.draft_provider='fake'
            result=(await run(p,'/dlm 模型对照 1|书店'))[0]
            self.assertIn('可能更正',result);self.assertEqual(ctx.calls,1)
            did=result.split()[1].split('（')[0]
            self.assertIn('未审核',(await run(p,'/dlm 对照草稿 '+did))[0])
            self.assertIn('已按你选择的关系',(await run(p,'/dlm 确认对照 '+did+'|修正'))[0])
            self.assertIn('已按你选择的关系',(await run(p,'/dlm 确认对照 '+did+'|修正'))[0])
            self.assertIn('修正',(await run(p,'/dlm 查看 书店'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_model_draft_explicit_optin_and_fake_provider(self):
        import json
        class Context:
            def __init__(self):self.calls=0
            async def llm_generate(self,chat_provider_id,prompt):
                self.calls+=1
                return types.SimpleNamespace(completion_text=json.dumps({'title':'周末书店','summary':'准备去书店',
                    'evidence':[{'seq':1,'quote':'去书店'}]},ensure_ascii=False))
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            ctx=Context();p=m.DigitalMemoryStore(ctx,{'enabled':True,'capture_enabled':True,'allowed_sender_ids':'42'})
            e=Event('周末去书店');e.message_obj.message_id='m1';e.message_obj.message=[Plain(e.message_str)]
            await p.capture_private(e)
            self.assertIn('未开启',(await run(p,'/dlm 模型整理 1'))[0]);self.assertEqual(ctx.calls,0)
            p.model_drafts_enabled=True;p.draft_provider='fixture-provider'
            result=(await run(p,'/dlm 模型整理 1'))[0]
            self.assertIn('模型草稿',result);self.assertIn('未审核',result);self.assertEqual(ctx.calls,1)
            draft_id=result.split()[1].split('（')[0]
            self.assertIn('未审核',(await run(p,'/dlm 模型草稿 '+draft_id))[0])
            self.assertIn('没有', (await run(p,'/dlm 查看 周末书店'))[0].replace('未找到','没有'))
            await run(p,'/dlm 关联 1|周末书店')
            self.assertIn('已过期',(await run(p,'/dlm 模型草稿 '+draft_id))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_capture_optin_and_review(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'capture_enabled':True,'allowed_sender_ids':'42','model_dir':'fake'})
            e=Event('周六去书店');e.message_obj.message_id='m1';e.message_obj.message=[Plain(e.message_str)]
            await p.capture_private(e);await p.capture_private(e)
            pending=(await run(p,'/dlm 待审'))[0]
            self.assertIn('1 [',pending);self.assertIn('周六去书店',pending)
            self.assertIn('未找到',(await run(p,'/dlm 建议 1'))[0])
            self.assertIn('待审核建议',(await run(p,'/dlm 整理'))[0])
            self.assertIn('已将原文关联',(await run(p,'/dlm 关联 1|周末书店'))[0])
            self.assertIn('周六去书店',(await run(p,'/dlm 查看 周末书店'))[0])
            self.assertIn('原文摘录',(await run(p,'/dlm 摘录 周末书店'))[0])
            self.assertIn('第1页',(await run(p,'/dlm 查看 周末书店|1'))[0])
            p.capture_enabled=False
            e.message_obj.message_id='m2';await p.capture_private(e)
            self.assertIn('没有更多待审',(await run(p,'/dlm 待审'))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_manual_commands(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42','model_dir':'fake'})
            self.assertEqual(await run(p,'/dlm 记录 周末书店|周六去书店',group='group'),[])
            self.assertIn('已保存', (await run(p,'/dlm 记录 周末书店|周六去书店'))[0])
            self.assertIn('周末书店',(await run(p,'/dlm 检索 书店'))[0])
            self.assertIn('周六去书店',(await run(p,'/dlm 查看 周末书店'))[0])
            import re
            reply=(await run(p,'/dlm 备份'))[0]
            self.assertIn('备份完成',reply)
            name=re.search(r'memory_\d{8}T\d{6}_[0-9a-f]{8}\.sqlite3',reply).group()
            self.assertIn('备份校验通过',(await run(p,'/dlm 备份校验 '+name))[0])
            self.assertIn('未完成',(await run(p,'/dlm 备份校验 ../'+name))[0])
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))
    def test_context_enabled_explicit_source_and_scoping(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'context_enabled':True,'allowed_sender_ids':'42','model_dir':'fake'})
            await run(p,'/dlm 记录 周末书店|周六去书店')
            def age():
                p.store.flow.db.execute("update messages set recorded_at='2026-01-01T00:00:00+00:00'")
                p.store.flow.db.commit()
            await asyncio.get_running_loop().run_in_executor(p.pool,age)
            req=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(Event('周末书店最初的安排是什么？'),req)
            self.assertEqual(len(req.extra_user_content_parts),1)
            self.assertTrue(req.extra_user_content_parts[0].temporary)
            self.assertIn('周六去书店',req.extra_user_content_parts[0].text)
            await p.add_memory_context(Event('周末书店最初的安排是什么？'),req)
            self.assertEqual(len(req.extra_user_content_parts),1)
            req2=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(Event('周末书店',group='group'),req2)
            self.assertFalse(req2.extra_user_content_parts)
            req3=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(Event('书店之前怎样？'),req3)
            self.assertIn('未确认的联想候选',req3.extra_user_content_parts[0].text)
            self.assertNotIn('周六去书店',req3.extra_user_content_parts[0].text)
            p.context_enabled=False
            req4=types.SimpleNamespace(extra_user_content_parts=[])
            await p.add_memory_context(Event('周末书店'),req4)
            self.assertFalse(req4.extra_user_content_parts)
            await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))


class CommandHelp(unittest.TestCase):
    """`/dlm 帮助` 与未知命令的提示：文档承诺的入口必须真实存在。"""

    def test_help_lists_commands_and_unknown_points_to_help(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            try:
                text=(await run(p,'/dlm 帮助'))[0]
                self.assertIn('常用命令',text)
                self.assertIn('/dlm 备份校验',text)
                self.assertIn('/dlm 上下文状态',text)
                unknown=(await run(p,'/dlm 这条命令不存在'))[0]
                self.assertIn('/dlm 帮助',unknown)
            finally:
                await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))


class CommandSurface(unittest.TestCase):
    """命令面冒烟：只读命令都要有非空回复。重构命令分发时的安全网。"""

    READ_ONLY = ['/dlm 上下文状态', '/dlm 记忆状态', '/dlm 采集状态', '/dlm 待审',
                 '/dlm 事件', '/dlm 时间线 记录', '/dlm 摘要缓存状态', '/dlm 帮助',
                 '/dlm 更新线索', '/dlm 分组建议', '/dlm 备份校验 不存在.bak']

    def test_read_only_commands_all_respond(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            try:
                for text in self.READ_ONLY:
                    reply=(await run(p,text))[0]
                    self.assertIsInstance(reply,str,text)
                    self.assertTrue(reply.strip(),'命令无回复：%s' % text)
            finally:
                await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))

    def test_unknown_command_never_raises(self):
        async def go(root):
            m.get_astrbot_data_path=lambda:root
            p=m.DigitalMemoryStore(None,{'enabled':True,'allowed_sender_ids':'42'})
            try:
                for text in ['/dlm', '/dlm  ', '/dlm 随便写点什么', '/dlm 备份校验']:
                    reply=(await run(p,text))[0]
                    self.assertIsInstance(reply,str,text)
            finally:
                await p.terminate()
        with tempfile.TemporaryDirectory() as root:asyncio.run(go(root))


if __name__=='__main__':unittest.main()
