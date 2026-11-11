# R0 复核意见处理（2026-11-11）

对应复核：`R0_FIX_EVIDENCE_7a8495a.zip` / `R0_FIX_REVIEW_2026-11-11.md`
当前功能提交：`9586328`

## 1. 处理结果

| 复核项 | 处理 | 独立验证 |
|---|---|---|
| C01 hybrid 索引外部失效 | 索引绑定 scope epoch；返回前按当前 revision/valid/pending/withdrawn 再核验 | 复核脚本：lexical 0 / hybrid 0 / context 0 |
| C02 零预算/裁剪/无 tokenizer | 零预算空块；框架溢出空块；裁剪重建 refs/used/degraded；无 tokenizer 默认 strict；CLI 显式 0 | 复核脚本：rendered 空、items/refs 0、budget_enforced true |
| C03 原判被覆盖 | 不改原 proposal；raw_label/raw_reasons 独立保存；explicit [] 原样保留 | 复核脚本：raw_empty_reasons_preserved=true |
| R0 包路径 | `--bundle` 或 `releases/` 自动查找 | 干净 checkout 校验通过 |
| 旧 P01–P06、B01–B04 | 保持修复 | `verify_fixes.py` 10/10 |

## 2. 测试口径

- Windows：536/536。
- Linux：530 通过 + 6 skipped（缺 tiktoken）。
- 不再写成“Linux 5 skipped”。
- skipped 明确是可选 tokenizer 用例，不是已执行。

## 3. 包一致性

- 31 文件、54,904 bytes。
- SHA-256 `05b66ffc9576ce0e48ce0cd255b03fcaf82570e892581af71a1b14e54c07d501`。
- 复核脚本：整包 hash 匹配、逐文件无 mismatch、无原始字节差异、无换行规范化差异。

## 4. 修正后的 NO-GO

C01/C02/C03 与包路径问题已关闭。R0 剩余为：

- R0-D：真实执行的恢复/重放；
- 完整语义快照；
- 独立离线数据分割与 gold；
- 自动闭环证据。

R1 剩余：真实平台 72h 与用户效果。断电/磁盘损坏另立耐久性实验，不作为 R0 已冻结必选门槛。

## 5. 产物

- 工程报告：`ENGINEERING_REPORT_2026-11-11.docx` / `.pdf` / `.md`
- 验收矩阵：`MEMORY_ENGINE_ACCEPTANCE_2026-11-10.md`
- R0 包：`releases/memory-engine-r0-rc1.zip`
