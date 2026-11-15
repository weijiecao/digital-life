"""把独立控制台的前端资源同步到 AstrBot 插件视图。

`dlm_console/web` 是唯一源；`host_shadow/views/console` 是插件页副本。
修改前端后运行：

    cd dev
    python scripts\\sync_console_view.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dlm_console" / "web"
TARGET = ROOT / "host_shadow" / "views" / "console"
FILES = ("index.html", "app.js", "style.css", "favicon.svg")


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        source = SOURCE / name
        if not source.is_file():
            raise SystemExit("missing console asset: %s" % source)
        shutil.copyfile(source, TARGET / name)
        print("synced", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
