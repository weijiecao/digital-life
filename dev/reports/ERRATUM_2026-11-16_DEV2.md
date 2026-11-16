# 0.8.0-dev.2 发布更正：工程 ZIP 误收录旧失败报告（2026-11-16）

## 问题

首次发布的 `digital-life-engineering-0.8.0-dev.2.zip` 中，`EVIDENCE/release_set.json` 是
`ok=false`。内容是校验旧 `0.8.0-dev.1` 目录时留下的失败报告（缺 dev.2 附件），与本次五件套无关。
插件 ZIP、源码 ZIP 以及全部功能与验收结论不受影响。

## 原因

`tools/build_engineering_bundle.py` 用通配方式把 `dist/` 下所有报告塞进 `EVIDENCE/`，没有核对
报告属于哪个提交/版本/包；`release_set.json` 又恰好是**在工程包生成之后**才产出的检查报告，
任何 `dist/` 里的副本都属于上一轮。发布检查也没有检查工程包里的证据索引。

## 修复

1. **收录前绑定**：`_bind_evidence()` 对每份报告核对 `source_commit`/`dirty`/`plugin_version`/
   `artifact_sha256`（含嵌套 `archive.sha256`）与本轮构建清单一致；不一致直接报错
   `refusing to package stale evidence`，不静默跳过。
2. **永不收录 `release_set.json`**：该检查在工程包之后运行，结果留在 `dist/release_set.json`
   和发布日志；`ACCEPTANCE.md` 明确写出这一点。
3. **证据索引**：工程包内新增 `EVIDENCE/INDEX.json`（版本、来源提交、包哈希、每份报告的大小与
   SHA-256）。
4. **发布检查**：`check_release_set.py` 现在要求工程 ZIP 带 `EVIDENCE/INDEX.json`、索引里的包哈希
   等于插件 ZIP、每份报告的哈希与索引一致，并**拒绝** `EVIDENCE/release_set.json`。
5. **报告本身带绑定**：`selftest.json` 与 `astrbot_install_check.json` 之前没有
   `source_commit`/`dirty`/`artifact_sha256`，无法绑定；现已补上。
6. 新增 6 项负例（`dev/tests/test_bundle_evidence.py`）：过期提交、错包哈希、脏报告、
   `release_set.json` 存在、缺提交，全部按预期拒绝/排除。

## 更正后的发布

| 资产 | 更正前 | 更正后 |
| --- | --- | --- |
| 工程 ZIP | 5,819,728 B / `b55a172e…b64e` | **5,820,327 B / `7a76c88d…fec0`** |
| 发布 JSON | `e2a3f87c…791e` | **`78a80d16…509f`** |
| SHA256SUMS | `32390bbb…b304` | 随工程 ZIP/发布 JSON 更新 |
| 插件 ZIP | `6706578c…81b3` | **不变** |
| 源码 ZIP | `ca9f3ff2…ad04` | **不变** |

Release 的工程 ZIP、发布 JSON、`SHA256SUMS` 已替换，说明中追加“更正”小节；插件与源码附件未改动，
tag 仍指向被测提交 `c42ceff`。

## 复核

- 重新下载五个附件，`SHA256SUMS` **4/4** 一致；
- 对**下载后的目录**运行 `check_release_set.py`：`{"ok": true, "version": "0.8.0-dev.2", "problems": []}`
  （包含“源码 ZIP 重建插件”与“证据索引哈希”两项检查）；
- 工程 ZIP：`EVIDENCE/release_set.json` 不存在，`EVIDENCE/INDEX.json` 存在，索引绑定
  `6706578c…`；
- 源码回归 407 项（新增 6 项），0 跳过；仓库检查 11/11。

## 教训

证据的正确性不能只靠“文件存在”——必须证明它属于本轮构建。这条规则现在同时在构建侧
（收录前绑定）与发布侧（检查索引与禁用路径）执行，并有负例覆盖。
