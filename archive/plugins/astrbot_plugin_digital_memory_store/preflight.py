"""启动自检：把“缺依赖 / 缺模型 / 目录填错”变成一条可复制的明确提示。

背景（见《E5.0-F_v0.15.116_工程进度核查报告》P1-3）：
本插件记录、查看、备份都不依赖 BGE；只有语义检索需要 `torch` + `sentence-transformers`
和一个本机 SentenceTransformer 目录。原实现把这些失败留到“第一次检索”才暴露，且
`requirements.txt` 只有一行注释。这个模块只做纯检查，不导入重型依赖、不访问网络。
"""

from pathlib import Path
import importlib.util
import sys

# (import 名, 发行包名)：提示里给人看的是发行包名。
DEPENDENCIES = (('torch', 'torch'), ('sentence_transformers', 'sentence-transformers'))
# SentenceTransformer 目录至少应有一个这样的元数据文件。
MODEL_MARKERS = ('config.json', 'modules.json')
REQUIREMENTS_HINT = 'pip install -r requirements.txt'
MODEL_HINT = '把 model_dir 指向本机 SentenceTransformer 格式的 BGE 目录（含 config.json）'


def module_available(name):
    """不导入模块，只判断当前解释器能否找到它。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _model_check(model_dir):
    if not model_dir:
        return {'id': 'model_dir', 'ok': True, 'level': 'info',
                'detail': '未配置 model_dir：语义检索关闭，记录/查看/备份不受影响'}
    path = Path(model_dir).expanduser()
    if not path.is_dir():
        return {'id': 'model_dir', 'ok': False, 'level': 'config',
                'detail': '模型目录不存在：' + str(path)}
    if not any((path / marker).is_file() for marker in MODEL_MARKERS):
        return {'id': 'model_dir', 'ok': False, 'level': 'config',
                'detail': '模型目录不像 SentenceTransformer 目录（缺少 config.json）：' + str(path)}
    return {'id': 'model_dir', 'ok': True, 'level': 'info',
            'detail': '模型目录可用：' + str(path)}


def check_environment(model_dir='', has_module=module_available):
    """返回可直接打印的自检结果。

    `ok=False` 表示“配置本身不可用”；`semantic_ready=False` 但 `ok=True`
    表示语义检索关闭而其余功能正常（例如没配 model_dir 或没装 torch）。
    只有 `level` 为 `config`/`dependencies` 的失败才产生提示文本。
    未配置 model_dir 时不检查 BGE 依赖：默认配置不该每次启动都报警。
    """
    model_dir = str(model_dir or '').strip()
    if model_dir:
        missing = [pkg for module, pkg in DEPENDENCIES if not has_module(module)]
        dependency_check = {
            'id': 'dependencies', 'ok': not missing,
            'level': 'dependencies' if missing else 'info',
            'detail': ('缺少 Python 依赖：' + '、'.join(missing)) if missing else '依赖完整',
        }
    else:
        missing = []
        dependency_check = {'id': 'dependencies', 'ok': True, 'level': 'info',
                            'detail': '未配置 model_dir：不检查 BGE 依赖'}
    checks = [dependency_check, _model_check(model_dir)]

    semantic_ready = not missing and checks[1]['ok'] and bool(model_dir)
    fixes = []
    if missing:
        fixes.append(REQUIREMENTS_HINT)
    if not checks[1]['ok']:
        fixes.append(MODEL_HINT)
    if not fixes:
        message = ''
    else:
        problems = '；'.join(c['detail'] for c in checks if not c['ok'])
        message = ('[DL_MEMORY 自检] ' + problems + '。修复：' + '；'.join(fixes) +
                   '。记录、查看、备份不需要 BGE；缺少依赖只影响语义检索。')
    return {
        'ok': all(c['ok'] for c in checks),
        'semantic_ready': semantic_ready,
        'python': sys.executable,
        'model_dir': model_dir,
        'checks': checks,
        'message': message,
    }


def format_report(result):
    """把自检结果渲染成给人看的几行文本（CLI 与日志共用）。"""
    lines = ['E5.0-F 依赖与模型自检', '解释器：' + result['python']]
    for check in result['checks']:
        lines.append(('  [通过] ' if check['ok'] else '  [失败] ') + check['detail'])
    lines.append('语义检索：' + ('可用' if result['semantic_ready'] else '不可用（其余功能正常）'))
    if result['message']:
        lines.append(result['message'])
    return '\n'.join(lines)
