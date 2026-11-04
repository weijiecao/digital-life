# testbed — E5.0-F 真实运行环境（从零搭建）

> 目的：把"实机验收"从"等外部提供"变成本机可复跑。
> 版本锚点：AstrBot **v4.29.3**（与插件文档声明的目标版本一致）+ Python 3.12 + BGE **bge-small-zh-v1.5**。
> 建立时间：2026-11-03。所有组件都在本目录内，不碰用户正在运行的机器人。

## 目录

```
testbed/
  AstrBot-4.29.3/             AstrBot 源码（GitHub tag v4.29.3，未修改）
  .venv/                      Python 3.12 venv（AstrBot 依赖 + torch CPU + sentence-transformers）
  models/bge-small-zh-v1.5/   BGE 模型（ModelScope，含 model.safetensors）
  instance/                   本实例的 AstrBot 根目录（= ASTRBOT_ROOT）
    data/cmd_config.json      最小配置：webchat 平台 + 本地 mock provider
    data/config/…_config.json 插件配置（enabled、白名单、model_dir）
    data/plugins/…            插件副本（从 dev/ 同步）
    data/plugin_data/…        插件运行时数据库（实机证据在这里）
  harness/
    mock_openai.py            OpenAI 兼容 mock（记录完整请求体 = 注入证据）
    real_chain.py             真实链路探针（CoreLifecycle + WebChat + 插件）
    bge_probe.py              真实 BGE 维度/加载/编码耗时
    boot_check.py             只看平台/插件/端口是否就绪
    napcat_connect.py         把小号 NapCat 接到 testbed 的反向 WS
    soak.py                   L0 连续运行记录器（JSONL + 汇总）
    schedule_soak.ps1         注册/查看/启停计划任务（当前用户，登录启动）
    run_soak_task.cmd         计划任务实际执行的批处理
  docs/QQ_RUNBOOK.md          小号扫码登录 → 主号聊天的完整步骤
  logs/                       mock 请求日志、pip 日志
```

## 为什么不是"再装一个 AstrBot 给 QQ 用"

本机已有一个由 NapCatQQ Desktop 托管的生产实例（`C:\ProgramData\NapCatQQ Desktop\apps\astrbot\57f01f36`，
AstrBot 4.29.3，接 QQ 与 deepseek-flash）。按《远程执行单》的边界，**不碰生产机器人**。

因此这个 testbed：

- 用同一版本源码起**独立实例**，端口/数据目录全隔离；
- 用 AstrBot 自带的 **WebChat 适配器**注入"私聊消息"，走真实事件总线与插件管线；
- 用**本地 mock 的 OpenAI 兼容 provider**替代真实模型服务：不花钱、不联网、不把原文发给第三方，
  并且能拿到"发给模型的完整请求体"——这正是"注入是否真的生效"的判据。

QQ/NapCat 这一层仍由用户侧真实账号完成；本 testbed 覆盖 ①(AstrBot 侧)→⑤。

## 运行

```powershell
cd testbed
# 1) 启动 mock provider（独立进程，保持运行）
.\.venv\Scripts\python.exe harness\mock_openai.py --port 9101 --log logs\llm_requests.jsonl
# 2) 跑真实链路探针
.\.venv\Scripts\python.exe harness\real_chain.py --out ..\dev\reports\real_env.json
# 3) 只测 BGE
.\.venv\Scripts\python.exe harness\bge_probe.py --model-dir models\bge-small-zh-v1.5
```

`instance/data/cmd_config.json` 已配置：`webchat` 平台、`mock/mock-model` provider（指向 127.0.0.1:9101）、
`t2i=false`（回复走纯文本）、`dashboard.enable=false`（避免与生产实例的 6185 端口冲突）。

## 证据在哪

| 问题 | 证据 |
|---|---|
| 插件是否被真实 AstrBot 加载 | `real_env.json` 的 `plugin.loaded` / `plugin.handlers` |
| 钩子是否被调用、注入是否可见 | mock 日志里的请求体是否含 `[DL_MEMORY_V040]` 与旧原话 |
| 采集是否落库 | `instance/data/plugin_data/…/manual_test_memory.sqlite3` 的 `dl_capture` |
| 命令路径是否可用 | `/dlm 记忆状态` 的真实回复文本 |
| 真实 BGE | `dimension` / `load_seconds` / `encode_ms_per_item` |
| 端到端耗时 | 发消息 → provider 收到请求的时间差 |

## 实测结果（2026-11-04）

| 项 | 结果 |
|---|---|
| AstrBot 版本 | 4.29.3（与插件文档声明一致） |
| 插件加载 | ✅ 出现在 `stars` 列表 |
| 钩子注册 | ✅ `add_memory_context(OnLLMRequestEvent)`、`capture_private`/`dlm(AdapterMessageEvent)` |
| 采集落库 | ✅ `dl_capture` 2 行（种子消息 + 查询消息） |
| 注入可见 | ✅ mock 收到的真实请求体含 `[DL_MEMORY_V040]` 与旧原话 |
| 钩子→provider | 0.13 s（含真实 BGE 查询编码） |
| 命令路径 | ✅ `/dlm 记忆状态` 返回真实统计 |
| 真实 BGE | 512 维 / 冷加载 10.9–13.1 s / 3.8–4.0 ms 每条 |
| 后台任务排空 | 2.06 s（BGE 加载完成后） |
| **真实 QQ 私聊** | ✅ 小号 `1000000001`（NapCat 4.18.41 / QQNT 9.9.36）反向 WS 连到 6201 |
| QQ 采集 | 2 条，独立作用域（seq 3/4） |
| QQ 注入 | 第 2 条请求体含 `[DL_MEMORY_V040]`，引用待审序号 3「我喜欢吃苹果」 |
| 模型预热（F1 修复后） | 加载中回复 12 ms、本轮不注入；加载完成首轮注入 89 ms；后台积压 0 s |

**冷启动问题（真实环境才暴露）**：首次 BGE 加载占满单线程池 → `dl_auto_jobs` 积压 →
插件按设计"有积压时不给自动线索" → 前几轮对话拿不到记忆。排空后同一查询 0.13 s 注入。
详见 `../dev/docs/REAL_ENV_REPORT.md`。

## 一键重建（推荐）

本仓库带环境引导脚本，不用手抄下面的命令：

```powershell
# 仓库根执行：建 venv → 下 AstrBot 4.29.3 → 装依赖(torch CPU) → 下 BGE → 生成 instance
powershell -NoProfile -ExecutionPolicy Bypass -File .\env\setup.ps1
# 只检查不修改
powershell -NoProfile -ExecutionPolicy Bypass -File .\env\verify_env.ps1
```

版本锚点、依赖锁定与模板说明见 `../env/README.md`；下面是**手工**重建步骤（脚本等价物，排错时用）。

## 从零重建（本次实际执行过的命令）

> 耗时约 10–15 分钟，占用约 1.6 GB。全部走本机可达的源：GitHub / PyPI 清华镜像 /
> download.pytorch.org(cpu) / ModelScope。HuggingFace 直连本机不可达。

```powershell
cd testbed

# 1) AstrBot v4.29.3 源码
curl.exe -L -o downloads\astrbot_v4.29.3.zip https://github.com/AstrBotDevs/AstrBot/archive/refs/tags/v4.29.3.zip
Expand-Archive downloads\astrbot_v4.29.3.zip -DestinationPath . -Force

# 2) venv + AstrBot 依赖 + torch(CPU) + sentence-transformers
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r AstrBot-4.29.3\requirements.txt
.\.venv\Scripts\python.exe -m pip install --index-url https://download.pytorch.org/whl/cpu torch
.\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "sentence-transformers>=2.7,<6"

# 3) BGE 模型（ModelScope 的 bge-small-zh-v1.5，只用 safetensors）
$base = "https://www.modelscope.cn/models/AI-ModelScope/bge-small-zh-v1.5/resolve/master"
$dst = "models\bge-small-zh-v1.5"; New-Item -ItemType Directory -Force -Path $dst,"$dst\1_Pooling" | Out-Null
foreach ($f in 'config.json','config_sentence_transformers.json','modules.json','sentence_bert_config.json',
               'special_tokens_map.json','tokenizer.json','tokenizer_config.json','vocab.txt',
               '1_Pooling/config.json','model.safetensors') {
  curl.exe -s -L -o (Join-Path $dst $f) "$base/$f"
}

# 4) 插件 + 实例配置（instance/ 下的两个 json 见本仓库，已写好 mock provider 与 webchat）
robocopy "..\dev\astrbot_plugin_digital_memory_store" "instance\data\plugins\astrbot_plugin_digital_memory_store" /E /XD __pycache__
```

注意：`modules.json` 里写了 `2_Normalize`，但 ModelScope 与 hf-mirror 都没有该目录文件；
`sentence-transformers` 5.7 在缺失该目录时仍能加载（编码时用 `normalize_embeddings=True`），
本次即按此运行。若换模型/版本，先跑 `harness/bge_probe.py` 确认能加载。

## L0 soak（连续运行记录）

**推荐：Windows 计划任务**（当前用户登录即启动，72 小时后自动退出，80 小时硬上限）。

```powershell
cd testbed
powershell -NoProfile -ExecutionPolicy Bypass -File harness\schedule_soak.ps1 -Action register
powershell -NoProfile -ExecutionPolicy Bypass -File harness\schedule_soak.ps1 -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File harness\schedule_soak.ps1 -Action start|stop|unregister
```

任务名 `DLM-L0-Soak`，执行 `harness\run_soak_task.cmd`（每 5 分钟采样，日志追加到 `logs\soak_task.log`）。
**已在本机注册并启动**（状态 Running）。

手工前台运行（调试用）：

```powershell
.\.venv\Scripts\python.exe harness\soak.py --interval 300 --hours 72
```

- 每 5 分钟记录：原话数/待审/缺口、后台任务、库大小、serve 进程 RSS、6201 连接、错误行数、provider 请求与注入比。
- 产物：`logs/soak.jsonl`（逐行）、`logs/soak_summary.json`（汇总，实时覆盖）。
- 3 天验收的六条线、填表与判定规则见 `../dev/docs/L0_ACCEPTANCE.md`。

## 已知边界

- 平台是 AstrBot 的 WebChat（同一条事件总线，但**不是 NapCat/QQ**）；
- provider 是本地 mock（**不验证模型质量**，只验证请求体）；
- 单机、单会话、单用户；未做 5,000 条压测。
