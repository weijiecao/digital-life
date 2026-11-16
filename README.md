# 数字记忆工程 · Digital Memory Engineering

**愿在时间的长河中，我们可以再次相遇。  
而记忆，是我们留给未来的坐标。**

数字记忆工程由两部分组成：

- **数字记忆基座**：事件、证据、来源、Resolver、租约/回执/连续水位；
- **数字记忆引擎**：候选召回、证据准入、重评、上下文与渲染；
- 外面套一层 **统一 AstrBot 插件**：原生输入、实时追加、旧数据迁移、备份/恢复、管理页。

两者都独立于 V1 生产插件；当前只用于隔离验收。

**当前状态：`0.8.0-dev.2` 开发基线（R0 / R1 NO-GO）。** 安装包只用于隔离验收，
不要接入 V1 生产或生产 Shadow。能力与验证状态见 [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md)。

## 最短路径

```powershell
python -m pip install -r dev/requirements-release.txt
# 版本只有一个来源：仓库根 VERSION；metadata 由工具同步
python tools/sync_version.py --check
python tools/run_regression.py                          # 407 项源码回归
python tools/check_repository.py                        # 入口/链接/归档隔离检查
python tools/build_unified_plugin.py --out-dir dist     # 构建插件包
python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json
```

产物在 `dist/`（Git 忽略）；完整流程、发布与回退见 [`docs/MAINTAINER_FLOW.md`](docs/MAINTAINER_FLOW.md)。

## 组件与目录

```text
dev/
  astrbot_plugin_digital_life/  统一插件：入口、应用服务、迁移/恢复、管理 UI
  astrbot_memory_input/         原生输入契约、实时追加、历史对账
  memory_engine/                引擎：召回/准入/重评/上下文/预算/渲染
  memory_output/                输出协议 v2 与临时注入适配
  v1_5_consolidator/            基座：事件/证据/Resolver/租约/回执/水位
  dlm_console/                  独立本地 Web 控制台（也可作为插件页）
  tests/                        活动回归
  evaluation/                   质量与恢复验收脚本、冻结数据
  reports/                      当前验收证据、历史快照与专项覆盖映射
  docs/                         模块级文档
  scripts/                      仍受支持的开发辅助脚本
tools/                          构建、验包、仓库检查、分发导出
docs/                           架构、维护流程、当前状态、迁移恢复
releases/                       当前交付包（历史包在 archive/releases）
archive/                        停止维护的实现、工具与历史材料（不被活动代码导入）
testbed/                        AstrBot/NapCat 测试床说明与 harness
env/                            环境模板与锁文件
```

插件源码的唯一来源是本仓库 `dev/astrbot_plugin_digital_life/`；分发仓库
[`weijiecao/astrbot_plugin_digital_life`](https://github.com/weijiecao/astrbot_plugin_digital_life)
由 `python tools/export_astrbot_plugin.py` 单向导出，不做反向同步。

## 统一插件

整体版本 `0.8.0-dev.2`（记忆基座、引擎、输入、输出、应用服务与 UI 同一版本号），包含旧基座迁移、实时追加与历史对账、恢复切换、撤回、
偏好排序/短指代/主动请求记忆入口与统一管理页。

| 模式 | 行为 |
| --- | --- |
| OFF | 不采集、不注入；管理员仍可管理已有数据 |
| COLLECT（默认） | 实时采集、同步历史、后台整理，不注入 |
| ASSIST | 在 COLLECT 基础上，按证据与 Token 预算注入临时记忆块 |

停用旧 `astrbot_plugin_memory_shadow` 后再启用统一插件；同一数据目录只允许一个写者。
安装、配置与管理页操作见 [`dev/astrbot_plugin_digital_life/README.md`](dev/astrbot_plugin_digital_life/README.md)。

## 交付物

版本只由仓库根 `VERSION` 决定（当前 `0.8.0-dev.2`）。一次交付是一整个集合，不是单个补丁包：

| 文件 | 说明 |
| --- | --- |
| `VERSION` | 唯一人工版本源；`metadata.yaml` 由 `tools/sync_version.py` 派生 |
| `releases/` | 当前版本的安装包、发布清单与校验值（构建后写入，旧版本移到 `archive/releases/`） |
| `dist/release/` | 完整交付集合：插件 ZIP、源码 ZIP、工程 ZIP、发布 JSON、`SHA256SUMS-<版本>.txt` |
| `dev/reports/current_acceptance.json` | 当前验收记录（含 suites、证据索引与一致性问题） |
| `dev/reports/acceptance/<提交>/` | 持久证据：回归、包级用例、构建清单、验包与导出的小型报告 |
| `dev/reports/SPECIALIZED_COVERAGE.md` | 能力 → 测试 → 环境的映射与缺口 |
| `verification/README.md` | 包级与 UI 验收资产（47＋137＋20） |
| `docs/ARCHITECTURE.md`、`docs/MIGRATION_RECOVERY.md` | 架构与迁移/恢复/撤回操作 |

构建与验收：

```powershell
python tools/sync_version.py --check
python tools/run_regression.py                 # 407 项源码回归
python tools/build_unified_plugin.py --out-dir dist --require-clean
python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json
python tools/run_package_regression.py --dist dist --suites completion,core,legacy
python tools/build_engineering_bundle.py --dist dist --out-dir dist/release
python tools/check_release_set.py --release-dir dist/release
python tools/record_acceptance.py
```

## 已知边界

- 质量集是合成/开发反例，不是独立真实聊天或人工盲评；
- 无真实 AstrBot/NapCat 安装、加载、采集、注入与 72h 长跑结果；
- 无 Windows 实机安装与断电/fsync 耐久性结果；
- 浏览器页面操作未验收，只验证了管理 API 的路由与状态返回；
- 迁移已用合成旧库夹具覆盖 `create → batch → 续跑 → release`；真实旧库仍需一次人工演练；
- 输出注入默认关闭，尚未评估真实模型收益或提示注入免疫；预算依赖调用方 tokenizer；
- 本轮不修改 V1 生产插件、不部署、不接入生产 Shadow。

## License

MIT，见 [`LICENSE`](LICENSE)。插件安装、配置与故障排查见插件目录的 `README.md`。
