# 安装 / 升级 / 回退（自己动手版）

> 适用：AstrBot 4.x（实测 **4.29.3**）+ Python 3.10–3.12。
> 这个插件默认是“装好但停用”的：**不填白名单、不打开开关，它什么都不做**。
> 记录、查看、备份不需要任何额外依赖；只有本地语义召回需要 `torch` + `sentence-transformers` + 模型目录。

## 0. 三分钟路径（最少步骤）

1. 解压 zip，把整个 `astrbot_plugin_digital_memory_store/` 放进 `<AstrBot根>/data/plugins/`；
2. 重启 AstrBot，插件列表出现「数字生命独立记忆库（自用版）」；
3. WebUI → 插件 → 该插件 → **配置**，改 3 项并保存：

   | 配置项 | 填什么 |
   |---|---|
   | `enabled` | `true` |
   | `automatic_memory_enabled` | `true`（一次打开采集、后台整理、受限召回） |
   | `allowed_sender_ids` | 你的 QQ 号，如 `123456`；多个用英文逗号 |

4. 重启 AstrBot，用**白名单号私聊**发 `/dlm 上下文状态`，看到“本地模型：未配置/就绪”即装好。

> 想少点手动：`presets/self.json` 就是上面这组开关（除白名单外），可以整段合并进配置。

## 1. 前置

| 项 | 要求 |
|---|---|
| AstrBot | 4.29.3 实测；其他 4.x 未验证 |
| Python | 3.12 实测；**必须与运行 AstrBot 的解释器一致** |
| 依赖 | 仅语义检索需要 `torch` + `sentence-transformers`，见包内 `requirements.txt` |
| 模型 | 可选；`model_dir` 指向本机 SentenceTransformer 目录（需含 `config.json`），**插件不会自动下载模型** |
| 数据 | 存在 `<AstrBot根>/data/plugin_data/astrbot_plugin_digital_memory_store/`，明文 |

## 2. 安装（手工，推荐）

```powershell
# 1) 解压到插件目录（目录名必须就是这个，不能改名）
Expand-Archive .\astrbot_plugin_digital_memory_store-1.0.4.zip -DestinationPath "$env:TEMP\dlm"
Copy-Item "$env:TEMP\dlm\astrbot_plugin_digital_memory_store" "C:\AstrBot\data\plugins\" -Recurse -Force

# 2) 重启 AstrBot 后，首次加载会按 _conf_schema.json 生成配置（默认全关）
#    配置文件：C:\AstrBot\data\config\astrbot_plugin_digital_memory_store_config.json
#    也可以只改 WebUI 插件页的「配置」，效果相同
```

**打开功能**：把 `presets/self.json` 的键值合并进配置，再补上白名单；等价于在 WebUI 里把
`enabled`、`automatic_memory_enabled` 打开并填写 `allowed_sender_ids`。

**接上本地语义（可选）**：

```powershell
# 一定用「运行 AstrBot 的那个 Python」，不是随便一个 python
C:\AstrBot\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

模型目录（`model_dir`）需要是 SentenceTransformer 格式，最少含 `config.json`、`modules.json`、
`model.safetensors`、`tokenizer*`、`vocab.txt`、`1_Pooling/config.json`。国内可直接从 ModelScope 取：

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

## 3. 安装（脚本，仅完整工程仓库）

脚本**不在插件 zip 里**，在完整工程仓库
<https://github.com/weijiecao/digital-life> 的 `dev/scripts/` 下（它需要仓库布局）：

```powershell
git clone https://github.com/weijiecao/digital-life.git
cd digital-life
python dev\scripts\install.py `
  --astrbot-root "C:\AstrBot" `
  --preset self `
  --allowed-sender-ids 123456 `
  --model-dir "D:\models\bge-small-zh-v1.5"
```

脚本做三件事：复制插件到 `data/plugins/`；按 schema 默认值 + 预设生成配置；已安装时先备份再覆盖。

## 4. 重启与自检

1. 重启 AstrBot，日志应出现 `Loading plugin astrbot_plugin_digital_memory_store`；
2. 白名单号私聊 `/dlm 上下文状态`：看“本地模型：就绪/加载中/未配置”；
3. `/dlm 记忆状态`：看采集数、事件数、后台任务；
4. 插件页 **panel** 视图：存储用量、容量预估、备份/轮转/完整性检查/归档/VACUUM。

## 5. 升级与回退（手工）

```powershell
# 升级：先备份旧目录与配置，再覆盖新版本（配置要保留的话不要删 config）
$root='C:\AstrBot'
Copy-Item "$root\data\plugins\astrbot_plugin_digital_memory_store" "$root\data\plugins_backup\dlm_$(Get-Date -f yyyyMMdd_HHmmss)" -Recurse
# 解压新 zip 覆盖 data\plugins\astrbot_plugin_digital_memory_store

# 回退：把备份目录改回原名即可（先移走当前版本）
```

升级**不会**改配置、也不会动数据；如果你的配置里没有新版本新增的键，插件会用 schema 默认值。

> 脚本化升级/回退（`upgrade.py` / `rollback.py`）同样只在完整工程仓库里。

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
| 日志找不到插件 | 目录名不是 `astrbot_plugin_digital_memory_store` | 用脚本安装，别手工改名 |
| `/dlm` 无响应 | 发送者不在白名单 / 不是私聊 / `enabled=false` | 检查这三项 |
| 没有记忆注入 | 模型目录无效、加载失败或首轮仍在加载 | 看 `/dlm 上下文状态` 的“本地模型” |
| 语义检索整体不可用 | 缺 `torch`/`sentence-transformers` | 在 **AstrBot 的解释器**里 `pip install -r requirements.txt` |
| 启动日志报 `[DL_MEMORY 自检]` | 缺依赖或 `model_dir` 不对 | 按日志里那句提示改；不影响记录/备份 |
| 想彻底停用 | 改 `off` 预设或 `enabled=false` | 已采集数据不会删除 |

## 8. 已知限制（安装前请读）

- 语义阈值默认 0.60（0.55–0.95 可配）；0.55 召回更高，但串味风险也更高。
- 自然改写与“之前那件事”类追问召回弱；后者需显式开启 LLM 候选筛选器（默认关闭）。
- 自动纠错仍弱（自造语料 4/35），不能把它当作“记得改期”来依赖。
- 未做 5,000 条压测、未做崩溃恢复演练；不要直接用于正式机器人。
