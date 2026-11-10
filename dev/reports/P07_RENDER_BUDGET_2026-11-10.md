# P07 最终渲染与预算

- 日期：2026-11-10
- 状态：**P07 核心已实现；默认 `build_context` 仍保留旧字段预算口径**
- 测试：499/499

## 1. 新增

`memory_engine/render.py`：

- `render_item`：event id、title、summary、evidence、分隔符；
- `render_context`：`<memory>`、scope、query、items、`</memory>`；
- `fit_packet`：按最终渲染字符串的 token 数删除尾部 item，直到满足预算；
- `rendered` / `rendered_tokens` / `budget_enforced`。

`MemoryEngine.build_rendered_context(scope, query, budget_tokens, token_counter)`：

- 使用 tiktoken 时对最终 block 计数；
- 无 tokenizer 时 `estimated=True`、`budget_enforced=False`，不冒充 hard limit。

## 2. 测试

| 测试 | 结果 |
|---|---|
| 最终 block 含 summary/evidence 且 ≤ 精确预算 | ✅ |
| 无 tokenizer 时标 estimated，不标 hard | ✅ |

## 3. 边界

- 默认 `build_context` 在提供 token_counter + budget_tokens 时，已对最终 block 做 fit；纯字符路径仍是估算；
- 尚未把 `build_rendered_context` 设为 CLI 默认；
- 未覆盖模型实际模板/系统提示；
- 不把本切片等同于完整上下文验收。
