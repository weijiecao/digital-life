"""Regression for the acceptance record and evidence index.

The 0.8.0-dev.1 audit found that the recorder accepted unknown commits, wrong
versions and dirty trees, and that the evidence index recorded a summary hash
from before the summary was rewritten. These tests pin the strict behaviour and
the re-verification entrypoint.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent
ROOT = DEV.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DEV))

from tools import record_acceptance as recorder  # noqa: E402

COMMIT = "a" * 40
VERSION = "0.8.0-dev.1"


def build_part(**overrides):
    value = {
        "schema": "digital-life-plugin-build/1",
        "plugin_version": VERSION,
        "source_commit": COMMIT,
        "dirty": False,
        "archive": "astrbot_plugin_digital_life-%s.zip" % VERSION,
        "archive_sha256": "b" * 64,
        "archive_bytes": 100,
        "file_count": 79,
        "files": [],
    }
    value.update(overrides)
    return value


def regression_part(**overrides):
    value = {
        "suite": "source-regression",
        "tested_commit": COMMIT,
        "dirty": False,
        "total": 386,
        "passed": 386,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "ok": True,
    }
    value.update(overrides)
    return value


def package_part(**overrides):
    value = {
        "tested_commit": COMMIT,
        "dirty": False,
        "artifact_sha256": "b" * 64,
        "suites": [
            {"id": "completion", "ok": True, "status": "passed", "total": 47},
            {"id": "core", "ok": True, "status": "passed", "total": 137},
            {"id": "legacy", "ok": True, "status": "passed", "total": 20},
        ],
        "ok": True,
    }
    value.update(overrides)
    return value


def verification_part(**overrides):
    value = {
        "ok": True,
        "source_commit": COMMIT,
        "dirty": False,
        "plugin_version": VERSION,
        "host": "stub",
        "checks": [{"id": "file-count", "status": "pass"}],
    }
    value.update(overrides)
    return value


def export_part(**overrides):
    value = {
        "source_commit": COMMIT,
        "dirty": False,
        "plugin_version": VERSION,
        "layout": "engineering",
        "file_count": 79,
        "archive": {"sha256": "b" * 64},
    }
    value.update(overrides)
    return value


def parts(**overrides):
    base = {
        "build": build_part(),
        "regression": regression_part(),
        "package": package_part(),
        "verification": verification_part(),
        "selftest": {"ok": True},
        "install": {"ok": True},
        "ui_dom": None,
        "export": export_part(),
    }
    base.update(overrides)
    return base


class StrictConsistencyTests(unittest.TestCase):
    def test_complete_set_is_consistent(self):
        problems = recorder.consistency(parts(), formal=False)
        # The version file on disk must also agree; that is the only allowed
        # complaint, and it must not be about the evidence itself.
        self.assertFalse([item for item in problems
                          if "missing" in item or "differ" in item
                          or "dirty" in item or "zero" in item], problems)

    def test_unknown_commit_is_a_problem_in_formal_mode(self):
        problems = recorder.consistency(
            parts(regression=regression_part(tested_commit=None)), formal=True)
        self.assertTrue(any("regression tested" in item for item in problems),
                        problems)

    def test_dirty_build_is_a_problem(self):
        problems = recorder.consistency(parts(build=build_part(dirty=True)),
                                       formal=True)
        self.assertTrue(any("dirty" in item for item in problems), problems)

    def test_package_on_another_artifact_is_a_problem(self):
        problems = recorder.consistency(
            parts(package=package_part(artifact_sha256="c" * 64)), formal=True)
        self.assertTrue(any("package suites ran on" in item for item in problems),
                        problems)

    def test_verification_for_another_commit_is_a_problem(self):
        problems = recorder.consistency(
            parts(verification=verification_part(source_commit="d" * 40)),
            formal=True)
        self.assertTrue(any("verification is for" in item for item in problems),
                        problems)

    def test_missing_package_suite_is_a_problem(self):
        package = package_part(suites=[{"id": "completion", "ok": True,
                                        "status": "passed", "total": 47}])
        problems = recorder.consistency(parts(package=package), formal=True)
        self.assertTrue(any("package suites missing" in item for item in problems),
                        problems)

    def test_zero_case_suite_is_a_problem(self):
        package = package_part(suites=[
            {"id": "completion", "ok": True, "status": "passed", "total": 0},
            {"id": "core", "ok": True, "status": "passed", "total": 137},
            {"id": "legacy", "ok": True, "status": "passed", "total": 20}])
        problems = recorder.consistency(parts(package=package), formal=True)
        self.assertTrue(any("zero tests" in item for item in problems), problems)

    def test_dom_report_cannot_claim_browser_validation(self):
        dom = {"ok": True, "artifact_sha256": "b" * 64, "source_commit": COMMIT,
               "dirty": False, "operations": ["backup"],
               "dom_errors": [], "visual_browser_validation": True}
        problems = recorder.consistency(parts(ui_dom=dom), formal=True)
        self.assertTrue(any("browser" in item for item in problems), problems)


class EvidenceTests(unittest.TestCase):
    def test_evidence_index_verifies_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory) / "dist"
            dist.mkdir()
            (dist / "regression.json").write_text('{"ok": true}', encoding="utf-8")
            record = {"tested_commit": COMMIT, "artifact_sha256": "b" * 64,
                      "plugin_version": VERSION}
            target = recorder.persist_evidence(record, dist,
                                               Path(directory) / "evidence")
            result = recorder.check_evidence(target)
            self.assertTrue(result["ok"], result["problems"])
            summary = target / "summary.json"
            data = json.loads(summary.read_text(encoding="utf-8"))
            data["artifact_sha256"] = "c" * 64
            summary.write_text(json.dumps(data), encoding="utf-8")
            result = recorder.check_evidence(target)
            self.assertFalse(result["ok"])
            self.assertTrue(any("summary.json" in item for item in result["problems"]))

    def test_evidence_index_detects_missing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory) / "dist"
            dist.mkdir()
            (dist / "regression.json").write_text('{"ok": true}', encoding="utf-8")
            target = recorder.persist_evidence(
                {"tested_commit": COMMIT, "artifact_sha256": "b" * 64},
                dist, Path(directory) / "evidence")
            (target / "regression.json").unlink()
            result = recorder.check_evidence(target)
            self.assertFalse(result["ok"])


class PreviousDeliveryTests(unittest.TestCase):
    def test_current_artifact_is_not_reported_as_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            releases = Path(directory)
            current = releases / ("astrbot_plugin_digital_life-%s.zip" % VERSION)
            current.write_bytes(b"current")
            from tools.repo_packaging import sha256_file
            digest = sha256_file(current)
            build = {"plugin_version": VERSION, "archive_sha256": digest}
            self.assertIsNone(recorder._previous(build, releases))

            older = releases / "astrbot_plugin_digital_life-0.7.0-dev.2.zip"
            older.write_bytes(b"older")
            selected = recorder._previous(build, releases)
            self.assertIsNotNone(selected)
            self.assertEqual(selected.name, older.name)


if __name__ == "__main__":
    unittest.main()
