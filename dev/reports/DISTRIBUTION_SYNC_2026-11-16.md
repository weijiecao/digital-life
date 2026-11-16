# 分发仓库同步记录（2026-11-16）

按 0.8.0 方案 PR-C 执行：从**已验收的干净提交**导出，先用 `compare_distribution.py` 只读比较，
只同步托管文件与明确列出的根 README 更新，保留其他文件；随后在分发仓库发布同版本 Release。

## 1. 固定标识

| 对象 | 标识 | 说明 |
| --- | --- | --- |
| 主仓库被测源码 S | `b245057501ac5081316eb6e4e69155ec7d37b326` | `v0.8.0-dev.1` tag 所指提交；源码回归 386、包级 47＋137＋20 都在此验证 |
| 插件包 | `05ba15e9d98792220c7ddf1615bad8448c178175626a6ebbc529c57a9ec90cff` | 79 文件 / 223,273 bytes |
| 分发仓库同步前 D | `d703a09bf1bb83181ea1ac1944c2e11d9642aa50` | 旧的工程包布局 |
| 分发仓库同步后 D2 | `f0b30758720c18c8b9a407f11ecbdbf8155b5caf` | 托管 79 文件 + `SOURCE_MANIFEST.json` |

## 2. 导出与只读比较（同步前）

从 `v0.8.0-dev.1` 的干净检出构建（`--require-clean`，`source_commit=b245057`、`dirty=false`），
用 `--layout engineering` 导出 79 个文件 + `SOURCE_MANIFEST.json`。

`compare_distribution.py` 同步前结果：

| 类别 | 数量 | 内容 |
| --- | --- | --- |
| 内容一致 | 69 | 插件目录与 `shared/` 多数文件 |
| 内容变化 | 8 | 插件 `README.md`、`main.py`、`config.py`、`application/service.py`、`metadata.yaml`；`shared/{astrbot_memory_input,dlm_console,memory_output}/README.md` |
| 仅导出有 | 1 | `shared/memory_engine/README.md`（新增） |
| 换行差异 | 1 | `astrbot_plugin_digital_life/requirements.txt`（分发侧 CRLF） |
| 非托管 | 121 | `archive/` 91、`verification/` 16、`tests/` 8、`tools/` 2、`docs/` 1、根 `.gitignore`/`LICENSE`/`README.md` 3 |

变化全部可解释：版本号统一到 `VERSION`（`metadata.yaml`、`config.py`、`main.py`、`service.py`）、
文档同步（4 个 README）、新增引擎说明（`memory_engine/README.md`）。**没有分发仓库独有的代码修复。**

## 3. 同步内容

| 动作 | 范围 |
| --- | --- |
| 写入托管文件 | 79 个（`astrbot_plugin_digital_life/` + `shared/`），逐字节来自导出目录 |
| 写入来源清单 | `SOURCE_MANIFEST.json`（来源提交、逐文件哈希、包哈希、布局、工具版本） |
| 明确列出的额外改动 | **只有根 `README.md`**：版本改 0.8.0-dev.1、安装与校验、托管/非托管边界、指向主仓库 |
| 保留不动 | `tests/`、`tools/`、`verification/`、`docs/`、`archive/`、`.gitignore`、`LICENSE` |
| 删除 | 无（首次同步，没有过期托管文件） |

同步后再次比较：**79/79 内容一致，0 变化、0 缺失、0 换行差异，非托管 122 项保持原样**
（121 + `SOURCE_MANIFEST.json`）。随后从远端重新克隆核对，结果相同（D2）。

## 4. 本轮修复：结账依赖的换行

用一个干净克隆构建时发现同一个提交产出了不同字节：`.gitignore`、`.toml`、`.svg` 等未被
`.gitattributes` 覆盖的文本文件在 Windows 上以 CRLF 检出，构建直接打包了这些字节。
已在主仓库修正 `.gitattributes`（`* text=auto eol=lf` 并显式列出 `toml/svg/cjs/jsonl` 等），
提交 `f9a1637`。修正后同一提交在 LF 结账下重新构建得到 `05ba15e9`，与已发布包一致。
tag 未移动；该修复在 tag 之后。

## 5. 分发仓库 Release

- 分发仓库 tag：`v0.8.0-dev.1` → `f0b3075`（内容来自 `b245057`，两仓库 SHA 不同属正常）
- Release：<https://github.com/weijiecao/astrbot_plugin_digital_life/releases/tag/v0.8.0-dev.1>
  标记为 pre-release，附件 5 个：

| 附件 | 大小 | SHA-256（下载后复核） |
| --- | --- | --- |
| `astrbot_plugin_digital_life-0.8.0-dev.1.zip` | 223,273 | `05ba15e9…cff` |
| `digital-life-source-0.8.0-dev.1.zip` | 5,435,996 | `b3e40ea3…dd5` |
| `digital-life-engineering-0.8.0-dev.1.zip` | 5,566,182 | `40dd725a…011` |
| `digital-life-release-0.8.0-dev.1.json` | 1,250 | `cd36aa1a…e36` |
| `SHA256SUMS-0.8.0-dev.1.txt` | 423 | `f3817f06…b59` |

上传后重新下载全部附件，按 `SHA256SUMS` 逐项复核，5/5 一致；GitHub 记录的 asset digest 与之一致。

## 6. 边界

- `flat` 布局（仓库安装）仍未验证，不声明可用；本次使用 `engineering` 布局。
- DOM 接线验证另行记录（`DOM_WIRING_2026-11-16.md`），真实浏览器与真实宿主仍未验收。
- 分发仓库的 `tests/tools/verification/docs/archive` 保留为历史工程材料，不再更新；
  修复必须回到主仓库，再从新提交导出。
