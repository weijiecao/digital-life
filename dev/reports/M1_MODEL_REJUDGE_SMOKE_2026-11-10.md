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

## 2.1 部分批次失败

测试：两条 pending，第一条成功、第二条 provider timeout；确认：

- 第一条已附着；
- 第二条仍 READY；
- 重试后第二条附着。

## 2.2 重试

`run_once(attempts=N, backoff=...)` 对瞬时 provider 失败重试；测试：第一次 timeout、第二次成功，最终 resolved 1。

## 3. 边界

- 单条合成输入、单次调用；
- 不是 21/340 稳定性；
- 未覆盖模型拒绝/超时/解析失败；
- 未接生产或 Shadow。
