"""启动 testbed 实例，打印平台/插件/端口状态后退出（不发送任何消息）。

用途：在接 QQ 前后快速判断"平台装上了没、端口听着没、插件加载了没"。
用法：.venv\\Scripts\\python.exe harness\\boot_check.py
"""

import asyncio
import os
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
INSTANCE = TESTBED / 'instance'
SOURCE = TESTBED / 'AstrBot-4.29.3'

os.environ['ASTRBOT_ROOT'] = str(INSTANCE)
os.chdir(INSTANCE)
sys.path.insert(0, str(INSTANCE))
sys.path.insert(0, str(SOURCE))

import runtime_bootstrap  # noqa: E402

runtime_bootstrap.initialize_runtime_bootstrap()

from astrbot.core import LogBroker, db_helper  # noqa: E402
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle  # noqa: E402


def port_open(port, host='127.0.0.1'):
    sock = socket.socket()
    sock.settimeout(1.0)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


async def main():
    lifecycle = AstrBotCoreLifecycle(LogBroker(), db_helper)
    await lifecycle.initialize()
    task = asyncio.create_task(lifecycle.start())
    await asyncio.sleep(3.0)
    try:
        print('platforms:')
        for inst in lifecycle.platform_manager.platform_insts:
            meta = inst.meta()
            print('  - %-18s id=%s' % (meta.name, meta.id))
        print('plugins:', [star.name for star in lifecycle.plugin_manager.context.get_all_stars()])
        print('reverse-ws 127.0.0.1:6201:', 'open' if port_open(6201) else 'closed')
        print('webui 6199:', 'open' if port_open(6199) else 'closed')
    finally:
        await lifecycle.stop()
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
