"""安装/升级/回退：把插件与配置放进一个 AstrBot 实例，不需要手工拷贝。

只做三件事：
1. 复制插件目录到 `<AstrBotRoot>/data/plugins/astrbot_plugin_digital_memory_store`（跳过 __pycache__）；
2. 以 `_conf_schema.json` 的默认值为底、叠加配置预设（off/self/debug），写入插件配置文件；
3. 升级前把旧插件目录备份到 `data/plugins_backup/`，回退时从备份恢复。

用法：
    python scripts/install.py --astrbot-root "C:/AstrBot" --preset self \
        --allowed-sender-ids 123456 --model-dir "D:/models/bge-small-zh-v1.5"
    python scripts/upgrade.py --astrbot-root "C:/AstrBot"
    python scripts/rollback.py --astrbot-root "C:/AstrBot"
"""

import argparse
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEV_ROOT = HERE.parent
PLUGIN_NAME = 'astrbot_plugin_digital_memory_store'
SOURCE = DEV_ROOT / PLUGIN_NAME
PRESETS = ('off', 'self', 'debug')


def _ignore_pycache(directory, names):
    """复制插件时排除缓存与版本库/编辑器目录（.git 里是只读对象，复制后删不掉）。"""
    ignored = {'.git', '.github', '.venv', 'venv', '__pycache__',
               '.idea', '.vscode', 'dist', '.pytest_cache'}
    return [name for name in names if name in ignored or name.endswith('.pyc')]


def _force_rmtree(path):
    """Windows 下 .git 对象是只读的，先去掉只读位再删。"""
    path = Path(path)
    for root, dirs, files in os.walk(path):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass
        try:
            os.chmod(root, stat.S_IWRITE)
        except OSError:
            pass
    shutil.rmtree(path)


def load_schema(plugin_dir=SOURCE):
    return json.loads((Path(plugin_dir) / '_conf_schema.json').read_text(encoding='utf-8'))


def load_preset(name, plugin_dir=SOURCE):
    if name not in PRESETS:
        raise ValueError('unknown preset: %s' % name)
    path = Path(plugin_dir) / 'presets' / (name + '.json')
    return json.loads(path.read_text(encoding='utf-8'))


def build_config(schema, preset, allowed_sender_ids='', model_dir=''):
    config = {}
    for key, item in schema.items():
        config[key] = item.get('default')
    for key, value in preset.items():
        if key not in schema:
            raise ValueError('preset key not in schema: %s' % key)
        config[key] = value
    if allowed_sender_ids:
        config['allowed_sender_ids'] = allowed_sender_ids
    if model_dir:
        config['model_dir'] = model_dir
    return config


def plugin_paths(astrbot_root):
    root = Path(astrbot_root)
    return {
        'root': root,
        'plugins': root / 'data' / 'plugins',
        'target': root / 'data' / 'plugins' / PLUGIN_NAME,
        'config': root / 'data' / 'config' / (PLUGIN_NAME + '_config.json'),
        'backup_root': root / 'data' / 'plugins_backup',
        'data': root / 'data' / 'plugin_data' / PLUGIN_NAME,
    }


def install(astrbot_root, preset='self', allowed_sender_ids='', model_dir='',
            force=False, source=SOURCE):
    paths = plugin_paths(astrbot_root)
    if not paths['root'].is_dir():
        raise FileNotFoundError('AstrBotRoot 不存在：%s' % paths['root'])
    if paths['target'].exists():
        if not force:
            raise FileExistsError('插件已存在：%s（升级请用 upgrade.py，或加 --force）'
                                  % paths['target'])
        _force_rmtree(paths['target'])
    paths['plugins'].mkdir(parents=True, exist_ok=True)
    paths['config'].parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, paths['target'], ignore=_ignore_pycache)
    config = build_config(load_schema(source), load_preset(preset, source),
                          allowed_sender_ids, model_dir)
    paths['config'].write_text(json.dumps(config, ensure_ascii=False, indent=2),
                               encoding='utf-8')
    return {'target': str(paths['target']), 'config': str(paths['config']),
            'preset': preset, 'keys': len(config)}


def backup(astrbot_root):
    paths = plugin_paths(astrbot_root)
    if not paths['target'].is_dir():
        raise FileNotFoundError('没有可备份的已安装插件：%s' % paths['target'])
    stamp = time.strftime('%Y%m%dT%H%M%S')
    destination = paths['backup_root'] / ('%s_%s' % (PLUGIN_NAME, stamp))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(paths['target'], destination, ignore=_ignore_pycache)
    return destination


def upgrade(astrbot_root, source=SOURCE):
    paths = plugin_paths(astrbot_root)
    if not paths['target'].is_dir():
        raise FileNotFoundError('尚未安装：%s' % paths['target'])
    if not paths['config'].is_file():
        raise FileNotFoundError('缺少插件配置，拒绝升级以免丢配置：%s' % paths['config'])
    saved = backup(astrbot_root)
    data_saved = None
    if paths['data'].is_dir():
        data_saved = paths['backup_root'] / ('dlm_data_%s' % time.strftime('%Y%m%dT%H%M%S'))
        shutil.copytree(paths['data'], data_saved)
    _force_rmtree(paths['target'])
    shutil.copytree(source, paths['target'], ignore=_ignore_pycache)
    return {'backup': str(saved), 'data_backup': str(data_saved) if data_saved else None,
            'target': str(paths['target'])}


def rollback(astrbot_root, backup_name=None):
    paths = plugin_paths(astrbot_root)
    backups = sorted(paths['backup_root'].glob(PLUGIN_NAME + '_*')) \
        if paths['backup_root'].is_dir() else []
    if not backups:
        raise FileNotFoundError('没有可用备份：%s' % paths['backup_root'])
    chosen = paths['backup_root'] / backup_name if backup_name else backups[-1]
    if not chosen.is_dir():
        raise FileNotFoundError('备份不存在：%s' % chosen)
    if paths['target'].exists():
        retired = paths['backup_root'] / ('%s_rolledback_%s' % (
            PLUGIN_NAME, time.strftime('%Y%m%dT%H%M%S')))
        shutil.move(str(paths['target']), str(retired))
    shutil.copytree(chosen, paths['target'], ignore=_ignore_pycache)
    return {'restored_from': str(chosen), 'target': str(paths['target'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description='安装数字生命独立记忆库插件')
    parser.add_argument('--astrbot-root', required=True,
                        help='AstrBot 实例根目录（其下有 data/ 目录）')
    parser.add_argument('--preset', choices=PRESETS, default='self')
    parser.add_argument('--allowed-sender-ids', default='')
    parser.add_argument('--model-dir', default='')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    result = install(args.astrbot_root, args.preset, args.allowed_sender_ids,
                     args.model_dir, args.force)
    print('已安装到：', result['target'])
    print('配置文件：', result['config'])
    print('预设：%s（%d 个配置项）' % (result['preset'], result['keys']))
    print('下一步：重启 AstrBot，确认日志出现插件加载；再用 /dlm 上下文状态 查看模型状态。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
