"""Regression for the single version source and the release set contract.

``VERSION`` at the repository root is the only hand-edited version. These tests
cover the derived-field sync, the drift detection used by CI, and the release-set
checks that prevent mixing versions or artifacts.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent
ROOT = DEV.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DEV))

from tools import project_version  # noqa: E402
from tools.repo_packaging import PackagingError  # noqa: E402
from tools.repo_packaging import read_version as read_packaging_version  # noqa: E402

METADATA = "dev/astrbot_plugin_digital_life/metadata.yaml"


def make_repo(root: Path, version: str = "0.8.0-dev.1",
              metadata_version: str | None = None) -> Path:
    (root / "VERSION").write_text(version + "\n", encoding="utf-8", newline="\n")
    metadata = root / METADATA
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(
        "name: astrbot_plugin_digital_life\n"
        "display_name: demo\n"
        "version: %s\n"
        "repo: https://example.invalid\n" % (
            version if metadata_version is None else metadata_version),
        encoding="utf-8", newline="\n")
    return root


class VersionSourceTests(unittest.TestCase):
    def test_reads_the_declared_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            self.assertEqual(project_version.read_version(root), "0.8.0-dev.1")
            self.assertEqual(project_version.release_level("0.8.0-dev.1"), "dev")

    def test_release_levels(self):
        self.assertEqual(project_version.release_level("0.8.0-rc.2"), "rc")
        self.assertEqual(project_version.release_level("0.8.0"), "stable")
        self.assertEqual(project_version.release_level("1.0.19"), "stable")

    def test_missing_empty_and_malformed_versions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(project_version.VersionError):
                project_version.read_version(root)
            (root / "VERSION").write_text("\n", encoding="utf-8")
            with self.assertRaises(project_version.VersionError):
                project_version.read_version(root)
            for bad in ("0.8", "v0.8.0", "0.8.0-beta.1", "0.8.0-dev", "abc"):
                (root / "VERSION").write_text(bad + "\n", encoding="utf-8")
                with self.assertRaises(project_version.VersionError, msg=bad):
                    project_version.read_version(root)

    def test_metadata_drift_is_reported_and_synced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory), "0.8.0-dev.1", "0.7.0-dev.3")
            problems = project_version.derived_mismatches(root, METADATA)
            self.assertEqual(len(problems), 1)
            self.assertEqual(problems[0]["expected"], "0.8.0-dev.1")
            self.assertEqual(problems[0]["found"], "0.7.0-dev.3")
            self.assertFalse(project_version.check(root, METADATA)["ok"])

            changed = project_version.sync(root, METADATA)
            self.assertEqual(changed, [METADATA])
            self.assertEqual(project_version.check(root, METADATA)["ok"], True)
            self.assertEqual(project_version.sync(root, METADATA), [])

    def test_sync_keeps_the_other_metadata_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory), "0.8.0-dev.1", "0.7.0-dev.3")
            project_version.sync(root, METADATA)
            text = (root / METADATA).read_text(encoding="utf-8")
            self.assertIn("name: astrbot_plugin_digital_life", text)
            self.assertIn("version: 0.8.0-dev.1", text)
            self.assertNotIn("0.7.0-dev.3", text)

    def test_packaging_refuses_a_stale_metadata_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory), "0.8.0-dev.1", "0.7.0-dev.3")
            spec = {"plugin_dir": "dev/astrbot_plugin_digital_life"}
            with self.assertRaises(PackagingError) as caught:
                read_packaging_version(root, spec)
            self.assertIn("sync_version", str(caught.exception))

    def test_packaging_accepts_a_synced_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            spec = {"plugin_dir": "dev/astrbot_plugin_digital_life"}
            self.assertEqual(read_packaging_version(root, spec), "0.8.0-dev.1")


class ReleaseSetTests(unittest.TestCase):
    """The release directory must contain one version, fully and consistently."""

    def test_release_set_is_complete_and_consistent(self):
        from tools.check_release_set import check_release_set

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_repo(root)
            result = check_release_set(root, "dist/release", "0.8.0-dev.1")
            self.assertFalse(result["ok"])
            self.assertTrue(any("missing" in item["problem"].lower()
                                for item in result["problems"]))

    def test_unknown_files_in_the_release_set_are_reported(self):
        from tools.check_release_set import check_release_set

        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            release = root / "dist" / "release"
            release.mkdir(parents=True)
            (release / "notes-0.7.0-dev.2.txt").write_text("old\n", encoding="utf-8")
            result = check_release_set(root, "dist/release", "0.8.0-dev.1")
            self.assertTrue(any("0.7.0-dev.2" in item.get("file", "")
                                for item in result["problems"]))


if __name__ == "__main__":
    unittest.main()
