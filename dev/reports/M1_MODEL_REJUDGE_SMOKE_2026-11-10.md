# M1 真实模型重判烟测

- 日期：2026-11-10
- 状态：**单条真实模型重判通过；不是稳定性验收**
- 脚本：`dev/evaluation/m1_model_rejudge_smoke.py`

## 1. 流程

```text
seed event（user/real）
→ pending source（同事件）
→ READY
→ RelationJudge（真实 deepseek-chat）
→ ConstrainedResolver
→ pending RELOCATED
```

## 2. 结果

```text
processed 1
resolved 1
deferred 0
attached  [evt_ae35...]
pending   RELOCATED
```

## 3. 边界

- 单条合成输入、单次调用；
- 不是 21/340 稳定性；
- 未覆盖模型拒绝/超时/解析失败；
- 未接生产或 Shadow。
