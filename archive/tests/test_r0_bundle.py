import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CANDIDATES = [
    HERE.parent / "releases" / "memory-engine-r0-rc1.zip",
    HERE.parent / "_publish" / "digital-life" / "releases" / "memory-engine-r0-rc1.zip",
]
ZIP = next((path for path in CANDIDATES if path.is_file()), None)


@unittest.skipIf(ZIP is None, "R0 bundle not present")
class TestR0Bundle(unittest.TestCase):
    def test_bundle_hash_and_runtime(self):
        manifest = json.loads((ZIP.parent / "memory-engine-r0-rc1.json").read_text(
            encoding="utf-8"))
        self.assertEqual(hashlib.sha256(ZIP.read_bytes()).hexdigest(),
                         manifest["sha256"])
        self.assertEqual(ZIP.stat().st_size, manifest["bytes"])
        with tempfile.TemporaryDirectory() as d:
            with zipfile.ZipFile(ZIP) as bundle:
                bundle.extractall(d)
                self.assertEqual(len(bundle.namelist()), manifest["files"])
            for rel, digest in manifest.get("file_sha256", {}).items():
                self.assertEqual(
                    hashlib.sha256((Path(d) / rel).read_bytes()).hexdigest(),
                    digest, rel)
            code = """
import sys
from memory_engine.engine import MemoryEngine
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store
s = V15Store(sys.argv[1] + '/v15.sqlite3')
e = MemoryEngine(s)
e.ingest('s', SourceEnvelope('s', 1, 'sha', 't', '茶'))
assert len(e.retrieve('s', '茶')) == 1
assert s.integrity_check() == 'ok'
s.close()
"""
            proc = subprocess.run(
                [sys.executable, "-c", code, d], cwd=d,
                env={**os.environ, "PYTHONPATH": d},
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
