"""通过 NapCat WebUI API 热更新 OneBot 配置（不用重启 NapCat）。

流程（从 static 资源里核对）：
  1. POST /api/auth/login {hash: sha256(token + ".napcat")} -> 会话 token
  2. POST /api/OB11Config/GetConfig  -> {code, data: 配置}
  3. POST /api/OB11Config/SetConfig  body={"config": "<json 字符串>"}

用法：
    python harness/napcat_webui.py --show
    python harness/napcat_webui.py --apply
"""

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

# 可用环境变量 NAPCAT_DIR 覆盖
NAPCAT_DIR = Path(os.environ.get('NAPCAT_DIR', Path.home() / 'Desktop' / 'NapCat.Shell'))
WEBUI = NAPCAT_DIR / 'config' / 'webui.json'
CLIENT_NAME = 'astrbot-testbed'


def client(port, token):
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
    settings = json.loads(WEBUI.read_text(encoding='utf-8'))
    webui_port = settings.get('port', 6099)
    raw_token = settings['token']

    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=6201, help='testbed 反向 WS 端口')
    parser.add_argument('--token', default='dlm-testbed-2026')
    parser.add_argument('--webui-port', type=int, default=webui_port)
    parser.add_argument('--show', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    base = 'http://127.0.0.1:%d/api' % args.webui_port

    def post(path, payload, auth=None):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + auth
        request = urllib.request.Request(
            base + path, data=json.dumps(payload).encode('utf-8'),
            headers=headers, method='POST')
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode('utf-8'))

    try:
        login = post('/auth/login', {
            'hash': hashlib.sha256((raw_token + '.napcat').encode('utf-8')).hexdigest(),
            'totpCode': '',
        })
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        raise SystemExit('WebUI 登录失败：%r（可改用重启 NapCat 的方式）' % exc)
    if login.get('code') != 0:
        raise SystemExit('WebUI 登录被拒：%s' % login.get('message'))
    session = login.get('data')

    current = post('/OB11Config/GetConfig', {}, auth=session)
    config = current.get('data')
    if not isinstance(config, dict):
        raise SystemExit('GetConfig 返回异常：%s' % json.dumps(current, ensure_ascii=False)[:200])
    print('当前 websocketClients:', json.dumps(
        config.get('network', {}).get('websocketClients', []), ensure_ascii=False))
    if args.show or not args.apply:
        return 0

    clients = [item for item in config.setdefault('network', {}).get('websocketClients', [])
               if item.get('name') != CLIENT_NAME]
    clients.append(client(args.port, args.token))
    config['network']['websocketClients'] = clients
    result = post('/OB11Config/SetConfig',
                  {'config': json.dumps(config, ensure_ascii=False)}, auth=session)
    print('SetConfig:', json.dumps(result, ensure_ascii=False)[:300])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
