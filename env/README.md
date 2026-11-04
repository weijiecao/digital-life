# env — 环境配置与可复现搭建

本目录负责“换一台机器也能把这套东西跑起来”。三个脚本 + 两份模板：

| 文件 | 作用 |
|---|---|
| `setup.ps1` | 一键：建 venv → 下 AstrBot 4.29.3 → 装依赖 → 下 BGE → 生成 `testbed\instance` → 自检 |
| `verify_env.ps1` | 只读自检：Python/依赖/AstrBot 版本/模型真实加载/实例配置/端口，全过返回 0 |
| `download_model.ps1` | 从 ModelScope 拉 `bge-small-zh-v1.5`（11 个文件，约 92 MB），已存在则跳过 |
| `requirements.lock.txt` | 已验证的完整依赖锁定（171 个包，`pip freeze` 产物） |
| `astrbot.pin.json` | AstrBot 版本锚点：4.29.3 + 源码 URL + sha256 |
| `configs/cmd_config.template.json` | AstrBot 实例配置模板（webchat + 本地 mock provider + aiocqhttp 6201，密码字段已清空） |
| `configs/plugin_config.template.json` | 插件配置模板（`allowed_sender_ids=1000000001` 占位，`model_dir` 由脚本回填） |

## 版本矩阵（唯一验证过的组合）

| 组件 | 版本 | 说明 |
|---|---|---|
| OS | Windows 10/11 x64 | 脚本是 PowerShell；Linux/macOS 需手工照做 |
| PowerShell | 5.1 或 7.x | 脚本带 UTF-8 BOM，5.1 下中文不乱码 |
| Python | **3.12.10** | 3.10–3.12 理论可用，锁定清单只在 3.12.10 上验证 |
| AstrBot | **4.29.3** | 源码运行（非 pip 包），sha256 见 `astrbot.pin.json` |
| torch | 2.15.0+cpu | CPU 轮子，从 `download.pytorch.org/whl/cpu` 装 |
| sentence-transformers | 5.7.0 | 5.x 在缺 `2_Normalize` 目录时仍可加载 |
| transformers | 5.22.1 | 随锁文件固定 |
| BGE 模型 | bge-small-zh-v1.5 | 512 维，冷加载 10.9–13.1 s，3.8–4.0 ms/条 |

## 用法

```powershell
# 仓库根执行（约 10–15 分钟，占约 1.6 GB）
powershell -NoProfile -ExecutionPolicy Bypass -File .\env\setup.ps1

# 只自检，不改任何东西
powershell -NoProfile -ExecutionPolicy Bypass -File .\env\verify_env.ps1
```

常用开关（`setup.ps1`）：

| 开关 | 用途 |
|---|---|
| `-Python C:\Python312\python.exe` | 指定创建 venv 的解释器 |
| `-SkipVenv` / `-SkipDeps` / `-SkipAstrBot` / `-SkipModel` | 分步执行或复用已有组件 |
| `-Force` | 重建 `testbed\instance` 下的配置文件（不动数据库） |

## 网络与镜像

中国大陆网络下已验证可达的组合（脚本内置）：

| 用途 | 源 |
|---|---|
| PyPI | `https://pypi.tuna.tsinghua.edu.cn/simple` |
| torch CPU | `https://download.pytorch.org/whl/cpu` |
| AstrBot 源码 | `https://github.com/AstrBotDevs/AstrBot/archive/refs/tags/v4.29.3.zip` |
| BGE 模型 | `https://www.modelscope.cn/models/AI-ModelScope/bge-small-zh-v1.5` |

**HuggingFace 直连在国内基本不可达**，所以模型默认走 ModelScope；若你所在网络反之，可自行改
`download_model.ps1` 里的 `$Base`。GitHub 下载 AstrBot 源码若失败，可手动下载同名 zip 放进
`testbed\downloads\`（脚本会先校验 sha256 再解压）。

## 这台机器上的实测结果（2026-11-04）

```
Python 3.12.10 | torch 2.15.0+cpu | sentence-transformers 5.7.0 | transformers 5.22.1
AstrBot 4.29.3 | BGE 512 维 / 冷加载 10.9–13.1 s
插件加载 ✅ 钩子注册 ✅ 采集落库 ✅ 注入可见 ✅ 命令路径 ✅
钩子→provider 0.13 s | 后台任务排空 2.06 s | 冷启动首条回复 TTFB 8.0 s → 0.017 s（预热修复后）
```

完整证据见 `dev/docs/REAL_ENV_REPORT.md` 与 `dev/reports/real_env.json`。

## 已知坑

1. **ExecutionPolicy**：默认策略禁止运行 .ps1。用 `powershell -ExecutionPolicy Bypass -File ...`
   或先 `Set-ExecutionPolicy -Scope Process Bypass`。脚本内不再自动改系统策略。
2. **PowerShell 5.1 读 UTF-8 无 BOM 会乱码**，进而导致语法错误；本目录脚本统一带 BOM，
   自己改脚本时别把 BOM 去掉。
3. **torch 必须装 CPU 轮子**：直接 `pip install torch` 会拉 CUDA 版（数 GB）。脚本先按
   `--index-url .../whl/cpu` 装 torch，再按锁定清单补齐其余包。
4. **`modules.json` 指向的 `2_Normalize` 目录上游未提供**，sentence-transformers 5.x 仍能加载
   （编码时用 `normalize_embeddings=True`）。若换模型或降级到 4.x，先跑 `verify_env.ps1`。
5. **端口**：mock provider 用 9101，aiocqhttp 用 6201，AstrBot 面板默认关（避免与生产实例
   6185 冲突）。被占用时改 `testbed\harness\*.py` 的端口参数与 `cmd_config.json` 中的
   `ws_reverse_port`。
6. **实例数据不要入库**：`testbed\instance`、`testbed\logs`、`testbed\models`、`testbed\.venv`、
   `testbed\AstrBot-4.29.3` 都在 `.gitignore` 里；这些是本地生成物。
