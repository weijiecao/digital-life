"""迁移包：把当前库、插件配置与认领表打成一个可校验的 zip。

设计边界：
- 只在**本机**打包与检查，不做联网传输，也不做在线恢复（恢复必须停服后手工替换文件）；
- 打包前对 WAL 做 checkpoint，保证 `memory.sqlite3` 是自洽快照；
- 包内每个文件都写 sha256 到 manifest；`/dlm 迁移检查` 会逐个校验，并对库做完整性检查与计数；
- 不打包派生缓存以外的“额外内容”：只带库、配置、认领表与恢复说明。
"""
import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

FORMAT = 'digital-life-migration/1'
DB_NAME = 'memory.sqlite3'
MANIFEST = 'manifest.json'
RESTORE = 'RESTORE.md'


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _plugin_version():
    try:
        text = Path(__file__).with_name('metadata.yaml').read_text(encoding='utf-8')
    except OSError:
        return ''
    for line in text.splitlines():
        if line.startswith('version:'):
            return line.split(':', 1)[1].strip()
    return ''


def _db_counts(path):
    """只读统计：原话数、作用域数、事件数；失败返回 None（不影响打包）。"""
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % Path(path).as_posix(), uri=True)
    except sqlite3.Error:
        return None
    try:
        captures = conn.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
        scopes = conn.execute('SELECT COUNT(DISTINCT scope) FROM dl_capture').fetchone()[0]
        try:
            events = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        except sqlite3.Error:
            events = None
        return {'captures': captures, 'scopes': scopes, 'events': events}
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _restore_text(plugin_name):
    return '''# 迁移包恢复说明

这个包由数字生命记忆库生成，包含三样东西：

| 文件 | 内容 |
|---|---|
| `memory.sqlite3` | 记忆库快照（原话、事件、关系、索引） |
| `plugin_config.json` | 打包时的插件配置（可能不存在） |
| `scope_overrides.json` | 会话认领表（可能不存在） |
| `manifest.json` | 每个文件的 sha256 与计数，供 `/dlm 迁移检查` 校验 |

## 恢复步骤（目标机器已装好 AstrBot 与本插件）

1. **停掉 AstrBot**（必须，否则库文件被占用）。
2. 备份目标机现有数据目录：
   `<AstrBot根>/data/plugin_data/%s/` → 改成 `.bak-时间戳`。
3. 解压本包，把 `memory.sqlite3` 放到 `<AstrBot根>/data/plugin_data/%s/manual_test_memory.sqlite3`。
4. 如果包里有 `plugin_config.json`：对照它修改目标机的
   `<AstrBot根>/data/config/%s_config.json`。**注意把 `model_dir` 改成新机器的实际路径**，
   `allowed_sender_ids` 保持你的 QQ 号。
5. 如果包里有 `scope_overrides.json`：放到 `<AstrBot根>/data/plugin_data/%s/` 下
   （这是旧机器的会话认领表；没有认领过就不会有）。
6. 启动 AstrBot，私聊发送 `/dlm 记忆状态` 看计数；若显示 0 而库确实有数据，说明 **origin 变了**：
   在旧机器上跑 `/dlm 会话信息` 记下 origin，在新机器上 `/dlm 会话认领 <旧 origin>`。

## 校验

在新机器上把本 zip 放进插件数据目录的 `imports/`（或 `exports/`），然后：

```text
/dlm 迁移检查 迁移包.zip
```

会逐个文件比对 sha256，并对库做 `PRAGMA integrity_check` 与计数核对。
''' % (plugin_name, plugin_name, plugin_name, plugin_name)


def build_pack(store, root, *, config_path=None, out_dir=None, now=None):
    """生成迁移包，返回路径与统计。"""
    now = now or datetime.now(timezone.utc)
    root = Path(root)
    folder = Path(out_dir or root / 'exports')
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / ('migrate-%s-%s' % (now.strftime('%Y%m%d-%H%M%S'), _plugin_version() or 'x'))
    pack = base.with_suffix('.zip')

    # WAL checkpoint：保证拷出来的库是自洽快照
    try:
        store.flow.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except sqlite3.Error:
        pass

    entries = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        db_copy = tmp / DB_NAME
        shutil.copy2(store.path, db_copy)
        entries.append(db_copy)
        for name, path in (('plugin_config.json', config_path),
                           ('scope_overrides.json', root / 'scope_overrides.json')):
            if path and Path(path).is_file():
                target = tmp / name
                shutil.copy2(path, target)
                entries.append(target)
        restore = tmp / RESTORE
        restore.write_text(_restore_text('astrbot_plugin_digital_memory_store'), encoding='utf-8')
        entries.append(restore)

        files = [{'path': item.name, 'bytes': item.stat().st_size, 'sha256': _sha256(item)}
                 for item in entries]
        manifest = {'format': FORMAT, 'created_at': now.isoformat(timespec='seconds'),
                    'plugin_version': _plugin_version(), 'database': DB_NAME,
                    'counts': _db_counts(db_copy), 'files': files,
                    'restore': RESTORE}
        (tmp / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                    encoding='utf-8')

        with zipfile.ZipFile(pack, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(tmp / MANIFEST, MANIFEST)
            for item in entries:
                archive.write(item, item.name)
        return {'pack': str(pack), 'bytes': pack.stat().st_size, 'manifest': manifest}


def inspect_pack(path):
    """校验迁移包：结构、sha256、库完整性、计数。返回 {'ok', 'problems', 'manifest'}。"""
    path = Path(path)
    problems = []
    manifest = None
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return {'ok': False, 'problems': ['无法打开压缩包：%s' % exc], 'manifest': None}
    with archive:
        names = set(archive.namelist())
        for required in (MANIFEST, DB_NAME):
            if required not in names:
                problems.append('缺少必需文件：%s' % required)
        if MANIFEST not in names:
            return {'ok': False, 'problems': problems, 'manifest': None}
        try:
            manifest = json.loads(archive.read(MANIFEST).decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as exc:
            return {'ok': False, 'problems': problems + ['manifest.json 无法解析：%s' % exc],
                    'manifest': None}
        if manifest.get('format') != FORMAT:
            problems.append('格式不匹配：%s（期望 %s）' % (manifest.get('format'), FORMAT))
        listed = {item.get('path'): item for item in manifest.get('files', []) if isinstance(item, dict)}
        for name in names:
            if name == MANIFEST or name.endswith('/'):
                continue
            if name not in listed:
                problems.append('包内有未登记文件：%s' % name)
        with tempfile.TemporaryDirectory() as tmp:
            for name, item in listed.items():
                if name not in names:
                    problems.append('manifest 登记但包里缺失：%s' % name)
                    continue
                target = Path(tmp) / Path(name).name
                target.write_bytes(archive.read(name))
                if _sha256(target) != item.get('sha256'):
                    problems.append('校验和不匹配：%s' % name)
                if name == DB_NAME:
                    counts = _db_counts(target)
                    expected = manifest.get('counts') or {}
                    if counts is None:
                        problems.append('数据库无法读取或已损坏')
                    else:
                        for key in ('captures', 'scopes'):
                            if expected.get(key) is not None and counts.get(key) != expected.get(key):
                                problems.append('计数不一致：%s（包内 %s，manifest %s）'
                                                % (key, counts.get(key), expected.get(key)))
                    conn = None
                    try:
                        conn = sqlite3.connect('file:%s?mode=ro' % target.as_posix(), uri=True)
                        check = conn.execute('PRAGMA integrity_check').fetchone()[0]
                        if check != 'ok':
                            problems.append('SQLite 完整性检查未通过：%s' % check)
                    except sqlite3.Error as exc:
                        problems.append('SQLite 完整性检查失败：%s' % exc)
                    finally:
                        if conn is not None:
                            conn.close()
    return {'ok': not problems, 'problems': problems, 'manifest': manifest}
