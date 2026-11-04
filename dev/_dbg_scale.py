import sys
import tempfile
import time

sys.path.insert(0, 'evaluation')
sys.path.insert(0, '.')
from m2_runner import _factory, configure
from astrbot_plugin_digital_memory_store.storage import Store
from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
from scale_bench import build_corpus, SCOPES

MODEL = r'..\testbed\models\bge-small-zh-v1.5'
rows = build_corpus(500)
per = 500 // SCOPES if False else 25
d = tempfile.mkdtemp(prefix='dbg10-')
s = Store(d, MODEL, _factory(MODEL))
configure(s, MODEL)
for index in range(500):
    scope_index = index // 25
    s.capture(s.scope(str(scope_index), 'scale'), 'm%d' % index,
              rows[index][2], recorded_at=rows[index][3])
process_jobs(s.flow.db)
print('topics', s.flow.db.execute('SELECT COUNT(*) FROM dl_auto_topic_members').fetchone()[0])
i = 395
sc = s.scope(str(i // 25), 'scale')
ent = rows[i][2].split('关于', 1)[1].split('的安排', 1)[0]
q = ent + '那个安排怎么样了？'
print('cands', len(s.pending_index.candidates(sc, s._character_pairs(q))))
started = time.perf_counter()
try:
    key = s._index().model_key
    print('index', key, round(time.perf_counter() - started, 1), 's')
except Exception as exc:
    print('index error', repr(exc))
try:
    pkt = s._pending_context(sc, q, '2099-01-01T00:00:00+00:00', 450, 'q', set())
    print('pending len', len(pkt))
except Exception as exc:
    print('pending error', repr(exc))
print('context', len(s.context_packet(sc, q, budget=450, current_message_id='q')))
s.close()
