# 当前状态

> 本文件是**能力与验证状态**的唯一说明；数字以
> [`dev/reports/current_acceptance.json`](../dev/reports/current_acceptance.json) 为准。
> 历史报告里的数字是当时快照，见 [`dev/reports/README.md`](../dev/reports/README.md)。

## 版本

| 项 | 值 |
| --- | --- |
| 插件 | `astrbot_plugin_digital_life` |
| 源码版本 | `0.8.0-dev.3` 修复候选 |
| 最近公开版本 | `0.8.0-dev.2`，下列发布验收记录属于该版本 |
| 版本源 | 仓库根 `VERSION`（唯一人工版本源）；`metadata.yaml` 由 `tools/sync_version.py` 派生并由 CI 校验 |
| 源码基线 | `e32b76cc5148c40c873c2e4de363e7417bc6c996`（整理前的远端 `main`） |
| 发布记录 | 被测提交、包哈希与各套件结果只在 [`dev/reports/current_acceptance.json`](../dev/reports/current_acceptance.json) 中给出；历史版本的数字标明所属版本，不复用 |

没有 `.git` 时构建会如实记录 `source_commit: null`，`--require-clean` 会拒绝这种构建；发布构建是在干净克隆上执行的，所以清单里有真实提交号。
正式交付必须从干净提交构建，并在清单里写明该提交。

## 状态分列

| 维度 | 状态 | 说明 |
| --- | --- | --- |
| 实现 | 已实现 | 迁移、实时追加、恢复切换、撤回、运行权、输出 v2、统一管理页的代码都在 `dev/` |
| 离线验证 | 通过 | 407 项源码回归 + 204 项包级用例（47＋137＋20）全部通过，0 跳过（Windows 11 / Python 3.12.10） |
| 最新正式验收 | 0.8.0-dev.2（被测提交 `c42ceff`）：407 源码 + 47/137/20 包级 + 7 条 DOM；`formal=true`，证据在 `dev/reports/acceptance/c42ceff…/` |
| 发布更正 | dev.2 工程 ZIP 曾误收录旧失败报告；已改为“收录前绑定 + 发布侧索引检查”并替换附件，见 [`ERRATUM_2026-11-16_DEV2.md`](../dev/reports/ERRATUM_2026-11-16_DEV2.md) |
| 工程验收问题 | A01–A04 已关闭并复核，记录见 [`dev/reports/AUDIT_CLOSURE_2026-11-16.md`](../dev/reports/AUDIT_CLOSURE_2026-11-16.md) |
| DOM 接线 | 通过（非浏览器） | 7 条管理路径（迁移创建/批次/放行、备份、校验、恢复、撤回）在解压包上通过；jsdom 26.1.0 / Node 20.18.1；**真实浏览器未运行** |
| 包级验证 | 通过（真实 AstrBot 源码） | 79 文件候选包 16 项结构检查通过；插件模块用 AstrBot 4.29.3 源码导入（`host: astrbot-source`），并通过 AstrBot 自带 `inspect_plugin_archive` 校验 |
| 真实宿主验证 | 部分（未启动实例） | 已用真实 AstrBot 4.29.3 源码完成插件导入与归档校验；但未启动 AstrBot/NapCat 实例、无 Windows 实机安装、无浏览器页面操作 |
| 发布判断 | 开发候选 | 不满足 RC 条件；不得对外宣称"生产可用" |

## 当前证据

| 证据 | 位置 | 内容 |
| --- | --- | --- |
| 源码回归 | `dist/regression.json`、`dist/regression.log` | 407 ran / 407 passed / 0 failed / 0 errors / 0 skipped |
| 包级用例 | `dist/package_regression.json` | completion 47/47、core 137/137、legacy 20/20，全部对同一候选 ZIP |
| 构建清单 | `dist/build_manifest.json` | 文件数、字节数、SHA-256、逐文件哈希与构建环境；当前值以验收记录为准 |
| 包验证 | `dist/package_verification.json` | 16 项检查全部通过，`host: astrbot-source`（`testbed/AstrBot-4.29.3`） |
| AstrBot 归档校验 | `dist/astrbot_install_check.json` | `_PluginUpdater.inspect_plugin_archive` 通过，metadata 入口为 `astrbot_plugin_digital_life/metadata.yaml` |
| 包级负例 | `python tools/verify_unified_plugin.py --self-test` | 缺文件 / 篡改 / 路径穿越 / 重复成员均被拒绝 |
| 仓库检查 | `python tools/check_repository.py` | 入口、链接、归档隔离、版本单一来源、CI 引用 |

汇总记录：`dev/reports/current_acceptance.json`。

## 交付集合

一个版本对应一个完整集合（`tools/build_engineering_bundle.py` 生成，`check_release_set.py` 校验）：

| 文件 | 内容 |
| --- | --- |
| `astrbot_plugin_digital_life-<V>.zip` | 可安装插件（79 文件） |
| `digital-life-source-<V>.zip` | 该提交的完整源码 |
| `digital-life-engineering-<V>.zip` | 源码 ZIP + 插件 ZIP + 说明 + 验收摘要 |
| `digital-life-release-<V>.json` | 版本、来源提交、附件哈希、验证边界 |
| `SHA256SUMS-<V>.txt` | 上述附件校验值 |

当前候选为 `0.8.0-dev.2`，用户下载入口是分发仓库的 [同名 Release](https://github.com/weijiecao/astrbot_plugin_digital_life/releases/tag/v0.8.0-dev.2)。它的插件包与哈希由构建清单给出，记录在
`dev/reports/current_acceptance.json`；已发布的 `0.7.0-dev.2` / `0.7.0-dev.3`
保留原字节在 `archive/releases/`，仅作追溯，不得与新版本共用同一附件名。

## 未完成的验收

1. **真实 AstrBot / NapCat**：插件安装、加载、采集、注入、模型调用均未在真实宿主上复跑。
2. **Windows 实机安装**：文件句柄、长期运行、断电耐久性未验证。
3. **浏览器 UI**：只验证了路由与状态返回，未验证页面加载与点击流程。
4. **迁移端到端**：`create → batch → 续跑 → release` 已用合成旧库夹具自动化；真实旧库样本（含真实分组/摘要）仍需一次人工迁移演练。
5. **DOM 之后**：DOM 通过不等于浏览器通过；真实 Chromium 页面、布局与登录态仍未验收。
6. **质量与语义**：质量集是合成/开发反例，不是独立真实聊天或人工盲评；输出预算依赖调用方 tokenizer。
6. **72 小时长跑**：无 soak 结果。

## 明确的边界

- 统一插件与旧 `astrbot_plugin_memory_shadow` **不能同时启用**；同目录只允许一个实例写。
- 输出注入默认关闭，只有 ASSIST 模式才注入；记忆块是临时内容，不写入持久化历史。
- 撤回不是物理擦除：审计原文、旧代次目录与历史备份仍然保留。
- 本仓库的自动化只证明"离线逻辑没坏"；不能替代真实宿主与人工验收。
