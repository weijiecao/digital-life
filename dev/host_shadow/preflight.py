"""Preflight for installing/running the Memory Engine shadow plugin."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def check(dev: Path, root: Path) -> dict:
    plugin = root / "data" / "plugins" / "astrbot_plugin_memory_shadow"
    data = root / "data" / "plugin_data" / "astrbot_plugin_memory_shadow"
    mode = (os.environ.get("SHADOW_OUTPUT_MODE", "off") or "off").strip().lower()
    checks = {
        "dev_dir": (dev / "memory_engine").is_dir(),
        "host_shadow_main": (dev / "host_shadow" / "main.py").is_file(),
        "astrbot_root": (root / "data").is_dir(),
        "plugin_installed": (plugin / "main.py").is_file(),
        "plugin_metadata": (plugin / "metadata.yaml").is_file(),
        "plugin_data_dir": data.is_dir(),
        "digital_life_dev_env": bool(os.environ.get("DIGITAL_LIFE_DEV")),
        "astrbot_root_env": bool(os.environ.get("ASTRBOT_ROOT")),
        "output_module": ((dev / "memory_output").is_dir()
                          and (dev / "memory_engine" / "output.py").is_file()),
        "output_mode_valid": mode in ("", "off", "inject", "on", "true", "1", "yes"),
    }
    # 输出模块只在显式开启注入时是硬性要求；默认只观察时不阻断安装。
    required = {key: value for key, value in checks.items() if key != "output_module"}
    if mode in ("inject", "on", "true", "1", "yes"):
        required["output_module"] = checks["output_module"]
    return {"ok": all(required.values()), "checks": checks, "output_mode": mode,
            "plugin": str(plugin), "data": str(data)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", default=os.environ.get("DIGITAL_LIFE_DEV", ""))
    parser.add_argument("--root", default=os.environ.get("ASTRBOT_ROOT", ""))
    args = parser.parse_args()
    result = check(Path(args.dev).resolve(), Path(args.root).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
