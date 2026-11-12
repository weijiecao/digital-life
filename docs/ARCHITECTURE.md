# 数字记忆工程架构

数字记忆工程由两个边界清晰的部分组成：

```text
┌──────────────────────── 数字记忆工程 ────────────────────────┐
│                                                             │
│  数字记忆基座                         数字记忆引擎            │
│  ├─ 原话 / source / receipt           ├─ 候选召回（词面+BGE） │
│  ├─ event / revision / evidence       ├─ 证据准入 admission    │
│  ├─ provenance / story / subject      ├─ rejudge / worker      │
│  ├─ resolver / cannot-link            ├─ context / render      │
│  ├─ scope lease / fencing             ├─ budget / cache        │
│  └─ manifest / contiguous frontier    └─ audit / explain       │
│                                                             │
│  AstrBot 插件：基座的接入、配置、面板与生命周期              │
└─────────────────────────────────────────────────────────────┘
```

## 数字记忆基座

职责：保存可追溯的事实来源，维护事件、修订、证据、来源归属和并发一致性。

代码位置：

- `dev/v1_5_consolidator/`：store、resolver、provenance guard、candidate index；
- `dev/astrbot_plugin_digital_memory_store/`：AstrBot 接入、原话存储、面板与安装；
- `dev/host_shadow/`：shadow/soak 接入。

基座不决定“答案是什么”，只提供有来源、有版本、可回退的事件与证据。

## 数字记忆引擎

职责：从基座读取有效证据，完成候选召回、证据准入、重评、上下文选择和预算渲染。

代码位置：

- `dev/memory_engine/engine.py`：检索、上下文、缓存；
- `dev/memory_engine/admission.py`：结构化准入（SUPPORTED/RELATED_ONLY/INSUFFICIENT/CONFLICT）；
- `dev/memory_engine/rejudge.py`、`worker.py`：自动重评与队列；
- `dev/memory_engine/render.py`、`token_budget.py`：预算与渲染；
- `dev/memory_engine/cli.py`：隔离 CLI。

引擎的原则：候选可以召回，但只有被当前有效证据支持的内容才能进入上下文；来源不足时返回空或降级原因，不伪造答案。

## 数据流

```text
私聊原话
  → 基座：source + receipt + event + evidence
  → 引擎：候选召回 → 证据准入 → 重评/Resolver 结果
  → context packet（带引用）→ 渲染/预算
```

## 状态

当前为隔离验收，**R0/R1 NO-GO**。AstrBot 插件包和 Q1 运行包只用于安装/升级/回退和离线验证，不接入 V1 生产或生产 Shadow。
