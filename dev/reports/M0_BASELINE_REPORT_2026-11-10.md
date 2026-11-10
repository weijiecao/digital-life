# M0 基线与契约冻结（本地）

- 日期：2026-11-10
- 状态：**本地冻结；未推送、未跑远端 CI**
- 基线提交：`bd5ec9236e0d5d4c8dcc03de398a3d02531c950a`
- 远端最后成功提交：`597f498221ad506149e42cd27d9fa9abba5e102a`

## 1. 冻结内容

| 项 | 值 |
|---|---|
| 插件版本 | 1.0.19 |
| Python | 3.12.10 |
| SQLite | 3.49.1 |
| 测试命令 | `python -m unittest discover -q -s tests -t .` |
| 全量结果 | 448/448 |
| 真实聊天语料 | 0 |

机器可读清单：

```text
dev/reports/M0_BASELINE_MANIFEST.json
```

包含：

- 提交 SHA；
- 关键源文件 SHA-256；
- Python/SQLite 版本；
- 测试总数；
- 冻结状态与已知缺口。

## 2. M0 契约状态

| 契约 | 状态 |
|---|---|
| 来源身份/source_seq/hash | ✅ 代码已有 |
| provenance PRESENT/MISSING | ◐ 有 bool，三态未完整 |
| 完整 proposals 保留 | ✅ `map_proposals` |
| 映射状态 | ✅ UNIQUE/SPLIT/MISSING |
| receipt/回执 | ✅ |
| scope lease/token | ✅ |
| 连续 frontier | ✅ manifest + receipt |
| 有效归属读契约 | ❌ |
| 模型/提示词版本冻结 | ◐ 有 model_version 参数，未固定 |
| 数据/Gold 隔离 | ◐ 21 例有，340 新 trace 无 |

M0 不能标记完全完成；当前只冻结了工程基线与已验证接口。

## 3. M1 当前状态

| M1 项 | 状态 |
|---|---|
| rollback-only | ✅ |
| 来源 receipt 同事务 | ✅ 回放层 |
| scope fencing | ✅ 来源事务与 job commit |
| manifest | ✅ |
| 缺失回执扫描 | ✅ 方法级 |
| 后台恢复任务 | ❌ |
| pending 自动重评 | ❌ |
| 双进程真实竞争 | ❌ |
| 提交前后 kill | ❌ |

## 4. 结论

M0 本地基线已冻结，M1 已具备单进程一致性原语；真实故障验收与新 trace 未完成。

V1.5-B 保持离线，不接 Shadow/生产。
