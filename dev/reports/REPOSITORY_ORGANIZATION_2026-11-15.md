# 仓库整理记录（2026-11-15）

按 `Digital_Life_Repository_Organization_Plan_v1.md`（v1.0）实施。本文件记录做了什么、
用什么证明、以及计划中仍未完成的部分。数字来自 `dev/reports/current_acceptance.json`。

## 1. 基线与执行环境

| 项 | 值 |
| --- | --- |
| 计划基线 | `e32b76cc5148c40c873c2e4de363e7417bc6c996`（整理前的远端 `main`） |
| 本轮工作副本 | 整理在无 `.git` 的工作目录完成；随后从干净克隆提交并推送，验收记录绑定 `tested_commit=5b35787` |
| 环境 | Windows 11 / Python 3.12.10；未安装 AstrBot；`git` 不在 PATH |
| 历史交付 | `releases/astrbot_plugin_digital_life-0.7.0-dev.2.zip`，78 文件 / 222,031 bytes / `06756a5f…` |

整理阶段没有 `git`，所以先在工作树完成；发布阶段从干净克隆分 5 次提交推送（`fdcebaf`、`88b886b`、`00a3d0d`、`5b35787`、`41cc8a1`），没有改写历史、没有动 tag。构建在 `5b35787` 上以 `--require-clean` 执行，清单记录真实提交且 `dirty=false`。
正式交付前应在干净克隆上重新执行 §4 的命令，让清单记录真实提交号。

## 2. PR1：恢复构建与验收入口

### 2.1 新增工具（`tools/`）

| 文件 | 作用 |
| --- | --- |
| `plugin_package.json` | 唯一打包清单：6 个源目录 → 包内布局、排除规则、必需文件、ZIP 元数据 |
| `repo_packaging.py` | 版本读取、`.gitattributes` 换行规范化、成员生成、确定性 ZIP、成员安全 |
| `build_unified_plugin.py` | `--out-dir`、`--require-clean`；写 `build_manifest.json` 与 `.sha256` |
| `verify_unified_plugin.py` | 16 项包级检查 + 4 个负例自检；`--report`/`--self-test-report`；`--host-path` 用真实 AstrBot 源码导入 |
| `check_astrbot_install.py` | 用 AstrBot 自带 `_PluginUpdater.inspect_plugin_archive` 校验归档 |
| `repo_packaging.py` | 原 `packaging.py`：避免 `python tools/<script>.py` 时遮蔽 PyPI `packaging` |
| `check_repository.py` | 11 项仓库检查（入口、归档隔离、链接、命令、CI、版本源、忽略项、归档包） |
| `run_regression.py` | 跑活动回归并写 `regression.json`/`regression.log` |
| `record_acceptance.py` | 从证据生成 `dev/reports/current_acceptance.json` |
| `export_astrbot_plugin.py` | 单向导出 + `SOURCE_MANIFEST.json` + `--check` 漂移检测 |
| `repository_manifest.json` | 声明必需路径、活动文档、活动代码根、忽略项 |

### 2.2 测试归集

- 新增 `dev/tests/test_unified_plugin.py`（35 项）：迁移预览与端到端写路径（create→batch→续跑→release）、备份一致性与篡改拒绝、
  恢复切换与崩溃回退、实时追加去重/冲突/待定位、撤回屏障、运行权（文件锁 + 宿主注册表）、
  输出 v2 契约、管理 API 路由与状态、SQLite 句柄释放、包元数据契约。
- `dev/tests/test_r0_bundle.py` 移入 `archive/tests/`：它找不到的 R0 包本来就在归档里，
  原位置只能永远 skip。旧包完整性改由 `check_repository.py` 的 `archive-releases` 检查
  （校验归档 ZIP 的 SHA-256、字节数与逐文件哈希），不再以跳过代替通过。
- 能力 → 测试 → 环境的映射与缺口写在 `dev/reports/SPECIALIZED_COVERAGE.md`。

### 2.3 脚本与 CI

- `dev/scripts/package_lifecycle.py`、`dev/scripts/upgrade.py` 归档：前者依赖已归档的
  `verify_r0_bundle.py`，后者 import 已归档的 `install.py`，都只服务旧 RC/旧插件，
  按计划"连同说明归档"，没有加路径绕回。
- `.github/workflows/tests.yml` 重写：6 组矩阵跑仓库检查 + `run_regression.py`；
  Ubuntu/3.12 作为规范环境构建（`--require-clean`）、验包、负例、双次导出比对；
  触发路径加入 `tools/**`、`docs/**`、`releases/**`、`archive/**` 与关键根文件。

## 3. PR2：统一文档与状态

- 新增 `docs/CURRENT_STATUS.md`：版本、证据、能力状态（实现/离线/宿主/发布分列）、未完成项。
- 新增 `docs/MIGRATION_RECOVERY.md`：迁移、备份、恢复切换、撤回的前提、步骤、状态与失败处理。
- 重写 `docs/ARCHITECTURE.md`、`docs/MAINTAINER_FLOW.md`、`README.md`、`CONTRIBUTING.md`、
  `dev/README.md`、`releases/README.md`、`archive/README.md`、`dev/reports/README.md`。
- `dev/STATUS.md` 改为指针；旧自动生成快照存 `dev/reports/history/STATUS_2026-11-11.md`。
- 模块 README 同步：插件、`astrbot_memory_input`、`memory_output`、`dlm_console` 删除失效命令与
  旧宿主接线描述；新增 `dev/memory_engine/README.md`。
- 旧的 `dev/docs/` 指南按性质分流：`LOCAL_INSTALL_TEST`、`METRICS`、`MEMORY_VERIFICATION_PLAN`、
  1.0.19 安装说明与 RC 说明进入 `archive/docs/`；`V1_5_CONSOLIDATOR.md` 的 56 行失效报告链接
  换成两个现存证据入口。
- 历史材料建索引：`archive/history/{e5_0_f,legacy_installers,unified_plugin_package}/`，
  桌面遗留的 E5.0-F 交接包、旧安装脚本、旧统一插件工程包测试都保留在内并写明状态。

## 4. 验证结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 活动回归 | `python tools/run_regression.py` | 367 ran / 367 passed / 0 failed / 0 errors / 0 skipped（耗时见 `current_acceptance.json`） |
| 仓库检查 | `python tools/check_repository.py` | 11 项全部通过 |
| 构建 | `python tools/build_unified_plugin.py --out-dir dist` | 79 文件 / 222,925 bytes / SHA-256 `08c90bbd…` |
| 可复现 | 连续构建两次、并在干净副本中构建 | 三次哈希一致 |
| 验包（真实源码） | `… --dist dist --host-path testbed/AstrBot-4.29.3` | 16 项通过，`host: astrbot-source` |
| 宿主归档校验 | `python tools/check_astrbot_install.py --astrbot-root testbed/AstrBot-4.29.3` | `inspect_plugin_archive` 通过，metadata 入口正确 |
| 负例 | `--self-test` | 缺文件 / 篡改 / 路径穿越 / 重复成员全部被拒绝 |
| 导出 | `--output dist/plugin_export` + `--check` | 79 文件，重复导出一致；手改文件被 `--check` 检出 |
| 干净副本 | 复制去掉 `.local/`、`dist/`、testbed 运行目录的树 | 8 条命令全部通过，产物哈希与主树一致 |

"干净副本"验证的是 PR1 的准入条件：不依赖本机外部开发路径。

## 5. 与计划的偏差

1. **提交分组**：计划建议三个 PR；实际按内容分成 5 个提交（工具、测试、文档、归档、记录），
   都直接落在 `main`，没有新建分支或 PR。
2. **`source_commit`**：发布构建在干净克隆上执行，清单记录 `5b35787` 且 `dirty=false`；
   无 `.git` 的副本构建仍记录 `null`，`--require-clean` 会拒绝。
3. **新包与已交付包字节不同**：79 vs 78 文件（新增 `memory_engine/README.md`），
   4 个模块 README 更新，`main.py`/`metadata.yaml`/`requirements.txt`/插件 README 的换行
   规范化为 LF。旧包保留原字节，新字节没有覆盖它。
4. **CI 远端验证**：推送后已触发新的 `tests` 工作流（基线 `e32b76c` 的 failure 正是入口失效）；结果以 GitHub Actions 为准。
5. **真实旧库未演练**：迁移写路径已用合成旧库夹具自动化，但真实旧库（真实分组/摘要/撤回）仍需一次演练。
6. **模块重命名**：`tools/packaging.py` 改名为 `tools/repo_packaging.py`。原因：`python tools/x.py` 会把
   `tools/` 放进 `sys.path[0]`，遮蔽 PyPI 的 `packaging` 包（AstrBot 依赖它），导致宿主校验失败。
7. **宿主检查不能写在仓库里**：AstrBot 以进程 cwd 解析 `data/`、`t2i_templates/`，直接导入会把
   `data/cmd_config.json` 写进工作树。`check_astrbot_install.py` 现在在临时目录内导入，
   `verify --host-path` 的探测也在临时 cwd 运行；`.gitignore` 增加 `/data/` 作为兜底。

## 6. 建议的提交分组

| 分支/提交 | 内容 |
| --- | --- |
| `chore/repository-entrypoints` | `tools/`（打包清单、构建、验包、仓库检查、回归记录）、`.github/workflows/tests.yml`、`dev/tests/test_unified_plugin.py`、`archive/tests/test_r0_bundle.py`、`dev/scripts/*` 归档 |
| `docs/repository-maintenance` | `README.md`、`CONTRIBUTING.md`、`docs/*`、`dev/README.md`、`dev/STATUS.md`、`releases/README.md`、`archive/README.md`、`dev/reports/*`、模块 README、`archive/docs/*` |
| `chore/plugin-export` | `tools/export_astrbot_plugin.py`、`tools/repository_manifest.json` 的导出口径、`docs/MAINTAINER_FLOW.md` 导出章节 |

## 7. 计划 §8 验收清单状态

| 条目 | 状态 | 说明 |
| --- | --- | --- |
| 基线提交、工作树状态、历史 ZIP 哈希已记录 | 部分 | 历史 ZIP 与基线提交已记录；工作树无 `.git`，状态记为 `null` |
| 两仓源码权属明确，独有修复已逐项处理 | 部分 | 已逐文件比较：74 一致、4 个文档落后、2 个新增、无独有代码修复；首次导出仍待人工执行（`DISTRIBUTION_SYNC_2026-11-16.md`） |
| 文档引用的构建/验包工具实际存在 | 完成 | `check_repository` 的 `required-paths` 与 `doc-commands` |
| 活动工具不依赖已归档实现 | 完成 | `archive-isolation`；两个旧脚本已归档 |
| 全新工作树完成依赖安装、回归、构建、验包 | 完成 | 干净副本 8 条命令全部通过 |
| 基座/引擎/输入/输出/应用服务/UI 有验证映射 | 完成 | `dev/reports/SPECIALIZED_COVERAGE.md` |
| 迁移、追加、恢复、撤回、运行权、SQLite 专项归集 | 完成 | 迁移写路径已自动化；浏览器页面另计 |
| 必需包级检查缺文件会失败 | 完成 | `required-files` 失败即整体失败；负例自检 4/4 拒绝 |
| 同一规范环境重复产物哈希一致 | 完成 | 三次构建同一哈希 |
| Windows/Ubuntu 矩阵可追溯，未运行平台明确标记 | 部分 | 本机 Windows 已跑；Ubuntu 与 3.10/3.11 未运行，CI 未远端验证 |
| 当前验收记录与文档一致，历史数字不作当前基线 | 完成 | `current_acceptance.json` 生成、历史数字标注 |
| 导出具备来源清单、覆盖保护和差异检查 | 完成 | `SOURCE_MANIFEST.json`、拒绝未知目录、`--check` 实测检出漂移 |
| 无真实数据库/凭据/私人会话进入提交与安装包 | 完成 | `no-private-data` 检查；仓库内无数据库文件 |
| 历史版本可追溯，未重写 Git 历史 | 完成 | 未执行任何 Git 写操作；归档包保留原字节 |
| 真实宿主/Windows 实机/浏览器验收状态明确 | 完成 | 全部标为"未运行"并写进 `CURRENT_STATUS.md` |

## 8. 下一步

1. ~~在干净克隆上分次提交并重新生成记录~~ —— 已完成：`fdcebaf`…`41cc8a1`，`current_acceptance.json` 绑定 `5b35787`。
2. 用真实脱敏旧库演练一次迁移（同一组用例已就绪）。
3. 在真实 AstrBot/NapCat 与 Windows 实机上执行安装、采集、注入与浏览器页面验收。
4. 分发仓库：两端差异已比较（`DISTRIBUTION_SYNC_2026-11-16.md`），下一步按该文件的
   `engineering` 布局方案人工执行首次导出。
