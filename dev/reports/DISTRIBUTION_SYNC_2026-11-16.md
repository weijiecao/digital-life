# 分发仓库对齐记录（2026-11-16）

PR3 的前提是"先比较两端指定提交，再启用单向导出"。本文件记录对
`weijiecao/astrbot_plugin_digital_life` 的实际比较结果与首次导出方案。
本轮**没有推送、没有建 Release**，只做只读比较与本地导出。

## 1. 两端现状

| 项 | 本仓库（源码） | 分发仓库 |
| --- | --- | --- |
| 布局 | `dev/` 开发树 + `tools/` + `releases/` | 旧"工程包"布局：根 `astrbot_plugin_digital_life/` + `shared/` + `tests/` + `tools/` + `verification/` + `docs/` + `archive/` |
| 版本 | `0.7.0-dev.2` | `0.7.0-dev.2`（`metadata.yaml`） |
| 文件数 | 导出 79（+`SOURCE_MANIFEST.json`） | 199 |
| 角色 | 唯一源码来源 | 用户分发副本（不是源码来源） |

分发仓库的 `tests/`、`tools/`、`verification/`、`archive/`、`docs/` 是上一版工程包的脚手架，
不属于插件运行内容；它们不应由导出工具写入，也不应被本仓库覆盖。

## 2. 内容比较（engineering 布局，逐文件）

导出到 `dist/plugin_export_engineering/` 后与分发仓库逐文件比较（忽略 CRLF/LF 差异）：

| 结果 | 数量 | 说明 |
| --- | --- | --- |
| 路径对齐且内容一致 | **74** | `astrbot_plugin_digital_life/` 19 个 + `shared/` 55 个 |
| 路径对齐但内容不同 | **4** | 见下 |
| 仅本仓库导出有 | **2** | `SOURCE_MANIFEST.json`、`shared/memory_engine/README.md` |
| 仅分发仓库有 | **121** | `archive/` 91、`verification/` 16、`tests/` 8、`tools/` 2、`docs/` 1、根 `.gitignore`/`LICENSE`/`README.md` 3 |

内容不同的 4 个文件都是文档，差异来自本轮 PR2 的同步：

- `astrbot_plugin_digital_life/README.md`（构建/验包命令、删失效测试引用）
- `shared/astrbot_memory_input/README.md`（旧 `host_shadow` 接线 → 统一插件）
- `shared/dlm_console/README.md`（插件页关系、去掉已归档的同步脚本）
- `shared/memory_output/README.md`（注入接线改为 ASSIST 模式）

**没有发现分发仓库独有的代码修复**：74 个对齐文件内容一致，说明插件与共享包没有在分发
仓库里被单独改过（或改动已包含在当前源码里）。

## 3. 三种导出布局

`tools/export_astrbot_plugin.py --layout` 支持三种映射，内容都来自同一份打包清单：

| 布局 | 路径结构 | 用途 | 验证状态 |
| --- | --- | --- | --- |
| `archive`（默认） | `astrbot_plugin_digital_life/<pkg>/…`，与 ZIP 完全一致 | 与已验包字节对应、可追溯 | 已验证（导出 79 文件，`--check` 通过） |
| `engineering` | 根 `astrbot_plugin_digital_life/` + `shared/<pkg>/` | 对齐分发仓库现状，差异可审查 | 已验证（74 一致 / 4 文档差异 / 2 新增） |
| `flat` | 插件文件在仓库根 | 仓库安装（AstrBot 要求根目录有 `metadata.yaml`） | **未验证**：没有真实仓库安装结果，不声明可用 |

## 4. 首次导出方案（待人工执行）

```powershell
python tools/export_astrbot_plugin.py --layout engineering --output dist/plugin_export_engineering
python tools/export_astrbot_plugin.py --layout engineering --check dist/plugin_export_engineering
```

1. 把 `dist/plugin_export_engineering/` 与分发仓库工作树做一次人工 diff：
   预期只有上表 4 个文档差异 + 2 个新增文件；
2. 确认 `archive/`、`tests/`、`tools/`、`verification/` 的处置（保留、单独归档或另开 PR 退役），
   导出工具不会删除它们；
3. 用 `SOURCE_MANIFEST.json` 记录来源提交（本轮为 `null`，因为工作树没有 `.git`）；
4. 由人提交并推送，工具本身不推送、不建 Release。

## 5. 结论与限制

- 两端差异已可解释：74 一致、4 文档落后、2 新增、121 属旧工程包脚手架。
- 首次导出应使用 `engineering` 布局，把差异压到文档更新，便于评审。
- `flat` 布局（仓库安装）尚未验证，不能在文档里声明"可从仓库安装"。
- 本轮没有推送权限/没有 `git`，分发仓库尚未更新；下次在干净克隆上重跑导出并写入真实来源提交。
