# 0.8.0-dev.1 验收问题关闭记录（2026-11-16）

对象：`Digital_Life_0.8.0_dev1_Acceptance_2026-11-16.md` 的 A01–A04 与同批问题。
结果：A01–A04 全部关闭，并在 **0.8.0-dev.2** 的发布集合上复核；同批问题一并修正。
未修改业务运行代码；数据库 schema、记忆组织、模型策略与输出 v2 语义均未改动。

| 项 | 状态 | 落点 |
| --- | --- | --- |
| A01 源码附件不能重建插件 | 关闭 | `tools/repo_packaging.py`、`tools/build_engineering_bundle.py`、`tools/check_release_set.py` |
| A02 验收记录容忍未知提交/脏树/错包 | 关闭 | `tools/record_acceptance.py`、`dev/tests/test_acceptance_tools.py` |
| A03 证据索引记录了改写前的摘要 | 关闭 | `tools/record_acceptance.py`（`--check-evidence`） |
| A04 导出符号链接绕过 | 关闭 | `tools/export_astrbot_plugin.py`、`dev/tests/test_export_tools.py` |
| previous_delivery 指向当前包 | 关闭 | `tools/record_acceptance.py::_previous` |
| CURRENT_STATUS 拼接历史数字 | 关闭 | `docs/CURRENT_STATUS.md` |
| CI 先打包后出证据、DOM 不入记录 | 关闭 | `.github/workflows/tests.yml`、`tools/run_dom_check.py` |

## A01 源码附件的可复现构建

**原因**：`git archive` 会按 `text`/`eol` 属性与 `core.eol` 改写换行。0.8.0-dev.1 的源码 ZIP
在 Windows 上以 `core.eol=crlf` 归档，6 个未被属性覆盖的文本文件（`.gitignore`、`*.toml`、
`*.svg`）因此带 CRLF；用附件内构建器重建就得到 `30b5120b…`，而发布插件是 `05ba15e9…`。

**修复**：
1. 归档时强制 `-c core.autocrlf=false -c core.eol=lf`；
2. 打包器把 `text=auto` 与 `text` 一律视为文本，未写 `eol` 时按 LF 规范化，构建不再依赖结账设置；
3. `check_release_set.py` 新增发布检查：解压**源码附件** → 用附件内构建器重建 → 与插件 ZIP
   逐字节比较；同时校验工程 ZIP 内嵌的源码/插件 ZIP 与外层附件相同。

**复核**（对已发布的 dev.2 附件）：

```text
python tools/check_release_set.py --release-dir <下载的五件套>
{"ok": true, "version": "0.8.0-dev.2", "problems": []}
```

源码 ZIP 内的 `dev/astrbot_memory_input/pyproject.toml` 为 388 bytes、0 CRLF；重建包
`6706578c…` 与发布插件一致。旧附件 `v0.8.0-dev.1` 的缺陷保留在历史记录中，不覆盖已发布字节。

## A02 证据绑定

正式记录现在要求：构建清单的 40 位提交、`dirty=false`、`VERSION`、候选 ZIP 的实际哈希、
源码回归/包级套件/验证报告/导出清单的提交与版本逐项相等；缺字段、`None`、未知提交、脏树、
其它包的哈希都会写入 `problems` 并使 `ok=false`。没有干净提交时只能生成
`--allow-uncommitted` 的诊断记录，且 `ok` 必为 false。

新增 11 项负例（`dev/tests/test_acceptance_tools.py`）：未知提交、脏构建、错包哈希、
错提交的验证报告、缺套件、零用例、DOM 声称浏览器通过、证据被篡改/缺失、当前包被当成上一版。

## A03 证据索引

`summary.json` 先写最终内容（含 `evidence_index`），再按最终文件生成 `index.json`，不再二次改写
摘要。新增：

```text
python tools/record_acceptance.py --check-evidence dev/reports/acceptance/<commit>
```

重新校验每个记录的大小与 SHA-256，并核对摘要与索引的包哈希；篡改或缺失都会失败。

## A04 导出边界

`_reject_links()` 检查导出根到叶子的每个已存在组件：符号链接、Windows 目录联接、硬链接
（`st_nlink > 1`）都拒绝；`SOURCE_MANIFEST.json` 本身是链接也拒绝；畸形清单（非法条目、
重复路径、缺哈希）直接拒绝；`--force` 只放宽“文件已存在/内容变化”，不放宽边界检查。

新增 4 项用例：清单被替换为链接、托管目录被替换为目录联接、空清单 + `shared/` 联接、
畸形清单。Windows 下文件符号链接需要提权，用例自动退化为硬链接，仍能覆盖逃逸路径。

## 同批问题

- `previous_delivery`：排除与当前版本同名或与当前包同哈希的文件；测试覆盖“只有当前包时不选”。
- `CURRENT_STATUS`：删除 `5b35787` 与 `223,274 / 631e114a` 这类拼接的历史数字，改为指向当前
  正式摘要，历史数字只在其所属版本的报告里保留。
- CI：先跑套件与 DOM、写正式记录，再打工程包（`EVIDENCE/` 内嵌报告），最后校验发布集合；
  DOM 以 `tools/run_dom_check.py` 生成 `dist/ui_dom.json`，被验收记录作为 `dom-wiring` 套件读取。
- 工程 ZIP：内嵌 `EVIDENCE/`（回归、包级、验包、自测、DOM、发布集合检查、导出清单），
  校验和改为相对路径，避免同名文件冲突。

## 0.8.0-dev.2 复核结果

| 项 | 值 |
| --- | --- |
| 主仓库被测提交 | `c42ceff338ce2857db2314a696a64d25cc970700`（tag `v0.8.0-dev.2`） |
| 插件包 | 79 文件 / 223,273 bytes / `6706578c…81b3` |
| 源码 ZIP | 5,683,688 bytes / `ca9f3ff2…ad04`，可重建同字节插件 |
| 工程 ZIP | 5,819,728 bytes / `b55a172e…b64e`，含 `EVIDENCE/` |
| 源码回归 | 401 / 401，0 跳过 |
| 包级 | completion 47、core 137、legacy 20 |
| DOM | 7 条路径，`visual_browser_validation: false` |
| 正式记录 | `formal=true`、`ok=true`、5 个套件，证据在 `dev/reports/acceptance/c42ceff…/` |
| 分发仓库同步 | 79/79 托管文件一致，122 项非托管保留 |
| Release | <https://github.com/weijiecao/astrbot_plugin_digital_life/releases/tag/v0.8.0-dev.2>，附件下载后 4/4 哈希复核通过 |

## 仍未验收

真实 AstrBot/NapCat 实例、Windows 实机安装、真实浏览器页面（Chromium/Playwright 未安装）、
72 小时长跑；DOM 主体通过不等于浏览器通过。
