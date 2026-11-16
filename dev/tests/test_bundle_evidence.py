"""Evidence in the engineering bundle must describe the bundle's own build.

0.8.0-dev.2 shipped a stale ``EVIDENCE/release_set.json`` that failed while
checking the previous version. These tests pin the rules: only reports bound to
the same commit/version/artifact are packaged, a mismatch is an error, and the
release-set report is never taken from ``dist`` because it is produced later.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent
ROOT = DEV.parent
sys.path.insert(0, str(ROOT))

from tools import build_engineering_bundle as bundle  # noqa: E402
from tools.repo_packaging import PackagingError  # noqa: E402

COMMIT = "a" * 40
VERSION = "0.8.0-dev.9"


def build_manifest(**overrides):
    value = {"plugin_version": VERSION, "source_commit": COMMIT, "dirty": False,
             "archive_sha256": "b" * 64, "archive": "plugin.zip", "file_count": 3}
    value.update(overrides)
    return value


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.dist = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def write(self, name, value):
        (self.dist / name).write_text(json.dumps(value), encoding="utf-8")

    def write_all(self):
        for name, value in {
            "regression.json": {"tested_commit": COMMIT, "dirty": False,
                                "total": 3, "passed": 3, "ok": True},
            "package_regression.json": {"tested_commit": COMMIT, "dirty": False,
                                        "artifact_sha256": "b" * 64,
                                        "suites": [], "ok": True},
            "package_verification.json": {"source_commit": COMMIT, "dirty": False,
                                          "plugin_version": VERSION, "ok": True,
                                          "checks": [{"id": "x"}]},
            "selftest.json": {"source_commit": COMMIT, "dirty": False, "ok": True},
            "astrbot_install_check.json": {"source_commit": COMMIT, "dirty": False,
                                           "ok": True},
            "ui_dom.json": {"source_commit": COMMIT, "dirty": False,
                            "artifact_sha256": "b" * 64, "ok": True,
                            "operations": ["backup"],
                            "visual_browser_validation": False},
        }.items():
            self.write(name, value)

    def test_bound_reports_are_included(self):
        self.write_all()
        included = bundle._bind_evidence(self.dist, build_manifest())
        self.assertEqual(sorted(included), sorted(bundle.BOUND_REPORTS))

    def test_stale_commit_is_refused(self):
        self.write_all()
        self.write("regression.json", {"tested_commit": "c" * 40, "dirty": False})
        with self.assertRaises(PackagingError) as caught:
            bundle._bind_evidence(self.dist, build_manifest())
        self.assertIn("stale evidence", str(caught.exception))
        self.assertIn("regression.json", str(caught.exception))

    def test_wrong_artifact_is_refused(self):
        self.write_all()
        self.write("package_regression.json", {"tested_commit": COMMIT,
                                               "dirty": False,
                                               "artifact_sha256": "d" * 64,
                                               "suites": []})
        with self.assertRaises(PackagingError):
            bundle._bind_evidence(self.dist, build_manifest())

    def test_dirty_report_is_refused(self):
        self.write_all()
        self.write("ui_dom.json", {"source_commit": COMMIT, "dirty": True,
                                   "artifact_sha256": "b" * 64, "ok": True})
        with self.assertRaises(PackagingError):
            bundle._bind_evidence(self.dist, build_manifest())

    def test_release_set_is_never_included(self):
        self.write_all()
        self.write("release_set.json", {"ok": False, "version": "0.8.0-dev.1",
                                        "problems": [{"problem": "missing"}]})
        included = bundle._bind_evidence(self.dist, build_manifest())
        self.assertNotIn("release_set.json", included)

    def test_missing_commit_cannot_bind_evidence(self):
        self.write_all()
        with self.assertRaises(PackagingError):
            bundle._bind_evidence(self.dist, build_manifest(source_commit=None))


if __name__ == "__main__":
    unittest.main()
