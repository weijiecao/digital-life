"""安装后自检：在一个 AstrBot 根目录上核对插件、配置、预设、依赖与模型目录。

用法：
    python scripts/verify_install.py --astrbot-root "C:/AstrBot"
    python scripts/verify_install.py --astrbot-root ... --json reports/verify_install.json
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEV_ROOT = HERE.parent
PLUGIN_NAME = 'astrbot_plugin_digital_memory_store'
PRESETS = ('off', 'self', 'debug')


def verify(astrbot_root):
    root = Path(astrbot_root)
    plugin = root / 'data' / 'plugins' / PLUGIN_NAME
    config = root / 'data' / 'config' / (PLUGIN_NAME + '_config.json')
    problems = []
    checks = {}

    checks['plugin_dir'] = plugin.is_dir()
    if not checks['plugin_dir']:
        problems.append('插件目录不存在')
    main = plugin / 'main.py'
    metadata = plugin / 'metadata.yaml'
    schema_file = plugin / '_conf_schema.json'
    checks['files'] = all(path.is_file() for path in (main, metadata, schema_file,
                                                      plugin / 'requirements.txt'))
    if not checks['files']:
        problems.append('缺少 main.py / metadata.yaml / _conf_schema.json / requirements.txt')

    version = None
    if metadata.is_file():
        for line in metadata.read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('version:'):
                version = line.split(':', 1)[1].strip()
    checks['version'] = version
    if not version:
        problems.append('metadata.yaml 缺 version')

    schema = {}
    if schema_file.is_file():
        try:
            schema = json.loads(schema_file.read_text(encoding='utf-8-sig'))
        except ValueError:
            problems.append('_conf_schema.json 不是合法 JSON')
    checks['schema_keys'] = len(schema)

    config_data = {}
    checks['config_file'] = config.is_file()
    if not checks['config_file']:
        problems.append('插件配置文件不存在（是否运行过 install.py？）')
    else:
        try:
            config_data = json.loads(config.read_text(encoding='utf-8-sig'))
        except ValueError:
            problems.append('配置文件不是合法 JSON')
        missing = [key for key in schema if key not in config_data]
        if missing:
            problems.append('配置缺 key：%s' % ','.join(missing[:5]))
        if config_data.get('enabled') is True and not str(config_data.get('allowed_sender_ids', '')).strip():
            problems.append('enabled=true 但 allowed_sender_ids 为空（不会采集任何消息）')

    preset_ok = []
    for name in PRESETS:
        path = plugin / 'presets' / (name + '.json')
        if not path.is_file():
            problems.append('缺少预设 %s' % name)
            continue
        try:
            json.loads(path.read_text(encoding='utf-8-sig'))
            preset_ok.append(name)
        except ValueError:
            problems.append('预设 %s 不是合法 JSON' % name)
    checks['presets'] = preset_ok

    model_dir = str(config_data.get('model_dir', '') or '').strip()
    if model_dir:
        model_path = Path(model_dir)
        checks['model_dir'] = model_path.is_dir() and (model_path / 'config.json').is_file()
        if not checks['model_dir']:
            problems.append('model_dir 不是有效的 SentenceTransformer 目录：%s' % model_dir)
    else:
        checks['model_dir'] = None  # 未配置 = 只走字面路径，不算失败

    return {'ok': not problems, 'version': version, 'checks': checks,
            'problems': problems, 'root': str(root)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='安装后自检')
    parser.add_argument('--astrbot-root', required=True)
    parser.add_argument('--json', default='')
    args = parser.parse_args(argv)
    result = verify(args.astrbot_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                   encoding='utf-8')
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
