# 发布说明 v1.0.5

**给已经在用 AstrBot 的人：一条命令装完。** 不用再手工解压、改 JSON、找解释器装依赖。

## 装法

```powershell
# 解压 zip 后，进入 astrbot_plugin_digital_memory_store 目录
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会问三件事：AstrBot 根目录（自动找 `C:\AstrBot` 等）、允许的 QQ 号、要不要现在装语义依赖/模型。
然后依次完成：

| 步骤 | 细节 |
|---|---|
| 复制插件 | → `<AstrBot根>\data\plugins\astrbot_plugin_digital_memory_store`，旧版先移到 `data\plugins_backup\` |
| 写配置 | schema 默认值 + `self` 预设；`enabled=true`、`automatic_memory_enabled=true`；白名单**追加**不覆盖 |
| 装依赖（可选） | 装进 AstrBot 自己的 `.venv`：torch **CPU 版** + sentence-transformers（约 1 GB） |
| 下模型（可选） | ModelScope 的 `bge-small-zh-v1.5`（约 92 MB）→ `<AstrBot根>\data\models\`，并回填 `model_dir` |

全自动模式（远程/重装）：

```powershell
.\install.ps1 -AstrBotRoot "C:\AstrBot" -QQ 123456 -WithDeps -WithModel
```

没模型也能用：语义召回关闭，**记录、查看、备份、字面/背景上下文照常**。重启 AstrBot 后，
白名单号私聊 `/dlm 上下文状态` 即见状态。

## 也可以

- **WebUI 上传 zip**：AstrBot 4.x 插件页支持上传安装，本包结构能被它直接识别。注意它自动装依赖会拉
  **CUDA 版 torch**（体积大），想省空间用 `install.ps1`。
- **手工**：解压到 `data/plugins/` → 重启 → WebUI 插件配置里开 3 个开关。

## 本版其它改动

- `metadata.yaml` 增加 `short_desc`、`repo`（AstrBot `StarMetadata` 会读取并在插件页展示）；
- `INSTALL.md` 重写为三条安装路径 + 升级/回退 + 常见问题（执行策略、自动装依赖的坑）。

## 验收状态（如实说明）

| 项 | 状态 |
|---|---|
| 离线回归 | 336/336 |
| 控制面板 | 插件页 `panel`：统计/容量预估/备份/轮转/维护/归档 |
| 每轮供给（M2 自造语料） | 105/120 轮有上下文；背景-only 39 |
| 命中口径 R@1 | 65/100（供给口径 84/100） |
| 插件上下文生效（真实 DeepSeek 单条种子实验） | ✅ 2026-11-04 |
| 一键安装脚本 | 本机实测：复制/配置/白名单合并/备份/模型下载（44 s，92 MB）均通过；`install.ps1` 在 PowerShell 5.1 下解析与执行正常 |
| 真实聊天准确率 | **未验证**（缺脱敏真实语料） |
| L0 三天连续运行 | **未完成** |

## 已知限制

- 只支持私聊纯文本；群聊/图片/语音/多设备不支持。
- 语义阈值与窄路参数基于自造语料，真实分布下可能明显下降。
- 指代追问默认接不住；开启候选筛选器会外发原话。
- 纠错是“新旧并列 + 未确认”，不是自动改事实。
