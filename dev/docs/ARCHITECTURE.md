# 数字记忆基座 · AstrBot 插件架构

本文件描述数字记忆基座的接入层。工程总体架构见
[`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)。

- 插件入口：`main.py`
- 原话/事件/证据存储：`storage.py`、`store/`
- 自动整理与索引：`auto_*.py`、`lexical_index.py`、`pending_index.py`
- 面板与生命周期：`panel.py`、`recovery.py`、`install.ps1`

插件只提供基座能力；候选召回、证据准入和上下文渲染属于数字记忆引擎。

引擎侧的原生历史入口分三层（v0.2）：

- `astrbot_memory_input/`：与 AstrBot 无关的输入契约（校验、原文、来源、去重、journal）；
- `memory_engine/native_input.py`：收件箱、候选、事务提交；
- `memory_engine/native_host.py` + `memory_engine/native_judge.py`：宿主会话历史读取、
  周期泵、来源映射，以及把既有 `RelationJudge` 接成判别协议；
- `host_shadow/`：在真实 AstrBot 里装配以上三层的 shadow 插件。

