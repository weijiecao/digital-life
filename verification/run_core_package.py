"""Run the 137-case core package suite against an extracted plugin package.

The test sources are the 13 files listed in the 0.8.0 plan, taken from
``dev/tests/`` so there is only one maintained copy. They are copied into a
temporary ``tests/`` package together with the extracted candidate package on
``sys.path``, so imports resolve to the shipped byte-for-byte package rather than
to the source tree.

    python verification/run_core_package.py --package <extracted_plugin_dir>
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEV_TESTS = REPO_ROOT / "dev" / "tests"
PACKAGES = ("astrbot_memory_input", "dlm_console", "memory_engine",
            "memory_output", "v1_5_consolidator")
CORE_TEST_FILES = (
    "test_astrbot_memory_input.py",
    "test_memory_engine_backup.py",
    "test_memory_engine_cli.py",
    "test_memory_engine_engine.py",
    "test_memory_engine_host.py",
    "test_memory_engine_host_budget.py",
    "test_memory_engine_rejudge.py",
    "test_memory_engine_render.py",
    "test_memory_engine_summary.py",
    "test_memory_engine_token_budget.py",
    "test_memory_engine_worker.py",
    "test_memory_output.py",
    "test_native_input_host.py",
)


def _package_dir() -> Path:
    value = os.environ.get("DIGITAL_LIFE_PACKAGE") or (
        sys.argv[1] if len(sys.argv) > 1 else "")
    if not value:
        raise SystemExit("set DIGITAL_LIFE_PACKAGE to the extracted plugin directory")
    path = Path(value).resolve()
    if not (path / "main.py").is_file():
        raise SystemExit("not an extracted plugin package: %s" % path)
    return path


def _check_module_origins(package: Path) -> list[str]:
    """Every loaded business module must come from the extracted package."""
    problems: list[str] = []
    for name in PACKAGES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            problems.append("%s: %s: %s" % (name, type(exc).__name__, exc))
    importlib.import_module("astrbot_plugin_digital_life")
    root = str(package.resolve()) + os.sep
    for name, module in sorted(sys.modules.items()):
        top = name.split(".")[0]
        if top not in (*PACKAGES, "astrbot_plugin_digital_life"):
            continue
        origin = getattr(module, "__file__", None)
        if origin and not str(Path(origin).resolve()).startswith(root):
            problems.append("%s loaded from %s" % (name, origin))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", default=None,
                        help="extracted plugin directory (or DIGITAL_LIFE_PACKAGE)")
    args = parser.parse_args(argv)
    package = Path(args.package).resolve() if args.package else _package_dir()
    missing = [name for name in CORE_TEST_FILES if not (DEV_TESTS / name).is_file()]
    if missing:
        print("missing core test sources: %s" % missing, file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="dl-core-package-") as temp:
        root = Path(temp)
        tests = root / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("", encoding="utf-8")
        for name in CORE_TEST_FILES:
            shutil.copy2(DEV_TESTS / name, tests / name)
        sys.path[:0] = [str(root), str(package.parent), str(package)]
        problems = _check_module_origins(package)
        if problems:
            print("module origin check failed:", *problems, sep="\n  ",
                  file=sys.stderr)
            return 3
        suite = unittest.TestLoader().discover(str(tests), top_level_dir=str(root))
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
        if result.testsRun == 0:
            print("core suite selected zero tests", file=sys.stderr)
            return 4
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
