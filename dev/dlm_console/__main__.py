"""命令行入口：python -m dlm_console [选项]"""

from __future__ import annotations

import argparse
import secrets
import sys
import threading
import webbrowser
from pathlib import Path

from . import APP_NAME, __version__
from .config import ConfigError, resolve
from .core import Console
from .server import ConsoleServer


def _default_workspace() -> Path:
    # dev/dlm_console/__main__.py -> dev -> 工程根目录
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dlm_console",
        description="数字记忆系统本地控制台：查看基座、影子引擎、输入队列与维护操作。",
    )
    parser.add_argument("--workspace", default=str(_default_workspace()),
                        help="工程根目录（默认：当前控制台所在工程）")
    parser.add_argument("--instance", default=None,
                        help="AstrBot 实例根目录；默认自动查找 testbed/instance，"
                             "或读取环境变量 ASTRBOT_ROOT")
    parser.add_argument("--store-dir", default=None,
                        help="直接指定 astrbot_plugin_digital_memory_store 的 plugin_data 目录")
    parser.add_argument("--shadow-dir", default=None,
                        help="直接指定 astrbot_plugin_memory_shadow 的 plugin_data 目录")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址，默认仅本机 127.0.0.1")
    parser.add_argument("--port", type=int, default=8788,
                        help="监听端口，默认 8788；被占用时自动向后寻找")
    parser.add_argument("--token", default=None,
                        help="固定访问令牌；默认每次启动随机生成")
    parser.add_argument("--no-token", action="store_true",
                        help="关闭令牌校验（仅建议在完全可信的本机使用）")
    parser.add_argument("--read-only", action="store_true",
                        help="只读模式：禁用备份、删除、VACUUM 等写操作")
    parser.add_argument("--no-open", action="store_true",
                        help="启动后不自动打开浏览器")
    parser.add_argument("--version", action="version",
                        version="%s %s" % (APP_NAME, __version__))
    return parser


def _bind(console: Console, host: str, port: int, token: str) -> ConsoleServer:
    last_error: OSError | None = None
    for candidate in range(port, port + 20):
        try:
            return ConsoleServer((host, candidate), console, token)
        except OSError as exc:
            last_error = exc
            continue
    raise SystemExit("端口 %d-%d 均不可用: %s" % (port, port + 19, last_error))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("警告：正在监听 %s，同一网络内的其他设备可能访问此控制台。" % args.host)
    token = "" if args.no_token else (args.token or secrets.token_urlsafe(18))
    try:
        paths = resolve(args.workspace, args.instance, args.store_dir,
                        args.shadow_dir, token, args.read_only)
    except ConfigError as exc:
        print("无法启动控制台：%s" % exc, file=sys.stderr)
        return 2
    console = Console(paths)
    try:
        server = _bind(console, args.host, args.port, token)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    port = server.server_address[1]
    host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = "http://%s:%d/" % (host, port)
    if token:
        url += "?token=" + token

    print()
    print("  %s v%s" % (APP_NAME, __version__))
    print("  " + "-" * 58)
    print("  地址：%s" % url)
    if token:
        print("  令牌：%s" % token)
    print("  模式：%s" % ("只读" if args.read_only else "可维护（本机）"))
    print()
    print("  数据源：")
    for label, path in (("工作目录", paths.workspace),
                        ("AstrBot 实例", paths.instance),
                        ("基座数据库", paths.store_db),
                        ("影子数据库", paths.shadow_db),
                        ("输入 journal", paths.journal_db),
                        ("观察日志", paths.shadow_log)):
        state = ""
        if path is not None:
            state = " [已发现]" if Path(path).exists() else " [未发现]"
        print("    %-12s %s%s" % (label, path if path else "—", state))
    print()
    print("  按 Ctrl+C 停止。")
    print()

    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n控制台已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
