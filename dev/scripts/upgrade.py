"""升级已安装插件：先备份当前版本，再用本目录的插件覆盖，保留原配置。

    python scripts/upgrade.py --astrbot-root "C:/AstrBot"
    python scripts/rollback.py --astrbot-root "C:/AstrBot"     # 回退到最近一次备份
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from install import upgrade  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description='升级数字生命独立记忆库插件')
    parser.add_argument('--astrbot-root', required=True)
    args = parser.parse_args(argv)
    result = upgrade(args.astrbot_root)
    print('已备份到：', result['backup'])
    print('已升级到：', result['target'])
    print('回退命令： python scripts/rollback.py --astrbot-root "%s"' % args.astrbot_root)
    return 0


if __name__ == '__main__':
    sys.exit(main())
