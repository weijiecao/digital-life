"""控制台 HTTP 服务：静态页面 + JSON API。"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import APP_NAME, __version__
from .core import ApiError, Console

WEB_DIR = Path(__file__).with_name("web")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
MAX_BODY = 1024 * 1024


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 的 SO_REUSEADDR 允许重复绑定同一端口，会让“端口占用后自动换端口”
    # 变成两个进程争抢同一端口；因此只在 POSIX 上启用。
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, console: Console, token: str = ""):
        self.console = console
        self.token = token
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "DLMConsole/" + __version__
    protocol_version = "HTTP/1.0"

    # -- 入口 -----------------------------------------------------------

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method: str):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            if method == "GET" and path in STATIC_FILES:
                return self._serve_static(path)
            if not path.startswith("/api/"):
                return self._json({"error": "未找到该路径"}, 404)
            if not self._authorized(query):
                return self._json({"error": "令牌缺失或无效，请从启动终端提供的地址打开"}, 401)
            if method == "POST":
                if not self._same_origin():
                    return self._json({"error": "拒绝跨站请求"}, 403)
                payload = self._body()
            else:
                payload = {}
            data, status = self._dispatch(method, path, query, payload)
            return self._json(data, status)
        except ApiError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # 保证服务不因单个请求退出
            traceback.print_exc()
            return self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    # -- 路由 -----------------------------------------------------------

    def _dispatch(self, method: str, path: str, query: dict,
                  payload: dict) -> tuple[dict, int]:
        c = self.server.console

        def q(name, default=""):
            values = query.get(name)
            return values[0] if values else default

        def qi(name, default=0):
            try:
                return int(q(name, default))
            except (TypeError, ValueError):
                return default

        if method == "GET":
            if path == "/api/health":
                return {"ok": True, "name": APP_NAME, "version": __version__,
                        "read_only": c.read_only}, 200
            if path == "/api/environment":
                return c.environment(), 200
            if path == "/api/overview":
                return c.overview(), 200
            if path == "/api/store/scopes":
                return c.store_scopes(), 200
            if path == "/api/store/captures":
                return c.store_captures(q("scope"), q("state"), q("q"),
                                        qi("page", 1), qi("size", 20)), 200
            if path == "/api/store/capture":
                return c.store_capture(q("scope"), qi("seq", 0)), 200
            if path == "/api/store/events":
                return c.store_events(q("scope"), q("q"),
                                      qi("page", 1), qi("size", 20)), 200
            if path == "/api/shadow/scopes":
                return c.shadow_scopes(), 200
            if path == "/api/shadow/events":
                return c.shadow_events(q("scope"), q("q"), q("state"),
                                       qi("page", 1), qi("size", 20)), 200
            if path == "/api/shadow/event":
                return c.shadow_event(q("event_id")), 200
            if path == "/api/shadow/inbox":
                return c.shadow_inbox(q("scope"), q("state"),
                                      qi("page", 1), qi("size", 20)), 200
            if path == "/api/shadow/conflicts":
                return c.shadow_conflicts(qi("page", 1), qi("size", 20)), 200
            if path == "/api/shadow/observations":
                return c.observations(q("scope"), q("q"), qi("days", 0),
                                      qi("page", 1), qi("size", 20)), 200
            if path == "/api/shadow/latency":
                return c.latency(), 200
            if path == "/api/maintenance/backups":
                return c.backups(q("dataset", "store")), 200
            if path == "/api/maintenance/audit":
                return c.audit(qi("limit", 100)), 200
            return {"error": "未知接口: %s" % path}, 404

        if path == "/api/maintenance/backup":
            return c.backup(payload.get("dataset", "store")), 200
        if path == "/api/maintenance/verify_backup":
            return c.verify_backup(payload.get("dataset", "store"),
                                   payload.get("name", "")), 200
        if path == "/api/maintenance/restore":
            return c.restore_backup(payload.get("dataset", "store"),
                                    payload.get("name", "")), 200
        if path == "/api/maintenance/rotate":
            return c.rotate(payload.get("dataset", "store"), payload.get("keep", 7)), 200
        if path == "/api/maintenance/checkpoint":
            return c.checkpoint(payload.get("dataset", "store")), 200
        if path == "/api/maintenance/integrity":
            return c.integrity(payload.get("dataset", "store")), 200
        if path == "/api/maintenance/vacuum":
            return c.vacuum(payload.get("dataset", "store")), 200
        if path == "/api/maintenance/archive":
            return c.archive(payload.get("months", 12), bool(payload.get("export")),
                             payload.get("kind", "captures")), 200
        if path == "/api/store/delete":
            backup = bool(payload.get("backup", True))
            backup_result = None
            if backup:
                # 备份失败时不删除：宁可报错，也不能让操作者以为已经备份。
                backup_result = c.backup("store")
            result = c.store_delete(payload.get("scope", ""),
                                    payload.get("seq"),
                                    bool(payload.get("all")),
                                    bool(payload.get("purge", True)))
            if backup_result:
                result["backup"] = backup_result
            return result, 200
        return {"error": "未知接口: %s" % path}, 404

    # -- 响应 -----------------------------------------------------------

    def _json(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str):
        name, content_type = STATIC_FILES[path]
        target = WEB_DIR / name
        if not target.is_file():
            return self._json({"error": "界面文件缺失: %s" % name}, 500)
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self'; img-src 'self' data:; "
                         "connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    # -- 请求辅助 -------------------------------------------------------

    def _authorized(self, query: dict) -> bool:
        token = self.server.token
        if not token:
            return True
        supplied = self.headers.get("X-Console-Token", "")
        if not supplied:
            values = query.get("token")
            supplied = values[0] if values else ""
        return secrets.compare_digest(supplied, token)

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        host = self.headers.get("Host", "")
        return parsed.netloc.lower() == host.lower()

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            raise ApiError("Content-Length 无效")
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ApiError("请求体过大", 413)
        content_type = (self.headers.get("Content-Type") or "").lower()
        if "application/json" not in content_type:
            raise ApiError("只接受 application/json", 415)
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError("请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise ApiError("请求体必须是 JSON 对象")
        return data

    def log_message(self, fmt, *args):
        line = fmt % args
        line = re.sub(r"token=[^&\s\"]+", "token=***", line)
        sys.stderr.write("[console] %s %s\n" % (
            self.log_date_time_string(), line))
