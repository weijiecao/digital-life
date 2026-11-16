"""Negative tests for the export tool: manifest tampering and local drift.

These mirror the two gaps found in the 0.8.0 plan (EXP-01/EXP-02) and add path
safety, layout switching and the formal-export cleanliness gate.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

DEV = Path(__file__).resolve().parent.parent
ROOT = DEV.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DEV))

from tools import export_astrbot_plugin as exporter  # noqa: E402
from tools.repo_packaging import PackagingError  # noqa: E402


class ExportFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.export = Path(self.directory.name) / "export"

    def tearDown(self):
        self.directory.cleanup()

    def manifest_path(self, output: Path = None) -> Path:
        return (output or self.export) / "SOURCE_MANIFEST.json"

    def read_manifest(self, output: Path = None) -> dict:
        return json.loads(self.manifest_path(output).read_text(encoding="utf-8"))

    def write_manifest(self, value: dict, output: Path = None) -> None:
        self.manifest_path(output).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")

    def managed_file(self) -> Path:
        files = self.read_manifest()["files"]
        return self.export / files[0]["path"]

    def test_clean_export_passes_check(self):
        exporter.export(self.export, "archive")
        result = exporter.check(self.export, "archive")
        self.assertTrue(result["ok"], result["problems"])

    def test_tampered_dirty_field_is_rejected(self):
        exporter.export(self.export, "archive")
        manifest = self.read_manifest()
        manifest["dirty"] = not manifest["dirty"]
        self.write_manifest(manifest)
        result = exporter.check(self.export, "archive")
        self.assertFalse(result["ok"])
        self.assertIn("dirty", [item["field"] for item in result["problems"]["fields"]])

    def test_tampered_layout_is_rejected(self):
        exporter.export(self.export, "archive")
        manifest = self.read_manifest()
        manifest["layout"] = "flat"
        self.write_manifest(manifest)
        result = exporter.check(self.export, "archive")
        self.assertFalse(result["ok"])
        fields = [item["field"] for item in result["problems"]["fields"]]
        self.assertIn("layout", fields)

    def test_tampered_archive_identity_is_rejected(self):
        exporter.export(self.export, "archive")
        manifest = self.read_manifest()
        manifest["archive"]["sha256"] = "0" * 64
        self.write_manifest(manifest)
        result = exporter.check(self.export, "archive")
        self.assertFalse(result["ok"])
        self.assertIn("archive",
                      [item["field"] for item in result["problems"]["fields"]])

    def test_local_modification_is_not_overwritten_without_force(self):
        exporter.export(self.export, "archive")
        target = self.managed_file()
        target.write_text("local edit\n", encoding="utf-8")
        with self.assertRaises(PackagingError) as caught:
            exporter.export(self.export, "archive")
        self.assertIn("changed since the last export", str(caught.exception))
        self.assertEqual(target.read_text(encoding="utf-8"), "local edit\n")
        exporter.export(self.export, "archive", force=True)
        self.assertNotEqual(target.read_text(encoding="utf-8"), "local edit\n")
        self.assertTrue(exporter.check(self.export, "archive")["ok"])

    def test_unknown_file_blocks_export(self):
        exporter.export(self.export, "archive")
        (self.export / "notes-from-maintainer.md").write_text("keep\n",
                                                              encoding="utf-8")
        with self.assertRaises(PackagingError):
            exporter.export(self.export, "archive")
        self.assertTrue((self.export / "notes-from-maintainer.md").is_file())

    def test_manifest_path_traversal_is_refused(self):
        self.export.mkdir()
        outside = Path(self.directory.name) / "escape.txt"
        outside.write_text("do not touch\n", encoding="utf-8")
        self.write_manifest({
            "schema": exporter.SCHEMA,
            "files": [{"path": "../escape.txt", "sha256": "0" * 64}],
        })
        with self.assertRaises(PackagingError) as caught:
            exporter.export(self.export, "archive")
        self.assertIn("escapes the export", str(caught.exception))
        self.assertEqual(outside.read_text(encoding="utf-8"), "do not touch\n")

    def test_layout_switch_is_detected(self):
        exporter.export(self.export, "archive")
        result = exporter.check(self.export, "engineering")
        self.assertFalse(result["ok"])
        self.assertTrue(result["problems"]["missing"])

    def test_formal_export_requires_a_clean_commit(self):
        from tools.repo_packaging import git_state as real_state
        clean = {"commit": "0" * 40, "dirty": False, "reason": ""}
        dirty = {"commit": "0" * 40, "dirty": True, "reason": ""}
        missing = {"commit": None, "dirty": None, "reason": "no .git"}
        with patch.object(exporter, "git_state", lambda root: clean):
            exporter.export(self.export, "archive", require_clean=True)
        for state in (dirty, missing):
            with patch.object(exporter, "git_state", lambda root, s=state: s):
                with self.assertRaises(PackagingError):
                    exporter.export(self.export, "archive", require_clean=True)
        self.assertIsNotNone(real_state)


class CompareDistributionTests(unittest.TestCase):
    def test_classifies_managed_and_unmanaged_files(self):
        from tools.compare_distribution import compare

        with tempfile.TemporaryDirectory() as directory:
            export = Path(directory) / "export"
            repository = Path(directory) / "repo"
            exporter.export(export, "engineering")
            shutil.copytree(export, repository)
            manifest = json.loads(
                (export / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
            managed = [item["path"] for item in manifest["files"]]
            # identical copy
            (repository / "LICENSE").write_text("MIT\n", encoding="utf-8")
            # unmanaged history that must not be reported as clutter
            (repository / "tests").mkdir()
            (repository / "tests" / "test_old.py").write_text("x = 1\n",
                                                             encoding="utf-8")
            # change one managed file, remove another
            changed = repository / managed[0]
            changed.write_bytes(changed.read_bytes() + b"\nedit\n")
            (repository / managed[1]).unlink()

            result = compare(export, repository)
            self.assertTrue(result["ok"])
            self.assertEqual(result["managed"], len(managed))
            self.assertIn(managed[0], result["changed"])
            self.assertIn(managed[1], result["missing_in_repository"])
            self.assertGreaterEqual(result["unmanaged"], 2)
            self.assertIn("tests", result["unmanaged_by_group"])


class SymlinkSafetyTests(unittest.TestCase):
    """A link anywhere in the managed path must be refused, with or without --force."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.export = self.base / "export"
        exporter.export(self.export, "archive")

    def tearDown(self):
        self.directory.cleanup()

    def _link(self, target: Path, link: Path, directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError):
            if sys.platform == "win32" and directory:
                # Windows file symlinks need elevation, directory junctions do not.
                completed = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                    capture_output=True, text=True)
                if completed.returncode == 0:
                    return
            if not directory:
                # Hard links need no privilege and are also an escape vector.
                try:
                    os.link(target, link)
                    return
                except OSError:
                    pass
            self.skipTest("links are unavailable on this host")

    def test_manifest_replaced_by_a_link_is_refused(self):
        outside = self.base / "outside-manifest.json"
        manifest = self.export / "SOURCE_MANIFEST.json"
        outside.write_bytes(manifest.read_bytes())
        manifest.unlink()
        self._link(outside, manifest)
        with self.assertRaises(PackagingError) as caught:
            exporter.export(self.export, "archive")
        self.assertIn("link", str(caught.exception))
        self.assertEqual(outside.read_bytes(),
                         (self.export / "SOURCE_MANIFEST.json").read_bytes())

    def test_linked_managed_directory_is_refused_even_with_force(self):
        plugin = self.export / "astrbot_plugin_digital_life"
        outside = self.base / "outside-plugin"
        shutil.copytree(plugin, outside)
        shutil.rmtree(plugin)
        self._link(outside, plugin, directory=True)
        with self.assertRaises(PackagingError) as caught:
            exporter.export(self.export, "archive", force=True)
        self.assertIn("link", str(caught.exception))

    def test_empty_manifest_with_linked_directory_is_refused(self):
        target = self.base / "empty-export"
        target.mkdir()
        outside = self.base / "outside"
        outside.mkdir()
        (target / "SOURCE_MANIFEST.json").write_text(json.dumps({
            "schema": exporter.SCHEMA,
            "files": [],
        }), encoding="utf-8")
        self._link(outside, target / "shared", directory=True)
        with self.assertRaises(PackagingError) as caught:
            exporter.export(target, "engineering", force=True)
        self.assertIn("link", str(caught.exception))
        self.assertEqual(list(outside.iterdir()), [])

    def test_malformed_manifest_is_refused(self):
        (self.export / "SOURCE_MANIFEST.json").write_text(json.dumps({
            "schema": exporter.SCHEMA,
            "files": [{"path": "a.txt"}],
        }), encoding="utf-8")
        with self.assertRaises(PackagingError):
            exporter.export(self.export, "archive", force=True)


if __name__ == "__main__":
    unittest.main()
