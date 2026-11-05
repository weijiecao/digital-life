# 维护者流程：从改一行代码到发布

> 面向维护这个仓库的人（含未来的你）。全部命令都在本机实测过，Windows + PowerShell 5.1。
> 用户侧流程见插件仓库的 [`docs/QUICKSTART.md`](https://github.com/weijiecao/astrbot_plugin_digital_memory_store/blob/main/docs/QUICKSTART.md)。

## 总览

```
搭环境 → 改代码 → 本地回归 → 打包 → 用 AstrBot 校验 zip → 双仓同步 → 发版 → 发布后校验
   ↑                                                                              │
   └────────────────────────── 回滚（删 release / 移 tag）←────────────────────────┘
```

两个仓库，**同一个版本号**：

| 仓库 | 内容 | 谁用 |
|---|---|---|
| `astrbot_plugin_digital_memory_store` | 仓库根 = 插件根（AstrBot 要求） | AstrBot 用户 |
| `digital-life`（本仓库） | `dev/` 开发区 + `env/` 环境 + `testbed/` 实机 | 开发者 / 复现环境 |

## 1. 准备环境（一次性）

```powershell
git clone https://github.com/weijiecao/digital-life.git
cd digital-life
powershell -NoProfile -ExecutionPolicy Bypass -File .\env\setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\env\verify_env.ps1   # 应全绿
```

产物：`testbed\.venv`、`testbed\AstrBot-4.29.3`、`testbed\models\bge-small-zh-v1.5`、
`testbed\instance`。这些都**不入库**，靠脚本重建。

## 2. 改代码

- 插件源码：`dev/astrbot_plugin_digital_memory_store/`
- 测试：`dev/test_*.py`
- 文档：插件内 `README.md` / `INSTALL.md` / `docs/`；工程级 `env/README.md`、`docs/`、根 `README.md`
- **不要改**只读基线（`E5.0-F_0.15.116_工程包/`、`_e50f_standalone/`），它们是历史对照

## 3. 本地回归

```powershell
cd dev
# 快跑（12 个用例，约 1 秒）
..\testbed\.venv\Scripts\python.exe -m unittest -q test_metrics test_threshold test_preflight

# 全量（19 个 suite / 336 项，约 30 秒），并刷新 STATUS.md 与 reports/status.json
..\testbed\.venv\Scripts\python.exe scripts\report.py

# 手动等价写法（不想用 report.py 时）
..\testbed\.venv\Scripts\python.exe -m unittest -q test_store test_plugin test_baseline_alignment `
  test_preflight test_auto_jobs test_backup_regression test_prewarm test_model_judge test_delivery `
  test_soak test_threshold test_correction test_metrics test_real_corpus test_background `
  test_package test_attribution test_panel test_integration_chain
```

> 用 `unittest`，不是 `pytest`（仓库测试是 unittest 风格，环境里也没装 pytest）。
> `STATUS.md` 是脚本生成物，**不要手改**；它是唯一数字来源。

### CI（GitHub Actions）

`.github/workflows/tests.yml` 在 `windows-latest` + Python 3.12 上跑同一套 337 项，push 到 `main`
或开 PR 即触发。它**不需要 torch/模型、不联网**：测试全部用替身（stub 的 AstrBot、假编码器）。

注意边界：CI 证明的是“逻辑没坏”，不能替代 `testbed/` 的真实链路验证（真实 AstrBot + BGE 必须本机跑）。

## 4. 打包

```powershell
cd ..            # 回到 数字生命\ 或 digital-life\ 的上一级工作区
python dev\scripts\build_package.py
```

产物：`dist\astrbot_plugin_digital_memory_store-<version>.zip` 与同名 `.sha256`。
脚本自动排除 `__pycache__`、`.git`、`.venv`、内部文档（`测试前功能清单.md`、`项目节点进度.md`）。

**发版前必须改版本号**：`dev/astrbot_plugin_digital_memory_store/metadata.yaml` 的 `version`。
zip 文件名、`dist` 校验和都从它来。

## 5. 用 AstrBot 自己的校验器验证 zip

别只看“能不能解压”，要让 AstrBot 的真实代码认这个包：

```powershell
cd testbed\AstrBot-4.29.3
..\.venv\Scripts\python.exe -c "from astrbot.core.star.updater import _PluginUpdater as U; print(U.inspect_plugin_archive(r'<zip 绝对路径>')['metadata_entry'])"
```

期望输出：`astrbot_plugin_digital_memory_store/metadata.yaml`。这一步能提前发现
`metadata.yaml` 缺字段、编码不对、目录层数不对——WebUI 上传和“从仓库安装”都走这条校验。

## 6. 双仓同步

插件仓库发版后，把插件副本同步到本仓库，保证 `dev/` 不落后：

```powershell
$src = 'C:\Users\<you>\Desktop\数字生命\dev\astrbot_plugin_digital_memory_store'
$dst = 'C:\path\to\digital-life\dev\astrbot_plugin_digital_memory_store'
robocopy $src $dst /E /XD __pycache__ .git .github /XF *.pyc 测试前功能清单.md 项目节点进度.md PUBLISH_GITHUB.md
```

`.github/`（issue 模板）只属于插件仓库，不同步；`测试前功能清单.md`、`项目节点进度.md` 是内部文档，
`dev/docs/PUBLISH_GITHUB.md` 是本机运维笔记（含代理与本地路径），三者都不入库也不入包。

`robocopy` 退出码 `0–7` 都是成功（`1` = 有文件被复制），`8+` 才是失败。

同步后检查根 `README.md` 里的版本号引用，别留下旧版本。

## 7. 发版

### 7.1 插件仓库（用户装的那个）

```powershell
cd dev\astrbot_plugin_digital_memory_store
git add -A
git commit -m "release: v1.0.x 一句话说明"
git push origin main
git tag v1.0.x
git push origin v1.0.x
gh release create v1.0.x "<dist>\astrbot_plugin_digital_memory_store-1.0.x.zip" `
  "<dist>\astrbot_plugin_digital_memory_store-1.0.x.zip.sha256" `
  --repo weijiecao/astrbot_plugin_digital_memory_store `
  --title "v1.0.x — 一句话" --notes-file RELEASE_NOTES.md --latest
```

### 7.2 完整工程仓库（本仓库）

```powershell
git add -A
git commit -m "sync: 插件同步到 v1.0.x"
git push origin main
git tag v1.0.x
git push origin v1.0.x
git archive --format=zip --prefix=digital-life-1.0.x/ -o dist\digital-life-v1.0.x.zip HEAD
```

再对归档算校验和、建 release（附件是 `digital-life-v1.0.x.zip` + `.sha256`）。

## 8. 发布后校验

```powershell
# 附件摘要必须与本地一致
gh release view v1.0.x --repo weijiecao/digital-life --json assets --jq '.assets[] | {name, digest}'
Get-FileHash dist\digital-life-v1.0.x.zip -Algorithm SHA256

# 抽查 GitHub 上的原文（防止只提交了本地）
(Invoke-WebRequest https://raw.githubusercontent.com/weijiecao/digital-life/main/README.md -UseBasicParsing).StatusCode
```

## 9. 回滚

| 场景 | 做法 |
|---|---|
| Release 附件传错 | `gh release upload v1.0.x <新文件> --clobber`，再核对 digest |
| 版本内容不对 | `gh release delete v1.0.x --yes`；本地 `git tag -d`、`git push origin :v1.0.x`，修好后重发 |
| 想回到上一版给用户 | 把上一版 Release 设为 latest：`gh release edit v1.0.y --latest` |
| 代码回退 | `git revert <commit>`（不要 `reset --hard` 强推，会破坏别人 clone） |

## 10. 已知坑

1. **PowerShell 5.1 读 UTF-8 无 BOM 会乱码**，进而语法报错。仓库里的 `.ps1` 统一带 BOM，
   **用编辑器改完别保存成“UTF-8 无 BOM”**。新增脚本后跑一次：
   `[System.Management.Automation.Language.Parser]::ParseFile($f,[ref]$null,[ref]$errs)`。
2. **执行策略**：默认禁止跑脚本，统一用 `powershell -NoProfile -ExecutionPolicy Bypass -File ...`。
3. **代理**：本机直连 GitHub 会 `Connection was reset`，两个仓库的 `.git/config` 里写了
   `http.proxy=127.0.0.1:7897`；换端口/关代理后 push 会失败。
4. **版本号一致**：`metadata.yaml` 的 `version`、zip 文件名、tag、Release 标题四处必须对齐。
5. **zip 别夹带 `.git`**：`build_package.py` 已排除；手工打包时注意。
6. **测试通过 ≠ 真实可用**：真实链路的证据在 `dev/reports/real_env.json`、`dev/docs/REAL_ENV_REPORT.md`，
   别拿合成数据的数字对外承诺。
