"""host_shadow 插件页控制台：路由注册、复用 Console、静态资源。"""

import os
import tempfile
import unittest
from pathlib import Path

from tests.test_host_shadow_native import load_host_shadow

MODULE = None


def setUpModule():
    global MODULE
    MODULE = load_host_shadow()


class FakeContext:
    def __init__(self):
        self.routes = []

    def register_web_api(self, route, handler, methods, desc):
        self.routes.append((route, tuple(methods), desc))


class HostShadowConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "root"
        (self.root / "data").mkdir(parents=True)
        os.environ["ASTRBOT_ROOT"] = str(self.root)
        self.context = FakeContext()
        self.plugin = MODULE.MemoryShadow(self.context, {})
        await self.plugin.initialize()

    async def asyncTearDown(self):
        await self.plugin.terminate()

    async def test_routes_registered_and_console_reuses_service(self):
        self.assertIsNotNone(self.plugin.console_api, self.plugin.console_error)
        self.assertIsNone(self.plugin.console_error)
        routes = {route: methods for route, methods, _ in self.context.routes}
        self.assertEqual(len(routes), 24)
        base = "/astrbot_plugin_memory_shadow/console/"
        self.assertEqual(routes[base + "overview"], ("GET",))
        self.assertEqual(routes[base + "maintenance/backup"], ("POST",))
        self.assertEqual(routes[base + "store/delete"], ("POST",))

        overview = await self.plugin.console_api._overview()
        self.assertIn("counts", overview)
        self.assertFalse(overview["app"]["read_only"])
        environment = await self.plugin.console_api._environment()
        self.assertTrue(environment["output"]["available"])
        self.assertEqual(environment["output"]["schema_version"], "memory-output-v1")

    def test_view_assets_exist(self):
        view = Path(MODULE.__file__).resolve().parent / "views" / "console"
        self.assertTrue((view / "index.html").is_file())
        self.assertTrue((view / "app.js").is_file())
        self.assertTrue((view / "style.css").is_file())
        html = (view / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="./app.js"', html)
        self.assertIn('href="./style.css"', html)

    def test_view_assets_match_standalone_source(self):
        root = Path(MODULE.__file__).resolve().parents[1]
        source = root / "dlm_console" / "web"
        view = root / "host_shadow" / "views" / "console"
        for name in ("index.html", "app.js", "style.css", "favicon.svg"):
            self.assertEqual((source / name).read_bytes(),
                             (view / name).read_bytes(),
                             "run python scripts/sync_console_view.py after editing " + name)

    def test_metadata_declares_view(self):
        metadata = Path(MODULE.__file__).resolve().parent / "metadata.yaml"
        text = metadata.read_text(encoding="utf-8")
        self.assertIn("views:", text)
        self.assertIn("name: console", text)


if __name__ == "__main__":
    unittest.main()
