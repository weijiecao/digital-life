# 数字生命 · AstrBot 记忆插件完整工程

把「私聊原话存本机、需要时以**未确认线索**回注模型」做成可验证的工程：插件本体、336 项离线回归、
真实 AstrBot/真实 BGE/真实 QQ 的实机证据，以及**换机器也能复现的环境引导**。

| 入口 | 说明 |
|---|---|
| **本仓库** | 完整工程：`dev/` 开发工作区 + `testbed/` 实机环境 + `env/` 环境引导 |
| [插件发行仓库](https://github.com/weijiecao/astrbot_plugin_digital_memory_store) | 只含插件本体，AstrBot 用户装插件从这里拿（仓库根即插件根） |
| [Releases](https://github.com/weijiecao/digital-life/releases) | 完整包 zip + SHA256 |

## 这套东西是什么

AstrBot 插件 `astrbot_plugin_digital_memory_store`（v1.0.4）：白名单账号的**私聊纯文本**落本机
SQLite，自动整理成“待审线索”，每轮请求前给模型一段**有界上下文**（最多 450–600 字），
一律标注“未确认”，不自动确认事实；默认关闭，除显式开启的候选筛选器外不联网。

本仓库除了插件本体，还包含：

- **336 项离线回归**（`dev/test_*.py`，含上游基线继承、备份回归、注入契约、端到端链路）；
- **可观测假 AstrBot**（`dev/sim_astrbot.py`）与真实链路探针（`testbed/harness/real_chain.py`）；
- **实机环境**：AstrBot 4.29.3 + Python 3.12 + 真实 BGE（512 维）+ 本地 mock provider；
- **一键环境引导**：`env/setup.ps1`（建 venv、拉源码、装依赖、下模型、生成实例）。

## 目录

```
digital-life/
  README.md                  本文件
  env/                       环境配置：setup / verify / download_model / 锁文件 / 配置模板
    README.md                版本矩阵、镜像源、已知坑（先读这个）
  dev/                       开发工作区（权威开发线）
    astrbot_plugin_digital_memory_store/   插件源码
    test_*.py                336 项回归 + 探针
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
| 离线回归 | 336/336（`dev/STATUS.md` 为唯一数字来源，脚本生成） |
| 真实 AstrBot 4.29.3 | 插件加载、钩子注册（`OnLLMRequestEvent`）、命令过滤均通 |
| 真实 BGE | 512 维；冷加载 10.9–13.1 s；3.8–4.0 ms/条 |
| 注入可见 | mock provider 收到的请求体含 `[DL_MEMORY_V040]` 与旧原话；钩子→provider 0.13 s |
| 真实 QQ 私聊 | 小号经 NapCat 反向 WS 接入，主号两条私聊被采集并注入（账号已脱敏） |
| 冷启动 | 预热线程修复后首条回复 TTFB 8.0 s → 0.017 s |
| 真实聊天准确率 | **未验证**（缺脱敏真实语料） |
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
