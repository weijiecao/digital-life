# 数字生命 工程包真源清单（MANIFEST）

> 目的：桌面上有 6+ 个 DigitalLife / E5.0-x 包，名字都像"工程包"。这份清单元数据说明
> **哪个是权威版本、哪个只能读、哪个是历史证据**，避免改错目录或把旧数字当成现状。
> 依据：2026-11-03 核查报告 + 本轮（2026-11-03）实际文件核对。维护规则见文末。

---

## 0. 一分钟判断

| 你要做的事 | 去哪个目录 |
|---|---|
| 改代码、加测试、跑回归 | `dev/`（唯一可写开发线） |
| 在真实 AstrBot/BGE 上验证 | `testbed/`（独立实例，不碰生产机器人） |
| 对照"交付时的原始状态" | `E5.0-F_0.15.116_工程包/`（只读，勿改） |
| 查上游实机基线 / 冻结话术 | `_e50f_standalone/`（= 桌面 E50F standalone，已验证同源） |
| 看当前真实进度数字 | `dev/STATUS.md`（脚本生成，勿手改） |
| 看历史核查结论与缺陷清单 | `E5.0-F_v0.15.116_工程进度核查报告.md` |

**结论：权威开发线是 `dev/`；`E5.0-F_0.15.116_工程包/` 是只读基线；E50C–E50F 是上游试点历史。**

---

## 1. 权威与只读

| 包 | 位置 | 类型 | 版本 | 规模 | 状态 |
|---|---|---|---|---|---|
| 开发工作区（权威） | `数字生命/dev/` | 目录 | **1.0.3** + 本轮改动 | 336 项用例（见 `dev/STATUS.md`） | **可写**，所有改动落在这里 |
| 交付基线（只读） | `数字生命/E5.0-F_0.15.116_工程包/` | 目录 | 0.15.116 | 61 个文件 / 1.09 MB（不含 `__pycache__`） | **只读**；含核查日志 `_audit/` |
| 交接压缩包 | `数字生命/E5.0-F_交接包_2026-11-03.zip` | zip | — | 1.18 MB，SHA256 `FD249C07…69A728` | 归档，不用于开发 |
| 真实运行环境 | `数字生命/testbed/` | 目录 | AstrBot 4.29.3 + BGE | 约 1.6 GB（含 venv 与模型） | 可重建（README 有从零重建命令）；**不是交付包** |

> "E5.0-F" 在本工作区是**二义**的：既指上游 standalone pilot（`astrbot_plugin_digitallife_shadow`，v0.5.3），
> 也指下游插件（`astrbot_plugin_digital_memory_store`；开发线已到 **v1.0.3**，只读交付基线仍是 v0.15.116）。
> 两者不是同一份代码，上游是"已实机验证的基线"，下游是"它的延续"。凡引用文档先确认说的是哪一个。

---

## 2. 上游试点（E50C → E50F）

| 包 | 类型 | 版本 | 规模 | 说明 |
|---|---|---|---|---|
| `Desktop/DigitalLife_Memory_E50F_Standalone_Windows_Complete/` | 目录 | shadow 0.5.3 | 含 `.venv` 与 35 个测试文件 | 上游最新基线；`FILE_SHA256_MANIFEST.json` 哈希 `838A341B…F7F16` |
| `数字生命/_e50f_standalone/` | 目录 | shadow 0.5.3 | 同上（不含 `.venv`） | **与上面同一份**：`FILE_SHA256_MANIFEST.json` 哈希完全相同；本工作区内的引用以它为准 |
| `Desktop/DigitalLife_Memory_E50F_Standalone_Windows_Complete.zip` | zip | 0.5.3 | 686 KB，`B1BF10F5…` | 上游分发件 |
| `Desktop/DigitalLife_Memory_E50E_…`（目录 + zip） | 目录/zip | 0.5.3 | 665 KB zip，`D4D122B2…` | 上游前一版，仅作对照 |
| `Desktop/DigitalLife_Memory_E50D_…`（目录 + zip） | 目录/zip | 0.5.3 | 655 KB zip，`FD6D9565…` | 上游更早版，仅作对照 |
| `Desktop/DigitalLife_Memory_E416_Standalone_Windows_Complete.zip` | zip | — | 525 KB，`7EAA3E1F…` | 早期 standalone，历史 |
| `Desktop/astrbot_plugin_digitallife_shadow_E50C_TEST_ONLY.zip` | zip | 0.5.3 | 5.7 KB，`AD7A8E5F…` | E50C 测试包（仅测试环境） |
| `Desktop/DigitalLife_E50C_AstrBot_v4.29.3_Load_Fix_v053_TEST_ONLY.zip` | zip | 0.5.3 | 6.2 KB，`7230665E…` | 修 AstrBot v4.29.3 加载问题 |
| `Desktop/DigitalLife_E50C_AstrBot_v4.29.3_Metadata_Fix_TEST_ONLY.zip` | zip | 0.5.3 | 6.2 KB，`D443B0F0…` | 修 metadata 兼容性 |

> 上游四个 E50x 目录的插件 `metadata.yaml` 都写 `version: 0.5.3`（`display_name` 仍写 "E5.0-C"）。
> 版本号不承载差异，**区分靠内容与 SHA256，不靠名字**。

---

## 3. 文档与交接材料

| 文件 | 用途 | 时效性 |
|---|---|---|
| `E5.0-F_v0.15.116_工程进度核查报告.md` | 六层逐节点核验、缺陷清单 P0–P3 | 2026-11-03 基线，**保留原样**（历史证据） |
| `E5.0-F_路线与进度规划_v1.md` | 里程碑/闸门/指标口径 | 计划，进度值以 `dev/STATUS.md` 为准 |
| `E5.0-F_交接资料清单.md` | 接手所需材料 A/B/C | 有效 |
| `远程执行单_E50F基线复现.md` | 人工在外部实例复现基线 | 待外部执行（回传结果写 `dev/docs/baseline_E50F_real.json`） |
| `dev/docs/METRICS.md` | 五个指标的代码口径 | 本轮新增 |
| `dev/STATUS.md` | 用例数/评估分/探针 | **脚本生成，唯一数字来源** |

---

## 4. 非本工程（不要混入判定）

| 路径 | 内容 |
|---|---|
| `Desktop/dafeiyu-astrbot-main/`、`.zip` | 第三方 AstrBot 相关插件仓库，与 E5.0-F 无代码依赖 |
| `Desktop/NapCat.Shell/`、`NapCatQQ-*.msi/.zip` | 平台侧工具（M1 实机联调需要，但不是本工程产物） |
| `Desktop/astrbot_plugin_multi_parser-main.zip`、`astrbot_plugin_proactive_chat-main.zip` | 其他插件 |
| `Desktop/python-3.12.10-amd64.exe`、`VC_redist.x64.exe` | 环境安装包 |

---

## 5. 维护规则

1. **新增包先登记**：任何新的 zip/目录进入桌面，先在本表加一行（版本、SHA256 前 8 位、是否权威）。
2. **版本号不作证据**：判定同一性用 `FILE_SHA256_MANIFEST.json` 或文件哈希。
3. **只读包不修改**：`E5.0-F_0.15.116_工程包/` 与 `_e50f_standalone/` 一旦变更，历史对照就失效。
4. **数字只引 STATUS.md**：文档里不再写"共 N 个测试"，改为"见 `STATUS.md`"。
5. 每次交付前跑 `python dev/scripts/report.py`，让 `STATUS.md` 与代码同步。

---

## 6. 本次核对的命令（可复跑）

```powershell
# 上游同源校验
Get-FileHash 'C:\Users\205\Desktop\DigitalLife_Memory_E50F_Standalone_Windows_Complete\FILE_SHA256_MANIFEST.json'
Get-FileHash 'C:\Users\205\Desktop\数字生命\_e50f_standalone\FILE_SHA256_MANIFEST.json'

# 交付基线规模
(Get-ChildItem 'E5.0-F_0.15.116_工程包' -Recurse -File |
  Where-Object { $_.FullName -notmatch '__pycache__' }).Count

# 交接包哈希
Get-FileHash 'E5.0-F_交接包_2026-11-03.zip'
```

_最后核对：2026-11-03。_
