# 真实语料：导入、标注与评分

> 目的：把 200–500 条**脱敏**真实私聊变成可重复的评估集，回答“真实 R@1 / 误注入 / 纠错”。
> 管线是通用的；当前仓库里只有 5 条合成样例自测（`evaluation/fixtures/real_corpus_smoke.json`）。

## 1. 导出格式

一个 JSON 对象，或 JSONL（每行一个会话）：

```json
{
  "sessions": [
    {"id": "s1", "messages": [
      {"text": "下个月十五号搬到杭州西湖区", "at": "2025-01-01T10:00:00+08:00"},
      {"text": "搬家改到滨江区", "at": "2025-01-01T10:02:00+08:00"}
    ]}
  ]
}
```

- `text`：脱敏后的原话；空串、超过 400 字、`role` 非 `user` 会被跳过并计数。
- `at`：带时区的时间；缺省用导入时刻。**改期类探针依赖真实间隔**，请保留原始时间。
- `id`：会话标识，探针里的 `session` 必须与之一致。

### 脱敏要求（务必）

- 替换姓名、手机号、地址门牌、公司名、账号/订单号；
- 保留“时间关系”和“话题结构”，不要改写语气与句式（否则测的不是真实分布）；
- 不要放任何密钥、验证码、身份证号。

## 2. 导入

```powershell
cd dev
..\testbed\.venv\Scripts\python.exe evaluation\real_corpus_import.py `
  --input corpus.json `
  --model-dir ..\testbed\models\bge-small-zh-v1.5 `
  --db-dir reports\real_corpus_db
```

输出导入统计（会话数/消息数/导入/跳过）与数据库路径。

## 3. 标注探针

```json
{
  "probes": [
    {"session": "s1", "query": "搬家的事现在怎么安排？",
     "expect": ["下个月十五号搬到杭州西湖区", "搬家改到滨江区"],
     "require_unconfirmed": true},
    {"session": "s1", "query": "火星飞船怎么租？",
     "forbid": ["公园跑步", "苹果"]}
  ]
}
```

| 字段 | 含义 |
|---|---|
| `expect` | 必须全部出现在注入包里的**原文片段**（纠错类写新旧两条） |
| `forbid` | 不得出现的片段（负例/串味检查） |
| `require_unconfirmed` | 包内必须含“未确认”（默认建议 true） |

标注建议：
- 正例按“无内容追问 / 明确事件 / 改期 / 取消 / 相似事件 / 跨日”分层，每层 20–50 条；
- 负例至少 30 条（无关问题、近似但没有记忆的问题）；
- 纠错类必须写成两条 expect，才能同时检查“新旧并列”和“未确认”。

## 4. 评分

```powershell
..\testbed\.venv\Scripts\python.exe evaluation\real_corpus_eval.py `
  --db-dir reports\real_corpus_db `
  --probes probes.json `
  --model-dir ..\testbed\models\bge-small-zh-v1.5
```

输出 `reports/real_corpus.json`：

| 指标 | 目标（L1） |
|---|---|
| 正例命中（R@1 近似） | ≥70% |
| 负例误注入 | ≤5% |
| 纠错新旧+未确认 | ≥60% |
| 延迟 P95（热） | <1.5 s |

`STATUS.md` 会在“真实语料探针”一节展示；没有 `reports/real_corpus.json` 时只显示管线自测结果。

## 5. 从 AstrBot 历史导入（可选）

插件自带 `/dlm 导入历史` 可以从 AstrBot 消息库导入；本管线独立于插件，便于在临时库里反复评估，
不会污染线上数据。两者格式不同，互不影响。

## 6. 注意

- 真实语料上的阈值/闸门参数需重新校准；当前默认 0.60 来自自造语料。
- 探针是人工标注，存在主观性；同一批探针至少两人各标一遍，分歧条目单独讨论。
- 报告里不要贴原始私聊全文，只保留片段与统计。
