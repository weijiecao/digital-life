# 维护者流程：从改一行代码到发布

> 面向维护本仓库的人。除标明"需要真实环境"的步骤外，命令都在仓库根目录执行。
> 用户侧安装说明见 `dev/astrbot_plugin_digital_life/README.md`。

## 总览

```text
改代码 → 本地回归 → 仓库检查 → 构建 → 验包 → 记录证据 → 提交/评审 → 发布 → 单向导出
   ↑                                                                              │
   └──────────────────────────── 回退（revert / 新候选标识）───────────────────────┘
```

单一源码来源：**本仓库**。
`weijiecao/astrbot_plugin_digital_life` 是分发仓库，只能由本仓库单向导出，不反向同步。

## 0. 环境

```powershell
git clone https://github.com/weijiecao/digital-life.git
cd digital-life
python -m pip install -r dev/requirements-release.txt
```

需要 Python 3.10+；`requirements-release.txt` 只固定 `tiktoken`（精确预算校验用）。
真实 AstrBot/NapCat 环境用 `env/setup.ps1` 重建到 `testbed/`，不要提交进去。

## 1. 改代码

| 改动 | 位置 |
| --- | --- |
| 插件入口、应用服务、管理 UI | `dev/astrbot_plugin_digital_life/` |
| 输入、实时追加、历史对账 | `dev/astrbot_memory_input/` |
| 召回、准入、上下文、预算 | `dev/memory_engine/` |
| 输出协议与注入 | `dev/memory_output/` |
| 基座、Resolver、租约 | `dev/v1_5_consolidator/` |
| 控制台 | `dev/dlm_console/` |
| 构建/验包/导出 | `tools/` |
| 文档 | `README.md`、`docs/`、`dev/README.md`、各模块 README |

不要修改 `archive/` 里的实现来"修好"活动代码；活动代码也不得 `import archive`。

## 2. 本地回归

```powershell
python tools/run_regression.py
```

输出：屏幕摘要 + `dist/regression.json`（用例数、跳过原因、耗时、环境）+ `dist/regression.log`。
这就是文档里数字的唯一来源；不要手抄到别处。

只跑某个文件：

```powershell
cd dev
python -m unittest -q tests.test_unified_plugin
```

## 3. 仓库检查

```powershell
python tools/check_repository.py
```

检查项：声明的入口是否存在、活动代码是否引用归档、活动/归档测试是否重复、
版本是否只有一个来源、文档链接与命令是否有效、CI 是否只调用存在的入口、
打包清单的源目录是否齐全、`dist/` 与 `.local/` 是否被忽略。
`--json` 输出同样内容，便于 CI 存档。

## 4. 构建

```powershell
python tools/build_unified_plugin.py --out-dir dist
```

- 文件清单来自 `tools/plugin_package.json`：插件宿主目录 + 5 个共享包，映射进
  `astrbot_plugin_digital_life/` 既有布局；
- 换行按仓库 `.gitattributes` 规范化（文本 LF，`*.ps1/*.cmd` CRLF，二进制不动）；
- ZIP 时间戳、权限、压缩参数、成员顺序固定，同一源码连续构建两次哈希一致；
- 输出 `dist/astrbot_plugin_digital_life-<version>.zip`、`.sha256`、`build_manifest.json`。

正式交付构建必须从干净提交开始，并加 `--require-clean`：

```powershell
python tools/build_unified_plugin.py --out-dir dist --require-clean
```

没有 `.git` 时 `source_commit` 为 `null`、`dirty` 为 `null`，`--require-clean` 会拒绝。

## 5. 验包

```powershell
python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json
python tools/verify_unified_plugin.py --dist dist --self-test
```

第一行做 16 项检查（成员安全、逐文件哈希、必需文件、metadata、配置 schema、UI 资源、
编译、共享包导入、插件导入）；第二行证明缺文件、篡改、路径穿越、重复成员会被拒绝。

插件模块的导入在无 AstrBot 时使用**标注的宿主 stub**（`host: stub`，只算离线通过）。
有 AstrBot 源码时，用同一个解释器环境把真实源码接进来，报告会写 `host: astrbot-source`：

```powershell
testbed\.venv\Scripts\python.exe tools\verify_unified_plugin.py --dist dist `
  --host-path testbed/AstrBot-4.29.3 --report dist/package_verification.json
testbed\.venv\Scripts\python.exe tools\check_astrbot_install.py --dist dist `
  --astrbot-root testbed/AstrBot-4.29.3 --report dist/astrbot_install_check.json
```

第一条用真实 AstrBot 4.29.3 源码导入插件模块；第二条用 AstrBot 自带的
`_PluginUpdater.inspect_plugin_archive` 校验归档，期望 metadata 入口以
`astrbot_plugin_digital_life/metadata.yaml` 结尾。两者都不启动实例，仍不等于真实宿主验收。

注意：`--host-path` 需要能导入 AstrBot 依赖的解释器（用 `testbed\.venv` 那个），
系统 Python 缺少 AstrBot 依赖时会明确报 `host path does not contain an importable astrbot package`。

## 6. 记录证据

```powershell
python tools/verify_unified_plugin.py --dist dist --self-test --self-test-report dist/selftest.json
python tools/record_acceptance.py
```

`record_acceptance.py` 从 `dist/regression.json`、`dist/build_manifest.json`、
`dist/package_verification.json`、`dist/selftest.json` 与 `dist/plugin_export/` 读出数字，
写出 `dev/reports/current_acceptance.json`，并自动与 `releases/` 里的上一份交付比对
（新增/删除/变化的文件）。**不要手写这个文件**。

写完后确认：

- `tested_commit` / `dirty` / `plugin_version` / `artifact_sha256` 与实际产物一致；
- 用例数来自 `dist/regression.json`，不是手抄；
- 失败或跳过写明原因，不能把跳过写成通过。

## 7. 提交与 CI

- 提交信息用 `feat:` / `fix:` / `docs:` / `test:` / `chore:`；
- 一个 PR 做一件事；改行为必须带测试；
- CI（`.github/workflows/tests.yml`）：
  - `regression`：Windows/Ubuntu × Python 3.10/3.11/3.12，跑仓库检查与全量回归，
    上传 `regression.json`/`.log`；
  - `package`：只在 Ubuntu/Python 3.12 这一规范环境构建、验包、负例与双次导出比对，
    上传 ZIP、清单与验包 JSON。

CI 证明"离线逻辑没坏"，不能代替 `testbed/` 的真实宿主验证。

## 8. 发布

1. 定版本：编辑仓库根 `VERSION`，执行 `python tools/sync_version.py`。
   非法格式、空值、派生漂移都会让 `--check` 失败。
2. 从干净提交构建并验包：

   ```powershell
   python tools/sync_version.py --check
   python tools/run_regression.py
   python tools/build_unified_plugin.py --out-dir dist --require-clean
   python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json
   python tools/run_package_regression.py --dist dist --suites completion,core,legacy
   python tools/export_astrbot_plugin.py --layout engineering --output dist/plugin_export
   ```

3. 生成并校验完整交付集合：

   ```powershell
   python tools/build_engineering_bundle.py --dist dist --out-dir dist/release --notes <说明.md>
   python tools/check_release_set.py --release-dir dist/release
   ```

4. 生成验收记录（自动从报告读取数字，并与构建/用例/导出交叉校验）：

   ```powershell
   python tools/record_acceptance.py
   ```

   正式记录要求干净提交；本地诊断可用 `--allow-uncommitted`，但 `ok` 会保持 false。
   小型报告会复制到 `dev/reports/acceptance/<提交>/`，即使 `dist/` 被清掉也能追溯。

5. 把当前插件包、发布清单与校验值放进 `releases/`，更新 `releases/README.md`；
   完整交付集合的 3 个 ZIP 作为 Release 附件。旧版本移到 `archive/releases/`，
   **不要**覆盖已有同名交付物。
6. 打 tag 在**被测源码提交 S** 上：`git tag v<VERSION> <S>`。证据归档提交 R 在标签之后，
   不改动 tag。用户下载入口固定在分发仓库的 Release；主仓库保留同名 tag 与索引，
   不另做内容不同的公开 Release。

## 9. 单向导出到分发仓库

```powershell
python tools/export_astrbot_plugin.py --output dist/plugin_export
python tools/export_astrbot_plugin.py --check dist/plugin_export
```

- 导出目录记录 `SOURCE_MANIFEST.json`：来源仓库、来源提交、导出工具版本、插件版本、
  逐文件哈希、关联包哈希、布局；
- 工具不推送、不建 Release，也不清空目标目录；
- 目标目录有本工具没创建的文件时默认拒绝；`--force` 前先人工看差异；
- `--check` 只读：报告缺失、被改、多余文件，发现漂移即退出非零；
- 分发仓库里的修复必须回到本仓库完成，再重新导出，不做双向同步。

布局用 `--layout` 选择（同一份打包清单）：

| 布局 | 路径结构 | 用途 |
| --- | --- | --- |
| `archive`（默认） | `astrbot_plugin_digital_life/<pkg>/…` | 与 ZIP 完全一致，可追溯到已验包 |
| `engineering` | 根 `astrbot_plugin_digital_life/` + `shared/<pkg>/` | 对齐现有分发仓库，差异最小 |
| `flat` | 插件文件在仓库根 | 仓库安装用；**尚未验证**，不声明可用 |

当前分发仓库仍是旧工程包布局；逐文件比较结果（74 一致 / 4 文档落后 / 2 新增 / 121 旧脚手架）
与首次导出步骤见 [`dev/reports/DISTRIBUTION_SYNC_2026-11-16.md`](../dev/reports/DISTRIBUTION_SYNC_2026-11-16.md)。

## 10. 回退

| 场景 | 做法 |
| --- | --- |
| 代码有问题 | `git revert <commit>`，不要 `reset --hard` 强推 |
| 包内容有问题 | 撤回候选，修好后用新候选标识重新构建；保留旧物与失败证据 |
| 发布说明写错 | 用新的版本说明更正，不删除历史 |
| 恢复数据 | 用管理页的备份列表恢复；不要手改 `CURRENT.json` |

## 11. 已知坑

1. **换行**：`.gitattributes` 决定入库与打包的换行。直接对任意字节做替换会破坏二进制。
2. **版本号**：仓库根 `VERSION` 是唯一人工来源；`metadata.yaml` 由 `tools/sync_version.py` 派生。不要在任何脚本或文档里再写一份版本常量。
3. **`dist/` 与 `*.zip` 被忽略**：构建产物不进源码提交，交付包放在 `releases/`。
4. **归档不是后备路径**：需要旧实现时迁回 `dev/` 并补测试，不要在活动脚本里加路径绕回。
5. **PowerShell 编码**：含中文的 `.ps1` 保存为 UTF-8 带 BOM，否则 Windows PowerShell 5.1 会乱码。
6. **测试通过 ≠ 真实可用**：真实 AstrBot/NapCat、Windows 实机、浏览器页面与 72h 长跑仍未验收。
