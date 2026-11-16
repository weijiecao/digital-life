import tempfile
from astrbot_plugin_digital_memory_store.storage import Store
from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
from test_store import SemanticFixtureEncoder

d = tempfile.mkdtemp()
st = Store(d, 'fixture', SemanticFixtureEncoder)
try:
    st.automatic_memory_enabled = True; st.model_dir = 'fixture'
    scope = st.scope('u1','private')
    st.capture(scope,'m1','原计划周六去书店', recorded_at='2025-01-01T10:00:00+00:00')
    st.capture(scope,'m2','改到周日去书店', recorded_at='2025-01-03T10:00:00+00:00')
    process_jobs(st.flow.db); st.sync_auto_source_vectors(); st.sync_auto_topics()
    print('topics:', [tuple(r) for r in st.flow.db.execute('SELECT scope,topic_id FROM dl_auto_topics')])
    print('members:', [tuple(r) for r in st.flow.db.execute('SELECT seq,topic_id,basis FROM dl_auto_topic_members')])
    stamp='2025-02-01T00:00:00+00:00'
    for q in ('书店计划怎么样了','原计划周六去书店怎么安排的','登山计划怎么样了'):
        pkt = st._auto_topic_context(scope,q,stamp,450,None,set())
        print(repr(q),'->',len(pkt),'chars:', pkt[:300] if pkt else '(empty)')
finally:
    st.close()
