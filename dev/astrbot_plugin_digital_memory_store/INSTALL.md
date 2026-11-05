# 安装 / 升级 / 回退

> 适用：AstrBot 4.x（实测 **4.29.3**）+ Python 3.10–3.12，Windows。
> 默认是“装好但停用”的：**不填白名单、不打开开关，它什么都不做**。
> 记录、查看、备份不需要任何额外依赖；只有本地语义召回要 `torch` + `sentence-transformers` + 模型目录。

## 0. 一键安装（推荐，Windows）

解压 zip 后，进到 `astrbot_plugin_digital_memory_store\` 目录，双击或在 PowerShell 里跑：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

脚本只问你三件事：**AstrBot 根目录**（会自动找 `C:\AstrBot` 等）、**允许的 QQ 号**、**要不要现在装语义依赖/模型**。
然后它一次做完：

1. 复制插件到 `<AstrBot根>\data\plugins\astrbot_plugin_digital_memory_store`（已有旧版先备份）；
2. 按 `self` 预设生成配置并打开 `enabled` / `automatic_memory_enabled`，写入白名单；
3. （可选）把 **torch CPU 版 + sentence-transformers** 装进 AstrBot 自己的 `.venv`；
4. （可选）从 ModelScope 下载 `bge-small-zh-v1.5`（约 92 MB）到 `<AstrBot根>\data\models\`,并回填 `model_dir`。

不想交互就一次给全参数（适合远程/重装）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -AstrBotRoot "C:\AstrBot" -QQ 123456 -WithDeps -WithModel
```

| 参数 | 作用 |
|---|---|
| `-AstrBotRoot` | AstrBot 根目录（含 `data\plugins` 或 `main.py`） |
| `-QQ` | 白名单 QQ，多个用英文逗号 |
| `-WithDeps` / `-SkipDeps` | 装 / 不装语义依赖（不指定则询问） |
| `-WithModel` / `-SkipModel` | 下载 / 不下载模型（不指定则询问） |
| `-ModelDir` | 指向已有的 BGE 目录，跳过下载 |
| `-Preset off` | 只装不启用 |
| `-Force` | 重建配置（默认保留原配置，只补关键开关） |

装完**必须重启 AstrBot**，再用白名单号私聊发 `/dlm 上下文状态`。

## 0.1 也可以直接在 WebUI 上传 zip

AstrBot 4.x 的插件管理提供“上传 zip 安装”（`/api/plugin/install-upload`），本包结构可被它直接识别
（根目录一层 `astrbot_plugin_digital_memory_store/` + `metadata.yaml`）。上传后重启，再去插件配置里
打开 `enabled`、`automatic_memory_enabled` 并填 `allowed_sender_ids`。
注意：AstrBot 自动装依赖时会按 `requirements.txt` 装 **CUDA 版 torch**（体积大）；想省空间用 `install.ps1`。

## 0.2 手工三分钟（不想跑脚本）

1. 解压，把整个 `astrbot_plugin_digital_memory_store/` 放进 `<AstrBot根>/data/plugins/`（目录名不要改）；
2. 重启 AstrBot，插件列表出现「数字生命独立记忆库（自用版）」；
3. WebUI → 插件 → 该插件 → **配置**，改 3 项并保存：

   | 配置项 | 填什么 |
   |---|---|
   | `enabled` | `true` |
   | `automatic_memory_enabled` | `true`（一次打开采集、后台整理、受限召回） |
   | `allowed_sender_ids` | 你的 QQ 号，如 `123456`；多个用英文逗号 |

4. 重启 AstrBot，用**白名单号私聊**发 `/dlm 上下文状态`，看到“本地模型：未配置/就绪”即装好。

> `presets/self.json` 就是第 3 步那组开关（除白名单外），可以整段合并进配置文件。

## 1. 前置

| 项 | 要求 |
|---|---|
| AstrBot | 4.29.3 实测；其他 4.x 未验证 |
| Python | 3.12 实测；**依赖必须装进运行 AstrBot 的那个解释器** |
| 依赖 | 仅语义检索需要 `torch` + `sentence-transformers`，见包内 `requirements.txt` |
| 模型 | 可选；`model_dir` 指向 SentenceTransformer 目录（含 `config.json`）。`install.ps1 -WithModel` 可自动下载 |
| 数据 | `<AstrBot根>/data/plugin_data/astrbot_plugin_digital_memory_store/`，明文 |

## 2. 手动接语义（可选）

```powershell
# 一定用「运行 AstrBot 的那个 Python」，不是随便一个 python
C:\AstrBot\.venv\Scripts\python.exe -m pip install --index-url https://download.pytorch.org/whl/cpu torch
C:\AstrBot\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

模型目录需要含 `config.json`、`modules.json`、`model.safetensors`、`tokenizer*`、`vocab.txt`、
`1_Pooling/config.json`。国内从 ModelScope 取：

```powershell
$base='https://www.modelscope.cn/models/AI-ModelScope/bge-small-zh-v1.5/resolve/master'
$dst='D:\models\bge-small-zh-v1.5'
New-Item -ItemType Directory -Force -Path $dst,"$dst\1_Pooling" | Out-Null
foreach($f in 'config.json','config_sentence_transformers.json','modules.json','sentence_bert_config.json',
              'special_tokens_map.json','tokenizer.json','tokenizer_config.json','vocab.txt',
              '1_Pooling/config.json','model.safetensors'){
  Invoke-WebRequest "$base/$f" -OutFile (Join-Path $dst $f) -UseBasicParsing
}
```

然后用启动日志里的 `[DL_MEMORY 自检]` 或 `/dlm 上下文状态` 的“本地模型”一项确认；没有模型时
**记录、查看、备份照常**，只是语义召回关闭，注入退化为字面/背景路径。

## 3. 脚本安装（仅完整工程仓库）

`dev/scripts/install.py` 需要仓库布局，不在插件 zip 里；在完整工程仓库
<https://github.com/weijiecao/digital-life> 下用：

```powershell
git clone https://github.com/weijiecao/digital-life.git
cd digital-life
python dev\scripts\install.py --astrbot-root "C:\AstrBot" --preset self --allowed-sender-ids 123456
```

## 4. 重启与自检

1. 重启 AstrBot，日志应出现 `Loading plugin astrbot_plugin_digital_memory_store`；
2. 白名单号私聊 `/dlm 上下文状态`：看“本地模型：就绪/加载中/未配置”；
3. `/dlm 记忆状态`：看采集数、事件数、后台任务；
4. 插件页 **panel** 视图：存储用量、容量预估、备份/轮转/完整性检查/归档/VACUUM。

## 5. 升级与回退

再跑一次 `install.ps1` 即为升级：它会把旧目录移到 `data\plugins_backup\`，配置保留并只补关键开关。

手工方式：

```powershell
$root='C:\AstrBot'
Copy-Item "$root\data\plugins\astrbot_plugin_digital_memory_store" "$root\data\plugins_backup\dlm_$(Get-Date -f yyyyMMdd_HHmmss)" -Recurse
# 解压新 zip 覆盖 data\plugins\astrbot_plugin_digital_memory_store
# 回退：把备份目录改回原名（先移走当前版本）
```

升级**不会**改配置、也不会动数据；新版本新增的键会用 schema 默认值。

## 6. 数据、隐私与边界

- 所有原话、索引、备份都在本机 AstrBot 数据目录，**明文**；请自行控制目录权限与备份清理。
- 每轮写一行本地 `latency.jsonl`（耗时/是否注入，2 MB 自动裁剪），可用 `latency_metrics_enabled=false` 关闭。
- 默认不调用任何远程模型，也不把原话外发；唯一例外是显式开启的 `model_judge_enabled`
  （会把问题、最近 1–2 条原话与候选原话发给所选模型服务商，默认关闭）。
- 只处理**私聊纯文本**；群聊、图片/语音、多设备同步均为 Non-goals。
- 所有结论保持“未确认”；不自动确认事实、不自动修改关系。

## 7. 常见问题

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 装完完全没反应 | `enabled=false`（默认）或没填白名单 | 打开 `enabled` 与 `automatic_memory_enabled`，填 `allowed_sender_ids` |
| `install.ps1` 直接闪退 | 执行策略禁止脚本 | 用 `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1` |
| 日志找不到插件 | 目录名不是 `astrbot_plugin_digital_memory_store` | 用脚本安装，别手工改名 |
| `/dlm` 无响应 | 发送者不在白名单 / 不是私聊 / `enabled=false` | 检查这三项 |
| 没有记忆注入 | 模型目录无效、加载失败或首轮仍在加载 | 看 `/dlm 上下文状态` 的“本地模型” |
| 语义检索整体不可用 | 缺 `torch`/`sentence-transformers` | 在 **AstrBot 的解释器**里装，或用 `install.ps1 -WithDeps` |
| 启动日志报 `[DL_MEMORY 自检]` | 缺依赖或 `model_dir` 不对 | 按日志里那句提示改；不影响记录/备份 |
| 想彻底停用 | 改 `off` 预设或 `enabled=false` | 已采集数据不会删除 |

## 8. 已知限制（安装前请读）

- 语义阈值默认 0.60（0.55–0.95 可配）；0.55 召回更高，但串味风险也更高。
- 自然改写与“之前那件事”类追问召回弱；后者需显式开启 LLM 候选筛选器（默认关闭）。
- 自动纠错仍弱（自造语料 4/35），不能把它当作“记得改期”来依赖。
- 未做 5,000 条压测、未做崩溃恢复演练；不要直接用于正式机器人。
