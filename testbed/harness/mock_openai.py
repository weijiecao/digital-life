"""OpenAI 兼容的本地 mock 服务：把 AstrBot 发来的完整请求记录下来。

用途：在没有任何外部 API key 的情况下，让 AstrBot 走**真实**的 openai 兼容管线，
从而证明 on_llm_request 注入的记忆片段确实进入了发给模型的请求体。

- POST /v1/chat/completions：记录 JSON 请求体（logs/llm_requests.jsonl），返回固定回复；
  支持 stream=true（SSE）与非流式。
- GET /requests：返回已记录请求数，供测试轮询。
- GET /health：存活检查。

只依赖标准库。启动：python mock_openai.py --port 9101 --log logs/llm_requests.jsonl
"""

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG_PATH = Path('logs/llm_requests.jsonl')
RESPONSE_LOG = Path('logs/llm_responses.jsonl')
REPLY = '好的，这是测试回复。'
LOCK = threading.Lock()
COUNT = {'n': 0}
UPSTREAM = ''
UPSTREAM_KEY = ''
UPSTREAM_MODEL = ''


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):  # 安静：只写我们自己的 jsonl
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/health'):
            self._json({'ok': True, 'count': COUNT['n']})
        elif self.path.startswith('/requests'):
            self._json({'count': COUNT['n']})
        else:
            self._json({'error': 'not found'}, 404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            payload = json.loads(raw.decode('utf-8'))
        except ValueError:
            payload = {'_unparsable': raw[:2000].decode('utf-8', 'replace')}
        record = {'at': time.time(), 'path': self.path, 'payload': payload}
        with LOCK:
            COUNT['n'] += 1
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')

        if not self.path.rstrip('/').endswith('/chat/completions'):
            return self._json({'error': 'not found'}, 404)

        model = payload.get('model', 'mock')
        if UPSTREAM:
            return self._proxy(payload)
        if payload.get('stream'):
            return self._stream(model)
        return self._json({
            'id': 'chatcmpl-mock', 'object': 'chat.completion', 'created': int(time.time()),
            'model': model,
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': REPLY}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
        })

    def _proxy(self, payload):
        """把请求转发给真实模型服务，并记录请求与回复（用于归因）。"""
        body = dict(payload)
        body['model'] = UPSTREAM_MODEL or body.get('model') or 'deepseek-chat'
        body['stream'] = False
        request = urllib.request.Request(
            UPSTREAM.rstrip('/') + '/chat/completions',
            data=json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + UPSTREAM_KEY},
            method='POST')
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read()
            data = json.loads(raw.decode('utf-8'))
            content = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
            with LOCK, RESPONSE_LOG.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'at': time.time(), 'content': content,
                                         'usage': data.get('usage')},
                                        ensure_ascii=False) + '\n')
            return self._json(data)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')[:500]
            with LOCK, RESPONSE_LOG.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'at': time.time(), 'error': detail},
                                        ensure_ascii=False) + '\n')
            return self._json({'error': {'message': detail, 'status': exc.code}}, 502)
        except Exception as exc:  # noqa: BLE001 - 代理失败要显式返回
            with LOCK, RESPONSE_LOG.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'at': time.time(), 'error': repr(exc)},
                                        ensure_ascii=False) + '\n')
            return self._json({'error': {'message': repr(exc), 'type': 'upstream_error'}}, 502)

    def _stream(self, model):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()

        def send(obj):
            data = ('data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n').encode('utf-8')
            self.wfile.write(('%X\r\n' % len(data)).encode() + data + b'\r\n')
            self.wfile.flush()

        base = {'id': 'chatcmpl-mock', 'object': 'chat.completion.chunk',
                'created': int(time.time()), 'model': model}
        send({**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': REPLY},
                                   'finish_reason': None}]})
        send({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})
        done = b'data: [DONE]\n\n'
        self.wfile.write(('%X\r\n' % len(done)).encode() + done + b'\r\n')
        self.wfile.write(b'0\r\n\r\n')
        self.wfile.flush()


def main():
    global LOG_PATH, RESPONSE_LOG, UPSTREAM, UPSTREAM_KEY, UPSTREAM_MODEL
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=9101)
    parser.add_argument('--log', default=str(LOG_PATH))
    parser.add_argument('--response-log', default=str(RESPONSE_LOG))
    parser.add_argument('--upstream', default='', help='例如 https://api.deepseek.com/v1')
    parser.add_argument('--key-file', default='')
    parser.add_argument('--model', default='')
    args = parser.parse_args()
    LOG_PATH = Path(args.log)
    RESPONSE_LOG = Path(args.response_log)
    UPSTREAM = args.upstream.strip()
    UPSTREAM_MODEL = args.model.strip()
    if args.key_file:
        UPSTREAM_KEY = Path(args.key_file).read_text(encoding='utf-8').strip()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print('mock openai listening on 127.0.0.1:%d -> %s (upstream=%s)'
          % (args.port, LOG_PATH, UPSTREAM or 'mock'), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
