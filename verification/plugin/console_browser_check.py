"""真实 Chromium + 实际应用服务；只替换 AstrBot 宿主壳，不调用外部模型。"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from aiohttp import web
from playwright.async_api import async_playwright
import completion_suite as fixture


async def main():
    with tempfile.TemporaryDirectory() as temp, patch.dict(
        os.environ, {"ASTRBOT_ROOT": temp}
    ):
        context = fixture.Context()
        p = fixture.mod.DigitalLifePlugin(
            context, {"mode": "COLLECT", "allowed_sender_ids": "42"}
        )
        await p.initialize()
        if p.pump:
            await p.pump.stop()
        source = Path(temp) / "旧记忆.sqlite3"
        fixture.old_database(source)
        app = web.Application()
        errors = []
        calls = []

        async def api(request):
            name = request.match_info["path"]
            calls.append((request.method, name))
            handler = context.routes["/astrbot_plugin_digital_life/console/" + name]

            async def body(default=None):
                return (
                    await request.json() if request.can_read_body else (default or {})
                )

            p.console_api._request = fixture.types.SimpleNamespace(
                query=request.query, json=body
            )
            result = await handler()
            return web.json_response(
                result,
                status=(
                    result.get("status_code", 200) if isinstance(result, dict) else 200
                ),
            )

        async def asset(request):
            name = request.match_info.get("file") or "index.html"
            if name not in ("index.html", "app.js", "style.css"):
                raise web.HTTPNotFound()
            return web.FileResponse(fixture.PACKAGE / "views" / "console" / name)

        app.router.add_route("*", "/api/{path:.*}", api)
        app.router.add_get("/", asset)
        app.router.add_get("/{file}", asset)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    executable_path=os.environ.get(
                        "DIGITAL_LIFE_TEST_CHROMIUM", "/usr/bin/chromium"
                    ),
                    args=["--no-sandbox"],
                )
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(f"http://127.0.0.1:{port}/#unified")
                await page.locator("#migration-source").wait_for()
                await page.locator("#migration-source").fill(str(source))
                await page.locator("#migration-map").fill(
                    json.dumps(
                        {"old-a": fixture.asdict(fixture.ORIGIN)}, ensure_ascii=False
                    )
                )
                await page.locator('[data-act="unified-create"]').click()
                await page.locator('[data-act="unified-batch"]').wait_for()
                await page.locator('[data-act="unified-batch"]').click()
                await page.wait_for_function(
                    "document.querySelector('#view').textContent.includes('READY')"
                )
                await page.locator('[data-act="unified-release"]').click()
                await page.locator("#modal-ok").click()
                await page.wait_for_function(
                    "document.querySelector('#view').textContent.includes('RELEASED')"
                )
                await page.locator('[data-act="unified-backup"]').click()
                await page.locator('[data-act="unified-verify"]').wait_for()
                await page.locator('[data-act="unified-verify"]').first.click()
                await page.wait_for_function(
                    "document.querySelector('#toast').textContent.includes('校验通过')"
                )
                await page.locator('[data-act="unified-restore"]').first.click()
                await page.locator("#modal-ok").click()
                await page.wait_for_function(
                    "document.querySelectorAll('[data-act=unified-restore]').length===2"
                )
                await page.locator("#withdraw-scope").fill(fixture.ORIGIN.scope_id)
                await page.locator("#withdraw-seq").fill("1")
                await page.locator('[data-act="unified-withdraw"]').click()
                await page.locator("#modal-ok").click()
                await page.wait_for_function(
                    "document.querySelector('#toast').textContent.includes('已撤回')"
                )
                # 窄屏仍可访问实际操作，正文不可横向溢出。
                await page.set_viewport_size({"width": 390, "height": 844})
                overflow = await page.evaluate(
                    "document.documentElement.scrollWidth>window.innerWidth"
                )
                await page.screenshot(
                    path=str(Path(temp) / "ui-internal.png"), full_page=True
                )
                assert not overflow, "mobile overflow"
                assert not errors, errors
                state = await p.application.action("status")
                assert state["inbox"].get("WITHDRAWN") == 1, state["inbox"]
                print(
                    json.dumps(
                        {
                            "browser": "Chromium",
                            "runtime": "packaged application + stub AstrBot",
                            "operations": [
                                "migration_create",
                                "migration_batch",
                                "migration_release",
                                "backup",
                                "verify_backup",
                                "restore",
                                "withdraw",
                            ],
                            "store_generation": state["store_generation"],
                            "inbox": state["inbox"],
                            "page_errors": errors,
                            "mobile_overflow": overflow,
                            "api_calls": calls,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                await browser.close()
        finally:
            await runner.cleanup()
            await p.terminate()


if __name__ == "__main__":
    asyncio.run(main())
