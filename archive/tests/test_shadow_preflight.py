import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from host_shadow.preflight import check


class TestShadowPreflight(unittest.TestCase):
    def test_detects_missing_plugin(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "data").mkdir()
            (root / "data" / "plugin_data" / "astrbot_plugin_memory_shadow").mkdir(
                parents=True)
            dev = Path(__file__).resolve().parents[1]
            result = check(dev, root)
            self.assertFalse(result["ok"])
            self.assertFalse(result["checks"]["plugin_installed"])

    def test_env_and_install_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            plugin = root / "data" / "plugins" / "astrbot_plugin_memory_shadow"
            plugin.mkdir(parents=True)
            (plugin / "main.py").write_text("", encoding="utf-8")
            (plugin / "metadata.yaml").write_text("name: x\n", encoding="utf-8")
            (root / "data" / "plugin_data" / "astrbot_plugin_memory_shadow").mkdir(
                parents=True)
            dev = Path(__file__).resolve().parents[1]
            with patch.dict(os.environ, {"DIGITAL_LIFE_DEV": str(dev),
                                         "ASTRBOT_ROOT": str(root)}):
                self.assertTrue(check(dev, root)["ok"])


if __name__ == "__main__":
    unittest.main()
