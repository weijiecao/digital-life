"""离线自检 CLI：在装 AstrBot 之前先判断“这台机器能不能跑语义检索”。

用法：
    python scripts/preflight.py                      # 只检查依赖
    python scripts/preflight.py --model-dir "D:/models/bge-small-zh-v1.5"
    python scripts/preflight.py --model-dir ... --json

退出码：0 = 可运行（语义检索可用或明确关闭）；1 = 配置/依赖不满足。
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from astrbot_plugin_digital_memory_store.preflight import check_environment, format_report


def main(argv=None):
    parser = argparse.ArgumentParser(description='E5.0-F 依赖与模型目录自检')
    parser.add_argument('--model-dir', default='', help='本机 SentenceTransformer 模型目录')
    parser.add_argument('--json', action='store_true', help='输出 JSON（供 report.py 汇总）')
    args = parser.parse_args(argv)
    result = check_environment(args.model_dir)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_report(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
