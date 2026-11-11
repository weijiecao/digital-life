# RC2 交接包复核与集成（2026-11-11）

- 交接基线：`cd9b23d`
- 应用：`patches/from_cd9b23d.patch`（14 个文件，20 项专项）
- 结果：**RC2 候选包可在 Windows 独立运行；R0/R1 仍为 NO-GO**

## 1. 应用与 Windows 兼容

按交接包 README 只应用累计补丁，不与增量补丁叠加。

Windows 上补了三处移植问题：

1. `build_r0_bundle.py` 在 PATH 无 `git` 时回退为 `base_commit=unavailable`，不阻塞打包；
2. `test_r0d_stage_close` 用 `contextlib.closing` 关闭 SQLite 连接，避免临时目录删除失败；
3. 本机复核用验证脚本的临时目录允许清理失败，但 **只影响证据脚本，不改产品语义**。

## 2. 测试

| 环境 | 运行 | 通过 | 跳过 | 失败 |
|---|---:|---:|---:|---:|
| Windows 本地 / 3.12 | 559 | **559** | 0 | 0 |
| GitHub Actions / Windows 3.10 | 559 | 559 | 0 | 0 |
| GitHub Actions / Windows 3.11 | 559 | 559 | 0 | 0 |
| GitHub Actions / Windows 3.12 | 559 | 559 | 0 | 0 |
| GitHub Actions / Ubuntu 3.10 | 559 | 558 | 1 | 0 |
| GitHub Actions / Ubuntu 3.11 | 559 | 559 | 0 | 0 |
| GitHub Actions / Ubuntu 3.12 | 559 | 559 | 0 | 0 |

- 远程 CI：run `43047374992`，6 个 job 全绿。
- Linux 3.10 的唯一跳过是 `sqlite3.Connection.setlimit` 依赖 3.11+。
- 本机已安装 tiktoken 0.14.0，token 预算用例实际执行，不再跳过。

## 3. 独立包

| 项 | 值 |
|---|---:|
| 文件数 | 38 |
| 大小 | 61,223 bytes |
| 包 SHA-256 | `69004fb521a61d1349ffa88d5ef12c327ba5df3b06ede7d0676c618e3cf3d2bc` |
| source_sha256 | `5470b2457a8ff77429d8926177772b96f66c55f2338cd17df738bb07c9442724` |
| base_commit | `568f44a67ba6f12d3b63b2b6a4da2ba961604701` |
| 隔离 smoke | 7/7 |
| exact budget | 通过 |

`verify_r0_bundle.py --exact-budget` 的结论是：

```text
PACKAGE_VERIFIED_NOT_RELEASE_GO
```

## 4. 功能复核

- R0-D 恢复矩阵：10/10，业务快照无差异，无遗留 CLAIMED job。
- 自动重评：8/8。
- 并发与输入改写：2/2。
- `verify_stage_close.py`：`PATCH_SCOPE_PASS_WITH_KNOWN_RECALL_REGRESSION`。

## 5. 质量复核：RC2 没有提升，且有回退

| 指标 | 上一轮 539 | RC2 559 |
|---|---:|---:|
| holdout Top1 / Top5 | 280/300 | **261/300** |
| 自然问句 | 140/140 | 140/140 |
| paraphrase | 20/40 | **1/40** |
| 摘要关键信息保留 | 100/100 | 100/100 |
| 负例注入 | 0/200 | 0/200 |
| 无效版本/越 scope 注入 | 0 | 0 |

RC2 的 `_question_content` 会剥离更多疑问脚手架并只保留长度 ≥2 的中文片段；安全上减少了泛化问句误注入，但 paraphrase 召回从 20/40 降到 1/40。交接包的 BGE 实验也得到 holdout 261/300、paraphrase 1/40，且 dev 选中的阈值 1.01 等于关闭 fallback。

**结论：RC2 是更保守的候选包，不是质量提升。不得把“可解压运行”写成“可生产上线”。**

## 6. 当前阻塞

- paraphrase 1/40 未解决；
- 质量集仍是单作者合成场景，不是独立盲评/真实聊天；
- 真实平台 72h、用户效果、NapCat/AstrBot 实际连接未执行；
- 断电/fsync 仍是独立耐久性实验。

R0 / R1 保持 **NO-GO**。

## 7. 证据

- 交接包：`_rc2_handoff/`
- 本机 RC2 包：`_publish/digital-life/releases/memory-engine-r0-rc2.zip`
- 包清单：`releases/memory-engine-r0-rc2.json`
- 隔离验证：`releases/memory-engine-r0-rc2.verify.json`
- 本机验收：`reports/rc2_results/acceptance.json`
- gold：`reports/rc2_results/gold_metrics.json`
- CI：run `43047374992`
