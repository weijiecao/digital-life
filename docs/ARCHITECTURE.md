# 数字记忆工程架构

数字记忆工程由两个边界清晰的部分组成，外面套一层宿主接入与运维界面：

```text
┌──────────────────────────── 数字记忆工程 ────────────────────────────┐
│                                                                      │
│  数字记忆基座                          数字记忆引擎                    │
│  ├─ 原话 / source / receipt            ├─ 候选召回（词面 + BGE）       │
│  ├─ event / revision / evidence        ├─ 证据准入 admission           │
│  ├─ provenance / story / subject       ├─ rejudge / worker             │
│  ├─ resolver / cannot-link             ├─ context / render             │
│  ├─ scope lease / fencing              ├─ budget / cache               │
│  └─ manifest / contiguous frontier     └─ audit / explain              │
│                                                                      │
│  输入适配 ─ 实时追加 + 历史对账        输出适配 ─ memory-output-v2      │
│  应用服务 ─ 迁移 / 备份 / 恢复 / 撤回   管理界面 ─ 统一管理页 + 控制台   │
└──────────────────────────────────────────────────────────────────────┘
```

## 代码位置与职责

| 位置 | 职责 | 关键模块 |
| --- | --- | --- |
| `dev/astrbot_plugin_digital_life/` | 插件入口、应用服务、宿主适配、管理 UI | `main.py`、`config.py`、`console_api.py`、`application/`、`runtime/` |
| `dev/astrbot_memory_input/` | 原生输入契约、实时追加、历史对账 | `native.py`、`unified.py`、`journal.py` |
| `dev/memory_engine/` | 召回、准入、重评、上下文、预算、渲染、CLI | `engine.py`、`admission.py`、`native_host.py`、`render.py` |
| `dev/memory_output/` | 输出协议 v2 与宿主临时注入适配 | `contracts.py`、`compose.py`、`astrbot.py` |
| `dev/v1_5_consolidator/` | 基座：事件、证据、来源、Resolver、租约、回执、水位 | `store.py`、`resolver.py`、`gateway.py` |
| `dev/dlm_console/` | 独立本地 Web 控制台（也作为插件页复用） | `core.py`、`server.py`、`web/` |
| `tools/` | 构建、验包、仓库检查、分发导出、宿主归档校验 | `build_unified_plugin.py`、`verify_unified_plugin.py`、`check_repository.py`、`check_astrbot_install.py`、`export_astrbot_plugin.py` |
| `dev/tests/` | 活动回归 | 见 `dev/reports/SPECIALIZED_COVERAGE.md` |
| `archive/` | 停止维护的实现、工具与历史材料 | 不得被活动代码导入 |

## 数字记忆基座

职责：保存可追溯的事实来源，维护事件、修订、证据、来源归属和并发一致性。

- `v1_5_consolidator/store.py`：事件、证据、来源、租约、回执、连续水位；
- `v1_5_consolidator/resolver.py`、`provenance_guard.py`：无法链接的判断与来源约束；
- 基座不决定"答案是什么"，只提供有来源、有版本、可回退的事件与证据。

## 数字记忆引擎

职责：从基座读取有效证据，完成候选召回、证据准入、重评、上下文选择和预算渲染。

- `memory_engine/admission.py`：SUPPORTED / RELATED_ONLY / INSUFFICIENT / CONFLICT；
- `memory_engine/rejudge.py`、`worker.py`：自动重评与队列；
- `memory_engine/render.py`、`token_budget.py`：预算与渲染；
- 原则：候选可以召回，但只有被当前有效证据支持的内容才能进入上下文；来源不足时返回空或降级原因。

## 输入：实时追加与历史对账

`astrbot_memory_input/unified.py` 维护持久身份账本：

- 带稳定 `message_id` 的当前消息独立追加，同一 ID 幂等，相同文本不同 ID 分别保留；
- 历史继续接受完整快照；截断、编辑、重排报冲突，不用局部列表冒充完整历史；
- 缺少 ID 的历史若与实时记录可能重叠，保留原性别名并进入待定位，不按文本相等强行合并；
- 可信适配器可用 `UnifiedJournal.stage(history, origin, links={...})` 提供位置映射。

## 输出：memory-output-v2

`memory_output/contracts.py` 定义协议，`compose.py` 组装预算内的临时块，
`astrbot.py` 只修改 `ProviderRequest.extra_user_content_parts`：

- 记忆块标记为临时，不写入持久化历史；`prompt`/`system_prompt`/`contexts` 不变；
- 协议新增 `store_generation` 与可选 `retrieval_query`；后者只扩展检索意图；
- 证据删除、撤回、修订、scope 变化或预算缩水时，旧块被拒绝并移除；
- 默认 `COLLECT` 不注入，只有显式 `ASSIST` 才注入。

## 应用服务：迁移、备份、恢复、撤回

`astrbot_plugin_digital_life/application/`：

- `service.py`：统一的在线写入口，所有维护操作串行，带维护闸门与状态机
  （`QUIESCE → SNAPSHOT/STAGE → CLOSE → SWITCH → OPEN → VERIFY`）；
- `migration.py`：旧库冻结快照、显式身份映射、断点批次、条目状态机；
- `storage.py`：双库一致备份、备份校验、代次目录与 `CURRENT.json` 原子切换、崩溃回退。

详见 [`MIGRATION_RECOVERY.md`](MIGRATION_RECOVERY.md)。

## 运行权

`runtime/ownership.py`：

- 数据目录用跨进程文件锁 + 进程内守卫，保证同一目录只有一个写者；
- 同名工具型入口会先查宿主星标注册表，旧 `astrbot_plugin_memory_shadow` 活动时显示
  `blocked-legacy`，此时不采集、不注入；
- 同进程检测无法阻止另一个进程中的不合作旧程序，操作同一数据集前必须停用旧插件。

## 管理界面

- 插件页：WebUI → 插件 → 数字生命 → **统一管理**（`console_api.py` 注册路由）；
- 独立控制台：`python -m dlm_console` 或根目录 `启动数字记忆控制台.cmd`；
- 两者共用 `dlm_console/core.py` 的只读查询；写操作统一走应用服务。

## 数据布局

```text
<AstrBot>/data/plugin_data/astrbot_plugin_digital_life/
  CURRENT.json                    活动代次指针
  shadow.sqlite3                  业务库（首次恢复前位于根目录）
  native_input_journal.sqlite3    输入账本
  commit_state.json               提交时间
  generations/<32hex>/            恢复后的代次目录
  backups/<32hex>/                备份集（含 manifest.json）
  migrations/                     迁移冻结源
```

不得绕过 `CURRENT.json` 直接使用根目录旧文件。

## 状态

当前为开发版 `0.7.0-dev.3`，**R0/R1 NO-GO**。真实宿主、Windows 实机、浏览器页面与
72 小时长跑未验收；见 [`CURRENT_STATUS.md`](CURRENT_STATUS.md)。
