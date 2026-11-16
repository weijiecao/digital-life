import json, tempfile
from astrbot_plugin_digital_memory_store.storage import Store
from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
from test_store import SemanticFixtureEncoder
from datetime import datetime, timezone

with tempfile.TemporaryDirectory() as d:
    st = Store(d, 'fixture', SemanticFixtureEncoder)
    st.automatic_memory_enabled = True
    st.model_dir = 'fixture'
    scope = st.scope('u1','private')
    st.capture(scope,'m1','原计划周六去书店', recorded_at='2025-01-01T10:00:00+00:00')
    st.capture(scope,'m2','改到周日去书店', recorded_at='2025-01-03T10:00:00+00:00')
    st.capture(scope,'m3','计划一月去登山', recorded_at='2025-01-04T10:00:00+00:00')
    n = process_jobs(st.flow.db)
    print('jobs processed:', n)
    print('vectors:', st.sync_auto_source_vectors())
    print('topics:', st.sync_auto_topics())
    print('groups:', st.flow.db.execute('SELECT COUNT(*) FROM dl_auto_groups').fetchone()[0],
          'topics:', st.flow.db.execute('SELECT COUNT(*) FROM dl_auto_topics').fetchone()[0])
    packet = st.context_packet(scope,'书店计划怎么样了', budget=450,
                               as_of=datetime(2025,2,1,tzinfo=timezone.utc))
    print('packet chars:', len(packet))
    print(packet[:600] if packet else '(empty)')
    st.close()
