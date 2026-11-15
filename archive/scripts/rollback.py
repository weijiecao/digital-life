"""回退到升级前的插件备份（最近一次或指定）。

    python scripts/rollback.py --astrbot-root "C:/AstrBot"
    python scripts/rollback.py --astrbot-root "C:/AstrBot" --backup astrbot_plugin_digital_memory_store_20261104T090000
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from install import plugin_paths, rollback  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description='回退数字生命独立记忆库插件')
    parser.add_argument('--astrbot-root', required=True)
    parser.add_argument('--backup', default='', help='备份目录名；默认最近一次')
    args = parser.parse_args(argv)
    if not args.backup:
        paths = plugin_paths(args.astrbot_root)
        backups = sorted(paths['backup_root'].glob('astrbot_plugin_digital_memory_store_*')) \
            if paths['backup_root'].is_dir() else []
        if backups:
            print('可用备份：')
            for item in backups[-5:]:
                print('  -', item.name)
    result = rollback(args.astrbot_root, args.backup or None)
    print('已从备份恢复：', result['restored_from'])
    print('当前插件目录：', result['target'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
