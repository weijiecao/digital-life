"""把便携 NapCat 的 OneBot 配置指向 testbed 的 aiocqhttp 反向 WS。

前提：小号已经用 NapCat 登录过一次，`config/onebot11_<QQ>.json` 已生成。
本脚本只改 `network.websocketClients`，保留其它字段；可重复执行（按 name 覆盖）。

用法：
    python harness/napcat_connect.py --qq 123456 --dry-run
    python harness/napcat_connect.py --qq 123456
    python harness/napcat_connect.py --qq 123456 --config-dir "D:/NapCat/config"
"""

import argparse
import json
import os
from pathlib import Path

# 可用 --config-dir 或环境变量 NAPCAT_CONFIG_DIR 覆盖
DEFAULT_CONFIG_DIR = Path(os.environ.get(
    'NAPCAT_CONFIG_DIR', Path.home() / 'Desktop' / 'NapCat.Shell' / 'config'))
CLIENT_NAME = 'astrbot-testbed'


def build_client(port, token):
    return {
        'name': CLIENT_NAME,
        'enable': True,
        'url': 'ws://127.0.0.1:%d/ws' % port,
        'token': token,
        'messagePostFormat': 'array',
        'reportSelfMessage': False,
        'reconnectInterval': 3000,
        'heartInterval': 30000,
        'debug': False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--qq', required=True, help='机器人小号的 QQ 号')
    parser.add_argument('--config-dir', default=str(DEFAULT_CONFIG_DIR))
    parser.add_argument('--port', type=int, default=6201)
    parser.add_argument('--token', default='dlm-testbed-2026')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    path = Path(args.config_dir) / ('onebot11_%s.json' % args.qq)
    if not path.is_file():
        raise SystemExit('找不到 %s：先让 NapCat 用该账号登录一次，再运行本脚本。' % path)

    data = json.loads(path.read_text(encoding='utf-8-sig'))
    network = data.setdefault('network', {})
    clients = [item for item in network.get('websocketClients', [])
               if item.get('name') != CLIENT_NAME]
    clients.append(build_client(args.port, args.token))
    network['websocketClients'] = clients

    print('目标文件：', path)
    print('将写入：', json.dumps(build_client(args.port, args.token), ensure_ascii=False))
    if args.dry_run:
        print('（dry-run，未写入）')
        return 0
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print('已写入。请在 NapCat 控制台确认出现 "WebSocket 客户端 ... 已连接"，')
    print('或让 NapCat 重载配置（多数版本会自动热加载；必要时重启 NapCat，QQ 会自动快速登录）。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
