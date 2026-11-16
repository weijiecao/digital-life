import tempfile, json
from datetime import datetime, timezone
from astrbot_plugin_digital_memory_store.storage import Store
from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
from test_store import SemanticFixtureEncoder

with tempfile.TemporaryDirectory() as d:
    st = Store(d, 'fixture', SemanticFixtureEncoder)
    st.automatic_memory_enabled = True
    st.model_dir = 'fixture'
    scope = st.scope('u1','private')
    st.capture(scope,'m1','原计划周六去书店', recorded_at='2025-01-01T10:00:00+00:00')
    st.capture(scope,'m2','改到周日去书店', recorded_at='2025-01-03T10:00:00+00:00')
    process_jobs(st.flow.db)
    st.sync_auto_source_vectors(); st.sync_auto_topics()
    print('topics rows:')
    for r in st.flow.db.execute('SELECT id,title,status FROM dl_auto_topics'):
        print('  ', tuple(r))
    print('members:')
    for r in st.flow.db.execute('SELECT topic_id,seq,relation FROM dl_auto_topic_members'):
        print('  ', tuple(r))
    stamp='2025-02-01T00:00:00+00:00'
    for q in ('书店计划怎么样了','原计划周六去书店怎么安排的'):
        pkt = st._auto_topic_context(scope,q,stamp,450,None,set())
        print(repr(q),'->',len(pkt),'chars'); print(pkt[:500] if pkt else '(empty)')
    st.close()
