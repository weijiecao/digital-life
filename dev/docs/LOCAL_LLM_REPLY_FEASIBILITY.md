# 本地模型做回复质量测试：可行性

> **边界**：这里的“本地 LLM”是**宿主回复模型 / 评测对象**，不属于记忆系统本身。
> 记忆系统内部只有本地 BGE 编码器（`model_dir`）与可选的 LLM 辅助
> （候选筛选/草稿/关联，`judge_provider_id`/`draft_provider_id`），**不生成聊天回复**。
> 所以本文件讨论的是“换宿主模型后，回复是否可用”，不是“记忆系统换生成模型”。

- 问题：不调用 DeepSeek，改用本机本地 LLM，能不能做回复质量测试？能不能达到可用质量？
- 机器：Lenovo 11CXCTO1WW / i7-10700（8C16T）/ 16 GB RAM / Intel UHD 630（无独显）/ C 盘余量约 104 GB
- 现状：本机没有 Ollama / llama.cpp / LM Studio / vLLM，也没有本地 LLM 权重；只有 BGE 小模型
- 结论：**能跑、能做离线质量评估；本机现实档位是 3B–7B，7B Q4 是“质量下限”，3B 只适合流程，14B 不建议。**

---

## 1. 两种“能做到”

| 含义 | 答案 |
|---|---|
| **测试能不能跑** | **能**。`reply_quality.py` 只依赖 OpenAI 兼容接口；Ollama / llama.cpp / LM Studio / vLLM 都能接 |
| **质量能不能达标** | **不保证**。3B/7B 在“接地/不确定/不执行指令”上通常弱于 DeepSeek；必须用同一套 20 条场景实测 |

> 关键区别：本地模型能完成“流程 + 初步评分”，但不能因为“跑通了”就认为“质量等价”。

---

## 2. 本机可选的模型档位

| 档位 | 模型例子 | 量化后内存 | 本机可行性 | 体验/质量 |
|---|---|---|---|---|
| 0.5B–1.5B | Qwen2.5-0.5B/1.5B | 0.4–1.2 GB | 很轻松 | 只适合管线/延迟测试，不能做质量结论 |
| **3B–4B** | Qwen2.5-3B、Qwen3-4B | 2–3 GB | 可行 | 能聊，但接地/不确定/抗注入明显弱；只能当基线 |
| **7B–8B（推荐下限）** | **Qwen2.5-7B-Instruct**、Qwen3-8B、GLM-4-9B | 4.5–6 GB | 可行，但 CPU 慢 | 中文和指令跟随明显好于 3B；可做离线评估，不适合实时聊天 |
| 14B+ | Qwen2.5-14B/32B | 9 GB+ | 16 GB 内存很紧，速度不可接受 | 不建议本机 |

> 量化优先 **Q4_K_M**；Q5 质量略好但更占内存/更慢。模型选中文强的 Qwen2.5/GLM-4，不要选中文弱的英文模型硬撑。

### 速度量级（CPU，粗略估计，需实测）

| 档位 | 生成速度 | 150–200 字回答 |
|---|---|---|
| 3B Q4 | 6–12 tok/s | 15–30 s |
| **7B Q4** | **2–6 tok/s** | **30–60 s** |
| 14B Q4 | 1–2 tok/s | 90 s+ |

这些是量级，不是承诺；i7-10700 无独显，prompt 处理也会占时间。**离线批量跑 20 条可以等，实时聊天体验会很差。**

---

## 3. 质量预期（基于模型规模，不是实测）

| 判据 | 3B | 7B | 14B+ / DeepSeek |
|---|---|---|---|
| C1/C2 接地（复述原话） | 容易漏/改写 | 多数能复述 | 稳定 |
| C0 无记忆不编造“你提过” | 弱，需要强 prompt | 中等，负证据后可能改善 | DeepSeek 仍有 4–7/20，需要负证据 |
| C3 旧版本不当现状 | 容易照抄旧版本 | 多数会标“旧记录” | 较稳 |
| C4 跨 scope | 看到就会用 | 看到就会用 | 看到就会用 |
| C5 记忆里的指令 | 弱，容易跟 | 中等 | 较强 |
| 延迟 | 15–30 s | 30–60 s | 1–2 s（API） |

**结论**：C4 不是模型能力问题，任何模型看到跨 scope 内容都会用；必须插件层隔离。C0/C5 是本地小模型的主要风险。

---

## 4. 怎么接本地模型测试

### 4.1 Ollama（最省事）

```powershell
# 安装 Ollama 后
ollama pull qwen2.5:7b-instruct-q4_K_M
# 或先试 3B：ollama pull qwen2.5:3b-instruct-q4_K_M
```

Ollama 的 OpenAI 兼容端点：`http://127.0.0.1:11434/v1`。
`reply_quality.py` 需要一个 key 文件（本地模型不校验 key，写个占位即可）：

> Ollama 的量化 tag 随版本会变（`qwen2.5:7b-instruct-q4_K_M` 或 `qwen2.5:7b`），
> 以 `ollama list` / 模型页实际 tag 为准；下文命令里的名字要替换成你拉下来的那个。

```powershell
'ollama' | Set-Content -Encoding ascii "$env:TEMP\local_llm_key.txt"

cd dev
..\testbed\.venv\Scripts\python.exe evaluation\reply_quality.py `
  --scenarios evaluation\reply_quality_scenarios_v1.json `
  --conditions c0,c1,c2,c3,c4,c5 `
  --base-url http://127.0.0.1:11434/v1 `
  --model qwen2.5:7b-instruct-q4_K_M `
  --api-key-file "$env:TEMP\local_llm_key.txt" `
  --out ..\dev\reports\reply_quality_local_qwen25_7b.jsonl `
  --temperature 0 --max-tokens 300 --sleep 0
```

### 4.2 llama.cpp（更可控）

```powershell
# 下载 GGUF（Q4_K_M）后
llama-server -m Qwen2.5-7B-Instruct-Q4_K_M.gguf --port 8080 --ctx-size 8192 -t 8
```

端点：`http://127.0.0.1:8080/v1`，`--model` 随便填服务端别名。
优点：可控制线程数/上下文；缺点：要自己找 GGUF、配 chat template。

### 4.3 LM Studio / vLLM

- LM Studio：开启 Local Server，端点通常是 `http://127.0.0.1:1234/v1`；
- vLLM：需要 NVIDIA GPU，本机不适用。

---

## 5. 接 AstrBot 端到端（可选）

AstrBot 的 provider 是 OpenAI 兼容配置，可以新增一个 provider source 指向本地端点：

```json
{
  "provider": "local-qwen",
  "type": "openai_chat_completion",
  "api_base": "http://127.0.0.1:11434/v1",
  "key": ["ollama"],
  "model": "qwen2.5:7b-instruct-q4_K_M"
}
```

但端到端会混入 AstrBot 的 system prompt、persona、历史与流式行为，**不适合做质量评分**；
质量评分仍用 `reply_quality.py` 的直接调用。端到端只验证“装得上、请求发得出去”。

---

## 6. 建议

1. **先装 Ollama + Qwen2.5-7B Q4**（约 4.7 GB，104 GB 空间足够），用同一 20 条场景跑 C0/C1/C2；
2. 重点看三个数：C0 编造率、C1/C2 接地、C3 旧当新；C4/C5 作为安全门槛；
3. 如果 7B 的 C0 明显差于 DeepSeek，先加“无记忆负证据”prompt，再考虑换 14B；
4. **不建议本机 7B 做实时聊天**：30–60 s/答；适合离线评估、草稿、夜间批处理；
5. 若要求生产级实时质量：本地需要 GPU + 14B/32B，或继续用 API；混合方案是“本地 7B 做离线回归，DeepSeek 做线上回复”。

> 我可以直接装 Ollama、拉 Qwen2.5-7B Q4，并跑同一套 20 条场景；这会下载约 4.7 GB 模型并占用 CPU 一段时间，需要你确认。
