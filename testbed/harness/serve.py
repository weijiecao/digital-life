"""常驻 testbed 实例：加载真实 AstrBot 核心 + 平台 + 插件，直到进程被停止。

与真实部署的差别只有：不启动 dashboard（避免占用生产实例的端口）、
provider 指向本地 mock（或后续配置的真实 provider）。

用法（后台运行）：
    .venv\\Scripts\\python.exe harness\\serve.py
"""

import asyncio
import os
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


async def main():
    lifecycle = AstrBotCoreLifecycle(LogBroker(), db_helper)
    await lifecycle.initialize()
    print('platforms:', [(inst.meta().name, inst.meta().id)
                         for inst in lifecycle.platform_manager.platform_insts], flush=True)
    print('plugins:', [star.name for star in lifecycle.plugin_manager.context.get_all_stars()],
          flush=True)
    print('testbed ready', flush=True)
    try:
        await lifecycle.start()
    finally:
        await lifecycle.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
