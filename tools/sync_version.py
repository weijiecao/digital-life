"""Synchronise derived version fields from the root ``VERSION`` file.

Developers run this after editing ``VERSION``; CI and release builds only run the
read-only check, so a clean tree is never modified during a build::

    python tools/sync_version.py           # write metadata.yaml
    python tools/sync_version.py --check   # CI: fail on any drift
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.project_version import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
