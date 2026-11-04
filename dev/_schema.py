import sys
import tempfile

sys.path.insert(0, '.')
from astrbot_plugin_digital_memory_store.storage import Store


class Enc:
    model_key = 'x'


store = Store(tempfile.mkdtemp(), '', Enc)
print([row[1] for row in store.flow.db.execute('PRAGMA table_info(dl_capture)')])
print([row[0] for row in store.flow.db.execute(
    "SELECT sql FROM sqlite_master WHERE type IN ('index','table') AND tbl_name='dl_capture'")])
store.close()
