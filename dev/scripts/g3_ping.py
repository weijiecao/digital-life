"""最小连通性测试：确认 DeepSeek 兼容接口可用，并读出 usage（不打印 key）。"""

import json
import os
import sys
import time
import urllib.request

KEY_FILE = r'C:\Users\205\Desktop\数字生命\testbed\instance\data\.deepseek_key'
BASE = 'https://api.deepseek.com/v1/chat/completions'


def main():
    key = open(KEY_FILE, encoding='utf-8').read().strip()
    payload = {
        'model': 'deepseek-chat',
        'messages': [{'role': 'user', 'content': '只回复两个字：收到'}],
        'max_tokens': 16,
        'temperature': 0,
        'stream': False,
    }
    request = urllib.request.Request(
        BASE, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key},
        method='POST')
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read().decode('utf-8'))
    elapsed = time.perf_counter() - started
    print('http_ok', 'elapsed_s', round(elapsed, 3))
    print('model', body.get('model'))
    print('content', (body.get('choices') or [{}])[0].get('message', {}).get('content', '')[:40])
    print('usage', json.dumps(body.get('usage'), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
