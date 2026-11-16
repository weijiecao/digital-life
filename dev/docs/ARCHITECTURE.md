# 开发树模块架构

工程总体架构（基座 / 引擎 / 输入 / 输出 / 应用服务 / 管理界面）见
[`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)。本文只补充 `dev/` 内部的
分层关系，避免和总体文档重复。

## 分层

```text
dev/astrbot_plugin_digital_life/main.py        宿主入口：钩子、模式、生命周期
        │
        ├── application/service.py           统一在线写入口（维护闸门、状态机）
        │     ├── application/migration.py   旧库冻结、身份映射、断点批次
        │     └── application/storage.py     备份、代次目录、CURRENT.json 切换
        │
        ├── runtime/ownership.py             数据目录所有权与旧插件检测
        ├── console_api.py                   管理页路由 → dlm_console/core.py
        │
        ├── astrbot_memory_input/unified.py  实时追加 / 历史对账（持久身份账本）
        ├── memory_engine/                   召回 → 准入 → 重评 → 上下文/预算
        ├── memory_output/                   输出协议 v2 → 宿主临时注入
        └── v1_5_consolidator/               事件、证据、来源、租约、回执、水位
```

## 依赖方向

- `v1_5_consolidator`、`memory_engine`、`memory_output`、`astrbot_memory_input`、
  `dlm_console` 之间只按公开接口相互调用，不反向依赖插件入口；
- 插件入口负责装配，把宿主对象翻译成上述包的输入；
- `application/` 只依赖基座、输入与输出包，不直接操作宿主；
- 任何层都不得依赖 `archive/`。

## 原生输入的三层

- `astrbot_memory_input/`：与 AstrBot 无关的输入契约（校验、原文、来源、去重、journal）；
- `memory_engine/native_input.py`：收件箱、候选、事务提交；
- `memory_engine/native_host.py` 与 `native_judge.py`：宿主历史读取、周期泵、来源映射、
  判别协议接线。

旧的 `host_shadow/` 装配插件已归档；统一插件在 `main.py` 里直接完成同样的接线。
