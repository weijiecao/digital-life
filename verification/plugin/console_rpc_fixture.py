"""供前端 DOM 集成测试调用真实 ConsoleApi；使用管道，无网络端口。"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
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
        print(
            json.dumps(
                {
                    "source": str(source),
                    "origin": fixture.asdict(fixture.ORIGIN),
                    "scope": fixture.ORIGIN.scope_id,
                }
            ),
            flush=True,
        )
        try:
            while True:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    break
                command = json.loads(line)
                try:
                    name = command["endpoint"].removeprefix("console/")

                    async def body(default=None):
                        return command.get("body", {})

                    p.console_api._request = fixture.types.SimpleNamespace(
                        query=command.get("query", {}), json=body
                    )
                    result = await context.routes[
                        "/astrbot_plugin_digital_life/console/" + name
                    ]()
                    print(
                        json.dumps(
                            {"id": command["id"], "data": result}, ensure_ascii=False
                        ),
                        flush=True,
                    )
                except Exception as exc:
                    print(
                        json.dumps({"id": command["id"], "error": str(exc)}), flush=True
                    )
        finally:
            await p.terminate()


if __name__ == "__main__":
    asyncio.run(main())
