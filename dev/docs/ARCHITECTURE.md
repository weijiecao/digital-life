# 数字记忆基座 · AstrBot 插件架构

本文件描述数字记忆基座的接入层。工程总体架构见
[`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)。

- 插件入口：`main.py`
- 原话/事件/证据存储：`storage.py`、`store/`
- 自动整理与索引：`auto_*.py`、`lexical_index.py`、`pending_index.py`
- 面板与生命周期：`panel.py`、`recovery.py`、`install.ps1`

插件只提供基座能力；候选召回、证据准入和上下文渲染属于数字记忆引擎。
