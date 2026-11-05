# 发布说明 v1.0.9

纯结构重构，**行为不变**：`storage.py` 2934 行、`main.py` 1520 行，两个文件都到了难以阅读的规模，
这版按领域拆开。

## 拆成什么样

**`storage.py` → `store/` 包**（`storage.py` 只剩 5 行兼容 shim）

| 模块 | 内容 |
|---|---|
| `store/base.py` | 四个信号常量、`_RollbackBatch`、`atomic_write` 写锁装饰器 |
| `store/embedding.py` | `embedder_ready`、`prewarm_embedder`、`_get_embedder`、`_index` |
| `store/capture.py` | 采集、缺口、`pending`、`memory_inventory` |
| `store/drafts.py` | 新分组/分组/对照/摘要草稿的存取与校验 |
| `store/events.py` | 别名、优先级、偏好信号 |
| `store/organize.py` | 弱候选、`propose`、批量整理、更新线索 |
| `store/relations.py` | 关系、日期、时间线、摘录 |
| `store/briefs.py` | 摘要/分段缓存 |
| `store/context.py` | 上下文包、事件供给（最大的一块，945 行） |
| `store/backup.py` | 备份与校验 |
| `store/core.py` | `Store` 组合入口（`__init__`、`save`、`search`、`show`、`close`） |

**`main.py` 1520 → 721 行**

`_work`（487 行的 `/dlm` 分发）、6 个 `_generate_*`、6 个 `_render_*` 移入 `commands.py` 的
`CommandMixin`，`class DigitalMemoryStore(CommandMixin, Star)`。
**装饰过的入口**（`capture_private`、`add_memory_context`、`dlm`）仍留在 `main.py`，
AstrBot 的处理器注册逻辑完全不变。

## 为什么这样拆而不是重写

- 方法体原样搬移（仍用 `self`），不改变一行逻辑，`git diff` 可逐行核对；
- `storage.py` 保留 shim，测试和脚本里的旧导入路径（`from .storage import Store, MAX_CAPTURE_CHARS,
  _UPDATE_CUES`）全部继续有效，不需要改 30 多个调用点；
- 拆分后用真实 AstrBot 跑过一遍，确认 `Star` 能识别继承来的方法。

## 验证

| 项 | 结果 |
|---|---|
| 离线回归 | **337/337** |
| 真实 AstrBot 4.29.3 | 插件加载 ✅、钩子 3 个 ✅（`add_memory_context`/`capture_private`/`dlm`） |
| 真实采集/注入 | 采集 24 条、注入 2 次 |
| 真实 BGE | 512 维，冷加载 12.3 s |
| 插件 zip | 通过 AstrBot 自带 `inspect_plugin_archive`（version=1.0.9） |

## 已知限制（不变）

- 只支持私聊纯文本；群聊/图片/语音/多设备不支持。
- 语义阈值基于自造语料，真实分布下可能下降。
- 指代追问默认接不住；开启候选筛选器会外发原话。
