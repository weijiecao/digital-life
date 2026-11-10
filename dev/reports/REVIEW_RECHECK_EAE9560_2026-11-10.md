# 复核脚本在 eae9560 上的复跑

- 日期：2026-11-10
- 提交：`eae956062da928faf79e7c9fb7780a70a0d43204`
- 脚本：外部复验包 `verify_fixes.py`
- 模型调用：0
- 真实聊天：0

## 结果

```text
Ran 10 tests
OK
failures 0
errors 0
```

P01–P06 六个原始场景和 B01–B04 四个边界，共 10/10 通过。

## 说明

- B01 的 `cannot-link` 查询改为读取真实 `event_cannot_link` 表；
- `b4_frozen_replay` 的 MAPPING_INCOMPLETE 分支补传 `source`/provenance；
- 本地全量：497/497。

P07 仍未完成；R0/R1 仍未整体验收。
