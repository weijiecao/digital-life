# 当前状态

> 本文件是**能力与验证状态**的唯一说明；数字以
> [`dev/reports/current_acceptance.json`](../dev/reports/current_acceptance.json) 为准。
> 历史报告里的数字是当时快照，见 [`dev/reports/README.md`](../dev/reports/README.md)。

## 版本

| 项 | 值 |
| --- | --- |
| 插件 | `astrbot_plugin_digital_life` |
| 版本 | `0.7.0-dev.3`（开发版，不是 RC） |
| 版本源 | `dev/astrbot_plugin_digital_life/metadata.yaml`（唯一） |
| 源码基线 | `e32b76cc5148c40c873c2e4de363e7417bc6c996`（整理前的远端 `main`） |
| 整理与发布 | 整理先在无 `.git` 的工作副本完成，随后从干净克隆分 5 次提交推送（`fdcebaf`…`41cc8a1`）；验收记录里的 `tested_commit` 为 `5b35787`，对应清单 `source_commit=5b35787`、`dirty=false` |

没有 `.git` 时构建会如实记录 `source_commit: null`，`--require-clean` 会拒绝这种构建；发布构建是在干净克隆上执行的，所以清单里有真实提交号。
正式交付必须从干净提交构建，并在清单里写明该提交。

## 状态分列

| 维度 | 状态 | 说明 |
| --- | --- | --- |
| 实现 | 已实现 | 迁移、实时追加、恢复切换、撤回、运行权、输出 v2、统一管理页的代码都在 `dev/` |
| 离线验证 | 通过 | 367 项活动回归全部通过，0 跳过（Windows 11 / Python 3.12.10；耗时见验收记录） |
| 包级验证 | 通过（真实 AstrBot 源码） | 79 文件候选包 16 项结构检查通过；插件模块用 AstrBot 4.29.3 源码导入（`host: astrbot-source`），并通过 AstrBot 自带 `inspect_plugin_archive` 校验 |
| 真实宿主验证 | 部分（未启动实例） | 已用真实 AstrBot 4.29.3 源码完成插件导入与归档校验；但未启动 AstrBot/NapCat 实例、无 Windows 实机安装、无浏览器页面操作 |
| 发布判断 | 开发候选 | 不满足 RC 条件；不得对外宣称"生产可用" |

## 当前证据

| 证据 | 位置 | 内容 |
| --- | --- | --- |
| 回归结果 | `dist/regression.json`、`dist/regression.log` | 367 ran / 367 passed / 0 failed / 0 errors / 0 skipped |
| 构建清单 | `dist/build_manifest.json` | 79 文件、223,274 bytes、SHA-256 `631e114a…`、逐文件哈希、构建环境 |
| 包验证 | `dist/package_verification.json` | 16 项检查全部通过，`host: astrbot-source`（`testbed/AstrBot-4.29.3`） |
| AstrBot 归档校验 | `dist/astrbot_install_check.json` | `_PluginUpdater.inspect_plugin_archive` 通过，metadata 入口为 `astrbot_plugin_digital_life/metadata.yaml` |
| 包级负例 | `python tools/verify_unified_plugin.py --self-test` | 缺文件 / 篡改 / 路径穿越 / 重复成员均被拒绝 |
| 仓库检查 | `python tools/check_repository.py` | 入口、链接、归档隔离、版本单一来源、CI 引用 |

汇总记录：`dev/reports/current_acceptance.json`。

## 与已交付包的关系

- 已交付：`releases/astrbot_plugin_digital_life-0.7.0-dev.3.zip`，79 文件 / 223,274 bytes / `631e114a…`
  （`source_commit=c1ae7f8`，干净树构建；发布清单见 `releases/astrbot_plugin_digital_life-0.7.0-dev.3.json`）
- 上一版：`archive/releases/astrbot_plugin_digital_life-0.7.0-dev.2.zip`，78 文件 / 222,031 bytes / `06756a5f…`
- 差异：版本号 `0.7.0-dev.2 → 0.7.0-dev.3`；版本改为只从 `metadata.yaml` 读取（不再有两处硬编码）；
  新增 `memory_engine/README.md`；4 个 README 更新；文本换行按 `.gitattributes` 规范化。
  **没有运行行为改动。**

旧包保留原字节，用于追溯；新版本不覆盖旧交付物。详见
[`releases/README.md`](../releases/README.md)。

## 未完成的验收

1. **真实 AstrBot / NapCat**：插件安装、加载、采集、注入、模型调用均未在真实宿主上复跑。
2. **Windows 实机安装**：文件句柄、长期运行、断电耐久性未验证。
3. **浏览器 UI**：只验证了路由与状态返回，未验证页面加载与点击流程。
4. **迁移端到端**：`create → batch → 续跑 → release` 已用合成旧库夹具自动化；真实旧库样本（含真实分组/摘要）仍需一次人工迁移演练。
5. **质量与语义**：质量集是合成/开发反例，不是独立真实聊天或人工盲评；输出预算依赖调用方 tokenizer。
6. **72 小时长跑**：无 soak 结果。

## 明确的边界

- 统一插件与旧 `astrbot_plugin_memory_shadow` **不能同时启用**；同目录只允许一个实例写。
- 输出注入默认关闭，只有 ASSIST 模式才注入；记忆块是临时内容，不写入持久化历史。
- 撤回不是物理擦除：审计原文、旧代次目录与历史备份仍然保留。
- 本仓库的自动化只证明"离线逻辑没坏"；不能替代真实宿主与人工验收。
