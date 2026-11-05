# 参与开发

这个仓库同时装着“给用户装的插件”和“证明它可信的证据”。贡献前请先看下面五条**不能破坏**的原则。

## 五条原则（改动前先对照）

1. **不自动确认事实**：注入内容只作为“未确认线索”，任何自动改写事实、自动合并关系的改动都不接受；
2. **默认关闭**：新增能力默认 `false`，由用户显式开启；
3. **默认不联网**：除用户显式开启的候选筛选器外，不外发原话；
4. **只处理私聊纯文本**：群聊、图片、语音、多设备同步是明确的 Non-goals；
5. **不夸大**：没有真实语料验证的准确率，不写进 README；测试数字只引 `dev/STATUS.md`。

## 报问题

提 issue 前先跑插件文档里的三条诊断命令（
[`docs/TROUBLESHOOTING.md`](https://github.com/weijiecao/astrbot_plugin_digital_memory_store/blob/main/docs/TROUBLESHOOTING.md)），
并附：AstrBot 版本、插件版本、`/dlm 上下文状态` 输出、相关日志（**不要贴真实聊天内容或 QQ 号**）。

## 本地开发

```powershell
git clone https://github.com/weijiecao/digital-life.git
cd digital-life
powershell -NoProfile -ExecutionPolicy Bypass -File .\env\setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\env\verify_env.ps1
```

环境约 1.6 GB（venv + AstrBot 源码 + BGE 模型），全部由脚本重建，不入库。

## 改代码

| 想改 | 改哪里 |
|---|---|
| 插件行为 | `dev/astrbot_plugin_digital_memory_store/` |
| 测试 | `dev/test_*.py`（unittest 风格） |
| 用户文档 | 插件内 `README.md`、`INSTALL.md`、`docs/QUICKSTART.md`、`docs/TROUBLESHOOTING.md` |
| 环境脚本 | `env/`（`.ps1` 必须带 UTF-8 BOM，PowerShell 5.1 才不乱码） |
| 维护流程 | `docs/MAINTAINER_FLOW.md` |

**不要动**只读基线目录（`E5.0-F_0.15.116_工程包/`、`_e50f_standalone/`），它们是历史对照；
也不要手改 `dev/STATUS.md`（脚本生成）。

## 测试要求

改行为就必须有用例；跑全量并刷新 STATUS：

```powershell
cd dev
..\testbed\.venv\Scripts\python.exe scripts\report.py
```

- 用 `unittest`，不是 `pytest`；
- 新增测试会被 `report.py` 的 `SUITES` 收集吗？只有列在里面的才会进全量，记得加；
- 涉及真实环境（AstrBot/BGE/QQ）的结论，请附 `dev/reports/` 下的证据文件，而不是口头说明。

## 提交与 PR

- 提交信息用 `feat:` / `fix:` / `docs:` / `test:` / `chore:` 前缀，写清“为什么”而不只是“改了什么”；
- 一个 PR 只做一件事；文档改动和代码改动尽量分开，方便回滚；
- PR 描述里贴：跑了哪些测试、是否有真实环境证据、有没有破坏上面五条原则；
- 版本号只在发版时改（`metadata.yaml`），日常提交不要动。

## 不接受

- 把 `.venv`、模型、AstrBot 源码、实例数据库提交进仓库（体积 + 可移植性）；
- 默认联网 / 默认上传原话 / 默认自动确认事实；
- 为了“看起来更好”而修改测试或跳过失败用例；
- 在 README 里写未经验证的准确率。

## 许可

贡献即同意以 MIT 许可发布（见 [`LICENSE`](LICENSE)）。
