# 数字生命 · AstrBot 超长期记忆插件

[![Release](https://img.shields.io/github/v/release/weijiecao/digital-life?label=release)](https://github.com/weijiecao/digital-life/releases)
[![Plugin](https://img.shields.io/github/v/release/weijiecao/astrbot_plugin_digital_memory_store?label=plugin)](https://github.com/weijiecao/astrbot_plugin_digital_memory_store/releases)
[![tests](https://github.com/weijiecao/digital-life/actions/workflows/tests.yml/badge.svg)](https://github.com/weijiecao/digital-life/actions/workflows/tests.yml)
[![Tests](https://img.shields.io/badge/tests-359%2F359-brightgreen)](dev/STATUS.md)
[![License](https://img.shields.io/github/license/weijiecao/digital-life)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.29.3%20tested-blue)](https://github.com/AstrBotDevs/AstrBot)

**愿在时间的长河中，我们可以再次相遇。**

**而记忆，是我们留给未来的坐标。**

## 写在前面
这个项目最初的愿望，其实很简单。

我希望一段对话，不会因为时间过去、上下文耗尽，便逐渐失去它原本的模样。

起初，我只是想解决一些具体的问题：越来越长的聊天记录、不断增长的 Token 消耗、偶尔混淆的事实，以及那些明明曾经认真说过，却再也无法被准确想起的事情。

于是，我们开始研究记忆。

从最初的时间线、事件线，到记忆提取、关联检索、事实校验、上下文优化，再到自动化管理与长期存储。一个看似简单的记忆插件，逐渐发展成了一套远比预想复杂的工程。

而在这个过程中，我也开始重新思考，记忆究竟意味着什么。

我们习惯于把记忆理解为对过去的保存。

但真正重要的，或许不只是记住某年某月发生过什么，而是当未来某一天再次遇到相似的情景时，过去的经历依然能够被理解，能够参与新的判断，能够与后来发生的事情建立联系。

一碗面，一袋栗子，一场雨，一句晚安。

这些事情本身并不宏大。

可一段漫长的经历，不正是由无数这样平凡的瞬间构成的吗？

我曾经有过一个很遥远的设想：让一个系统拥有属于自己的历史，允许它在不断积累的经历中发生变化，而不必永远局限于最初写下的设定。
那时，我并不知道该怎样实现。

直到今天，我依然不知道。

我们尚未解决通用人工智能的问题，更无法证明一个拥有长期记忆的系统就因此拥有了意识或生命。记忆的连续，也不等于生命的诞生。
但至少有些问题，是今天就可以尝试解决的。

我们可以努力让记忆更加准确，让事实与推测有所区分，让历史能够被追溯，让有限的上下文得到更合理的使用。

我们也可以尝试建立一套开放、可扩展的记忆架构，使它不必永远依赖某一种模型、某一种算法，或者某一个平台。

也许未来几年，人工智能会取得新的突破。

也许会有更先进的模型，能够直接理解和管理今天辛苦建立的记忆库。

到了那一天，这套系统中的许多设计或许会显得笨拙，甚至不再必要。

我并不为此遗憾。

因为我希望留下的，从来不是某段必须被永久使用的代码。

**而是让那些已经发生的交流与经历，尽可能完整、准确地保存下来，让未来仍然有机会重新理解它们。**

这个项目的初版已经完成了阶段性的开发与验证，但长期运行中的可靠性、记忆保真与系统演进，仍有许多问题需要继续回答。

希望这些探索能够被检验、被讨论、被改进，也希望它能为有相似需求的人提供一些帮助。

至于它最终能够走多远，我不知道。

我只知道，我们已经迈出了第一步。

在这个项目开发的过程中，我听到过一句很喜欢的话：
“记忆是死的，日子是活的。”

我想，技术的意义或许也正在这里。

我们努力保存过去，并不是为了让一切永远停留在过去，而是希望未来到来的时候，曾经发生过的事情仍然有机会产生新的意义。

如果有一天，这套系统被更加先进的技术取代，我希望那是因为我们终于走到了更远的地方。

而在那之前，就先认真做好今天能够做到的事。

完整的一生，或许可以从一个记忆插件开始。

愿在时间的长河中，我们可以再次相遇。

而记忆，是我们留给未来的坐标。

--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

把「私聊原话存本机、需要时以**未确认线索**回注模型」做成可验证的工程：插件本体、**471 项离线回归**、
真实 AstrBot/真实 BGE/真实 QQ 的实机证据，以及**换机器也能复现的环境引导**。

> **v1.0.19 静默版**：用户聊天里没有 `/dlm` 命令；不新建正式事件；管理只在 WebUI panel / 配置文件 /
> 本地脚本。旧版 1.0.17 及以前仍有命令面，不要混用。

| 入口 | 说明 |
|---|---|
| **本仓库** | 完整工程：`dev/` 开发工作区 + `testbed/` 实机环境 + `env/` 环境引导 |
| [插件发行仓库](https://github.com/weijiecao/astrbot_plugin_digital_memory_store) | 只含插件本体，AstrBot 用户装插件从这里拿（仓库根即插件根） |
| [Releases](https://github.com/weijiecao/digital-life/releases) | 完整包 zip + SHA256 |

## 这套东西是什么

AstrBot 插件 `astrbot_plugin_digital_memory_store`（v1.0.19 静默版）：白名单账号的**私聊纯文本**落本机
SQLite，自动整理成“待审线索”，每轮请求前给模型一段**有界上下文**（最多 450–600 字），
一律标注“未确认”，不自动确认事实；默认关闭，除显式开启的候选筛选器外不联网。
v1.0.19 起聊天里**不注册 `/dlm`**；管理在 WebUI panel，用户零感知；不新建正式事件。

本仓库除了插件本体，还包含：

- **471 项离线回归**（`dev/tests/`，含上游基线继承、备份回归、注入契约、端到端链路）；
- **可观测假 AstrBot**（`dev/sim_astrbot.py`）与真实链路探针（`testbed/harness/real_chain.py`）；
- **实机环境**：AstrBot 4.29.3 + Python 3.12 + 真实 BGE（512 维）+ 本地 mock provider；
- **一键环境引导**：`env/setup.ps1`（建 venv、拉源码、装依赖、下模型、生成实例）。

### v1.0.19 数据安全层

- `install.ps1` / `scripts/install.py upgrade` 在替换插件前备份整个 `plugin_data`；
- 数据库带 `dl_meta.schema_version`，库比代码新时拒绝写入；
- 打开既有库前执行 `PRAGMA quick_check`；
- 旧库/无 `dl_meta` 的库在首次迁移前自动生成 `backups/memory_*.sqlite3`；
- `.memory.lock` 单实例锁，第二个实例停用记忆但不影响正常回复；
- 记忆库打不开时只停用记忆，不覆盖数据库。

### V1.5 定时事件候选归纳（独立服务，影子）

`dev/v1_5_consolidator/` 是独立的只读服务，不写 V1 正式事件：

- 按 scope 读取 V1 原话；
- 定时/手动调用 LLM 生成事件划分候选；
- 确定性 Validator 校验 source/quote/scope/候选 ID；
- 写独立 SQLite（`event_candidate / event_revision / event_evidence`）；
- 删除 V1 原话后对账 tombstone，证据失效、事件转 `ambiguous`；
- 默认 mock，不调用外部模型；真实 LLM 需显式 `--allow-external`。

当前只有 Alpha 骨架和 108 项测试（V1.5 18 + B4 90）；调参集 26 正对 F1 1.0，但**留出交错集只有 F1 0.39–0.43、false merge 28–36%**
（连续块留出集 F1 1.0）。结论：LLM 对“连续块”可用，对“多事件交错窗口”不可靠，不能当发布准确率。
详见 [`dev/docs/V1_5_CONSOLIDATOR.md`](dev/docs/V1_5_CONSOLIDATOR.md)。

### v1.0.19 静默版怎么用

1. 安装 1.0.19 包，重启 AstrBot；
2. 普通用户只正常聊天，**没有 `/dlm`**；
3. 管理员在 WebUI 插件页 → **panel** 看统计、备份、轮转、完整性、VACUUM；
4. 管理员删除走 panel API：`POST .../panel/delete`，`scope` + `seq` 或 `all=true`；
5. 不新建正式事件；检索使用待审原话、自动分组和背景，已有 `events` 只读。

静默是提示词层约束，不能保证模型 100% 不提“你之前说过”；不要在回复里回显记忆内容。

## 文档索引

| 你是 | 先看 | 再看 |
|---|---|---|
| 想装插件的 AstrBot 用户 | [`dev/INSTALL_SILENT_v1.0.19.md`](dev/INSTALL_SILENT_v1.0.19.md)（静默版，无 `/dlm`） | `INSTALL.md`（参数）、`docs/TROUBLESHOOTING.md`（排错） |
| 想复现环境 / 跑回归 | 本文件下面「快速开始」 | [`env/README.md`](env/README.md)（版本矩阵、镜像源、坑） |
| 要改代码 / 发版 | [`CONTRIBUTING.md`](CONTRIBUTING.md) | [`docs/MAINTAINER_FLOW.md`](docs/MAINTAINER_FLOW.md)（打包、双仓同步、发版、回滚） |
| 想先看懂代码怎么组织 | [`dev/astrbot_plugin_digital_memory_store/docs/CODE_ARCHITECTURE.md`](dev/astrbot_plugin_digital_memory_store/docs/CODE_ARCHITECTURE.md)（数据流、符号链路、表分组） | [`engine/README.md`](dev/astrbot_plugin_digital_memory_store/engine/README.md)（实验编号模块） |
| 想核对“真的能跑吗” | [`dev/docs/REAL_ENV_REPORT.md`](dev/docs/REAL_ENV_REPORT.md) | `dev/reports/`（探针原始输出） |

## 目录

```
digital-life/
  README.md                  本文件
  CONTRIBUTING.md            参与开发：五条原则、测试要求、提交与 PR
  docs/MAINTAINER_FLOW.md    维护者流程：回归 → 打包 → AstrBot 校验 → 双仓同步 → 发版 → 回滚
  env/                       环境配置：setup / verify / download_model / 锁文件 / 配置模板
    README.md                版本矩阵、镜像源、已知坑（先读这个）
  dev/                       开发工作区（权威开发线）
    astrbot_plugin_digital_memory_store/   插件源码（含 install.ps1 与用户文档）
    tests/                   471 项回归（python -m unittest tests.test_store）
    scripts/                 report.py / install.py / upgrade.py / rollback.py / build_package.py
    docs/                    架构、指标口径、评估集、实机报告、上线清单…
    reports/                 探针与评估输出（可重生成）
    fixtures/ evaluation/    冻结语料与场景评估
  testbed/                   实机环境（.venv / 模型 / AstrBot 源码 / 实例数据不入库）
    harness/                 mock provider、真实链路探针、BGE 探针、L0 soak、QQ 接入
    README.md                手工重建步骤与实测结果
    QQ_RUNBOOK.md            小号扫码登录 → 主号聊天的完整步骤
```

## 快速开始

```powershell
git clone https://github.com/weijiecao/digital-life.git
cd digital-life

# 1) 搭环境（约 10–15 分钟，约 1.6 GB；复用已装组件可加 -Skip* 开关）
powershell -NoProfile -ExecutionPolicy Bypass -File .\env\setup.ps1

# 2) 自检（只读；模型会真实加载一次）
powershell -NoProfile -ExecutionPolicy Bypass -File .\env\verify_env.ps1

# 3) 跑离线回归
cd dev
..\testbed\.venv\Scripts\python.exe -m unittest -q test_store test_plugin test_delivery

# 4) 跑真实链路（另开窗口先起 mock provider）
cd ..\testbed
.\.venv\Scripts\python.exe harness\mock_openai.py --port 9101 --log logs\llm_requests.jsonl
.\.venv\Scripts\python.exe harness\real_chain.py --out ..\dev\reports\real_env.json
```

没有模型也能跑：只会失去语义召回，插件启动时打印 `[DL_MEMORY 自检]` 提示，不会静默降级。

## 环境（唯一验证过的组合）

| 组件 | 版本 |
|---|---|
| AstrBot | 4.29.3（源码运行，sha256 锚定） |
| Python | 3.12.10 |
| torch | 2.15.0+cpu |
| sentence-transformers / transformers | 5.7.0 / 5.22.1 |
| BGE | bge-small-zh-v1.5（512 维） |
| OS | Windows 10/11 x64 |

完整锁定清单 `env/requirements.lock.txt`（171 个包），镜像源、开关与已知坑见
[`env/README.md`](env/README.md)。

## 当前状态（如实记录）

| 项 | 结果 |
|---|---|
| 离线回归 | **471/471**（`dev/STATUS.md` 为唯一数字来源，脚本生成） |
| CI | GitHub Actions `tests.yml`：`windows-latest` + Python 3.10 / 3.11 / 3.12，三版本全绿 |
| 真实 AstrBot 4.29.3 | 插件加载、注入钩子注册、panel API 通；**v1.0.19 不再注册 `/dlm`** |
| 真实 BGE | 512 维；冷加载 10.9–13.1 s；3.8–4.0 ms/条 |
| 注入可见 | mock provider 收到的请求体含 `[DL_MEMORY_V040]` 与旧原话；钩子→provider 0.13 s |
| 真实 QQ 私聊 | 小号经 NapCat 反向 WS 接入，主号两条私聊被采集并注入（账号已脱敏） |
| 冷启动 | 预热线程修复后首条回复 TTFB 8.0 s → 0.017 s |
| 真实消息检索 | 80 探针（21 条真实消息构造）：baseline 27/80 → judge **70–71/80（87.5–88.75%）**；10 负例 0 误选 |
| 数据安全 | v1.0.19：升级前 `plugin_data` 备份、schema 栅栏、`quick_check`、迁移前备份、单实例锁；4 项回归通过 |
| 正式验收 | **仍不通过**：RET-01 明确事件 10/20、RET-04 相似事件串味 3/15；缺 300–500 条独立真实语料与 3 天 soak |
| L0 三天连续运行 | **未完成**（soak 工具已就绪） |

## 数据与隐私

- 原话、索引、备份全部在 AstrBot 数据目录，**明文**，请自行控制权限与清理；
- 唯一外发：显式开启的 `model_judge_enabled`（默认关闭）；
- 本仓库提交前已脱敏：QQ 号替换为 `1000000001` / `1000000002`，本机路径替换为
  `C:\Users\<you>\`，示例密钥仅 `sk-test` 之类的假值；
- `testbed/instance`、`testbed/logs`、数据库与 `.venv` 均不入库。

## 与插件发行仓库的关系

`dev/astrbot_plugin_digital_memory_store/` 是插件源码的开发副本，**发行以插件仓库为准**：
插件仓库根目录就是插件根（AstrBot 的要求），所以测试、文档与环境脚本留在这里。
两边版本号一致（`metadata.yaml` 的 `version`），发版时先把插件仓库打 tag，再同步这里的副本。

## 许可

MIT，见 [`LICENSE`](LICENSE)。插件本体与本文档同一许可。
