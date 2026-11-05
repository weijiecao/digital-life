"""作用域覆盖（会话认领）：把某个发送者的旧 origin 绑回来，使旧库在新环境下继续可见。

背景：作用域是 `sha256(发送者 + origin)`，origin 形如 `platform:message_type:session`。
换机器/换平台实例名后 origin 变化 → 作用域变化 → 旧库查不到。这里不改数据库，只在入口
把 origin 替换成用户认领的旧值，于是所有表仍然按旧作用域读写。

存储：`<插件数据目录>/scope_overrides.json`，形如 {"123456": "aiocqhttp:FriendMessage:123456"}。
只影响本机，删除该文件或 `/dlm 取消认领` 即恢复。
"""
import json
import os
from pathlib import Path

FILENAME = 'scope_overrides.json'


def path_for(root):
    return Path(root) / FILENAME


def load(root):
    """读取覆盖表；文件不存在或损坏时返回空表（不抛异常，避免插件启动失败）。"""
    try:
        data = json.loads(path_for(root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            result[key] = value
    return result


def save(root, mapping):
    """原子写入：先写临时文件再替换，避免半截文件导致下次启动读不到。"""
    target = path_for(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.tmp')
    tmp.write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                   encoding='utf-8')
    os.replace(tmp, target)
    return target


def valid_origin(origin):
    """只接受三段式 `platform:message_type:session`，且必须是私聊。"""
    if not isinstance(origin, str):
        return False
    parts = origin.split(':')
    if len(parts) != 3 or not all(part.strip() for part in parts):
        return False
    return parts[1].strip().lower() == 'friendmessage'
