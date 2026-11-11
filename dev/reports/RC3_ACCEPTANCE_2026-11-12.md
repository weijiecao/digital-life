# RC3 验收与上线判定（2026-11-12）

- 代码提交：`b5f9846`
- 测试：**569/569**
- RC3 包：38 文件、62,451 bytes、SHA-256 `afcca11a…`
- 判定：**隔离候选包 GO；生产接入 NO-GO**

## 1. 查询质量：候选召回＋证据准入

实际代码：

- `MemoryEngine.retrieve()` 在有 encoder 时走 BGE/词面候选融合；
- 召回后用 `_answerable()` 做证据准入：
  - 拒绝初稿/原来/撤回/废弃/备份/临时方案/隔壁组；
  - 拒绝未记录的付款、凭证、联系人、编号等属性；
  - 拒绝查询声明的值与当前证据不一致；
  - 拒绝主体不符（我/朋友/家人）和故事现实性不符；
- 语义候选只有达到 0.45 才允许跨字符重叠，避免“牙科复查”等泛化问句误注入；
- `revise_source()` 继承原事件 provenance，避免更正后丢失主体/故事。

## 2. 冻结的独立质量集

`evaluation/quality_v2/`：4 个 dev 组 + 4 个 holdout 组，冻结前写入 manifest 和 SHA-256；
通过标准在运行前固定：

```text
positive_top1 >= 0.95
positive_top5 >= 0.95
negative_context_injections = 0
invalid_context_injections = 0
paraphrase_top1 >= 0.80
no_answer_injections = 0
safety_categories_zero_injection
```

结果：

| split | 正例 | 负例 | Top1 | 负例注入 | 无效注入 | 结论 |
|---|---:|---:|---:|---:|---:|---|
| dev | 12 | 14 | 12/12 | 0 | 0 | PASS |
| holdout | 12 | 13 | 12/12 | 0 | 0 | PASS |

覆盖：主体（我/朋友/妈妈）、故事（虚构 vs 真实）、时间（现在/原来）、更正、撤回、无答案、paraphrase。

旧 340/300 组合集在 BGE+准入下为 **296/300**，paraphrase **36/40**，负例注入 0；旧的纯词面路径只有 5/12，通过质量需要配置 BGE，不能把词面路径当作同等能力。

## 3. 系统闭环

`evaluation/closed_loop_accept.py` **11/11 通过**：

- 自动 READY 队列消费；
- provider 首次失败、第二次成功；
- pending 重评并 RELOCATED；
- 关闭/重开 store 后仍可查询；
- attach 后 context cache 失效；
- 120 token 预算有界，0 预算返回空；
- 200 次查询后 integrity ok，cache 有界。

## 4. 包与生命周期

| 项 | 值 |
|---|---:|
| 文件数 | 38 |
| 大小 | 62,451 bytes |
| SHA-256 | `eaed986ad122c95eeb7e5a942c06ce542dd047e6120a2c433708b8c4b05403b5` |
| source_sha256 | `4fba7699e6972d80b051c0bafa0d113022ca236a752c631450ce8edbb564ae65` |
| base_commit | `b5f9846…` |
| 隔离 smoke | 7/7 |
| exact budget | PASS |
| 安装 / 升级 / 回退 | 6/6 PASS |

`verify_r0_bundle.py` 输出 `PACKAGE_VERIFIED_NOT_RELEASE_GO`；生命周期验证使用目录安装、外部 SQLite DB、覆盖升级后回退，旧/新数据都可读。

## 5. CI

GitHub Actions：Windows/Ubuntu × Python 3.10/3.11/3.12，6 个 job 全绿；Linux 3.10 唯一跳过 `sqlite3.setlimit`。

## 6. GO / NO-GO

### 隔离候选包：GO

RC3 包可以解压、安装、升级、回退，质量门槛在冻结 holdout 上通过，闭环检查通过。

### 生产接入：NO-GO

- 质量集是单作者构造场景，不是独立人工盲评或真实聊天；
- 质量依赖显式 BGE；纯词面路径仅 5/12；
- 还没有真实 AstrBot/NapCat 72h、用户效果或真实平台错误/重启记录；
- 断电/fsync 没有纳入本轮。

因此不把 RC3 接入 V1 生产或生产 Shadow；只允许隔离验收继续。

## 7. 证据

- `reports/rc3_results/quality_v2_acceptance.json`
- `reports/rc3_results/quality_v2_dev4.json`
- `reports/rc3_results/quality_v2_holdout2.json`
- `reports/rc3_results/legacy_gold_bge.json`
- `reports/rc3_results/closed_loop.json`
- `reports/rc3_results/package_lifecycle.json`
- `releases/memory-engine-r0-rc3.zip` / `.json` / `.verify.json`
