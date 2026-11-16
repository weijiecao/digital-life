# memory_engine — 数字记忆引擎

职责：从基座读取有效证据，完成候选召回、证据准入、重评、上下文选择与预算渲染。
不直接接触宿主；宿主相关装配在 `memory_engine/native_host.py` 与
`dev/astrbot_plugin_digital_life/main.py`。

## 公开接口

| 模块 | 作用 |
| --- | --- |
| `engine.py` | `MemoryEngine`：摄入、检索、`retrieve_with_audit`、删除来源、缓存 |
| `admission.py` | 结构化准入：SUPPORTED / RELATED_ONLY / INSUFFICIENT / CONFLICT |
| `rejudge.py`、`worker.py` | 自动重评与队列 |
| `render.py`、`token_budget.py` | 上下文渲染与预算；`tiktoken` 缺失时不注入 |
| `native_input.py` | 收件箱、候选、事务提交（业务库侧） |
| `native_host.py` | 宿主历史读取、周期泵、`Origin` 构造、来源映射 |
| `native_judge.py` | `NativeRelationJudge` 与 `build_provider`（none/mock/openai） |
| `output.py` | `MemoryOutputService`：把已准入证据组装为预算内的临时块 |
| `cli.py` | 隔离 CLI（`health`/`ingest`/`search`） |

## 原则

- 候选可以召回，但只有被当前有效证据支持的内容才能进入上下文；
- 来源不足时返回空或降级原因，不伪造答案；
- 删除、撤回、修订或 scope 变化后，旧输出必须失效；
- 预算依赖调用方 tokenizer，不等于供应商实际计费。

## 验证

```powershell
# 仓库根目录
python tools/run_regression.py

# 只跑引擎相关
cd dev
python -m unittest -q tests.test_memory_engine_engine tests.test_memory_engine_rejudge `
  tests.test_memory_engine_render tests.test_memory_engine_host tests.test_query_admission_v4
```

质量验收脚本在 `dev/evaluation/`，需要本机 BGE 目录，不进入离线回归；
能力与缺口见 `dev/reports/SPECIALIZED_COVERAGE.md`。
