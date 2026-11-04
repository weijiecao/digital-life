"""崩溃演练的子进程：持续写库，由父进程在写库中途强杀。"""
import os
import sys
import time
from pathlib import Path

DEV_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEV_ROOT))
from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402


class Enc:
    model_key = 'crash-no-model'

    def __init__(self, path):
        pass

    def encode_documents(self, texts):
        raise RuntimeError('no model')

    def encode_queries(self, texts):
        raise RuntimeError('no model')


store = Store(os.environ['DLM_ROOT'], '', Enc)
scope = store.scope('1', 'crash')
index = 0
while True:
    index += 1
    store.capture(scope, 'm%d' % index, '关于编号%d的安排定在周五下午' % index,
                  recorded_at='2025-01-01T10:%02d:00+00:00' % (index % 60))
    with open(os.environ['DLM_MARKER'], 'w', encoding='utf-8') as handle:
        handle.write(str(index))
    time.sleep(0.01)
