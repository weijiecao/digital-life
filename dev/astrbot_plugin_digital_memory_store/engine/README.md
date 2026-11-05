# engine/ —— 实验编号模块说明

这些目录名（`e31`、`e32`、`e417`…）是 E5.0 迭代过程中的**实验编号**，不是版本号，也不代表新旧顺序。
改名会破坏测试、历史报告（`dev/docs/PLUGIN_HISTORY.md`、`dev/reports/*.json`）里的引用，所以保留原名。
按目录名猜“哪个是废弃代码”是错的——**请按下面的调用链判断**。

## 当前调用链（谁在用谁）

```
storage.py
├── engine/e32/cpu_model.py   CPUSentenceEmbedder（CPU 语义编码器）
│     └── engine/e31/local_model.py   LocalEmbedder（严格本地、内容版本化的适配层）
├── engine/e418/engine.py     统一“保源”写入/审核/检索
│     └── engine/e417/writer.py       离线写入闭环
│           └── engine/lifemem/core.py  证据优先的离线记忆基座（标准库）
└── engine/e419/dynamic_index.py  已审核原话的动态语义候选 + SQLite 加速索引
      ↑ auto_model / auto_topics / auto_organize / auto_cross_update / auto_vector_chunks 也直接用它
```

## 每个模块一句话

| 模块 | 职责 | 关键约束 |
|---|---|---|
| `e31/local_model.py` | 严格本地的句向量适配层 | 调用方必须给可用模型路径；**不静默伪造语义分** |
| `e32/cpu_model.py` | 面向本地 SentenceTransformer 目录的 CPU 适配 | 不联网；无模型时返回“明确标记的非语义基线”；不隐式用 GPU |
| `e417/writer.py` | 离线记忆写入闭环 | 标准库实现，Windows 10 / Python 3.12 可用 |
| `e418/engine.py` | 统一“保源”的写入/审核/检索 | scope/origin/session/reviewer 由上游鉴别，本层不做身份认证 |
| `e419/dynamic_index.py` | 已审核、来源可核验、用户消息的动态语义候选 | 不推断事实、不自动审核；SQLite 只是加速结构，不是记忆真源 |
| `lifemem/core.py` | 证据优先的离线记忆基座 | 纯标准库 |

## 改这里的规矩

1. **先看调用链**：被 `storage.py` 直接引用的才是活跃路径；只被自己 `__init__.py` 引用的就是未用；
2. **不要为了“看起来整齐”改名**：`test_store.py`、`dev/docs/*`、`dev/reports/*` 里有大量按名引用；
3. **语义相关改动必须能离线跑**：这些模块的卖点是“无网络、无隐式下载、无隐式 GPU”；
4. **不要在这里做事实判定**：语义分只作候选排序，是否确认由用户/审核流程决定。
