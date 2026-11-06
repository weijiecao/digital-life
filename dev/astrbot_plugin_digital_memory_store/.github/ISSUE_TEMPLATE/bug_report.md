---
name: 问题反馈
about: 装不上、命令没反应、采集/注入不生效等
title: "[问题] "
labels: bug
assignees: ''
---

<!-- 提交前请先看 docs/TROUBLESHOOTING.md：多数问题在前三条诊断命令里就能定位。
     请勿粘贴真实聊天内容、QQ 号、API Key。 -->

## 环境

- AstrBot 版本：
- 插件版本（`metadata.yaml` 的 `version`，或 `/dlm 帮助` 里显示）：
- 操作系统 / Python：
- 是否用 `install.ps1` 安装：是 / 否（手工解压 / WebUI 上传）

## 三条诊断命令的输出

```powershell
# 1) 插件在不在
Test-Path "<AstrBot根>\data\plugins\astrbot_plugin_digital_memory_store\main.py"

# 2) 关键开关
Get-Content "<AstrBot根>\data\config\astrbot_plugin_digital_memory_store_config.json" -Raw -Encoding UTF8 |
  ConvertFrom-Json | Select-Object enabled, automatic_memory_enabled, allowed_sender_ids, model_dir

# 3) 语义依赖（没有模型时此条可跳过）
<AstrBot根>\.venv\Scripts\python.exe -c "import torch, sentence_transformers; print(torch.__version__)"
```

（把输出贴在这里，QQ 号请打码）

## 现象

- 期望发生什么：
- 实际发生了什么：
- 复现步骤：

## 相关日志

```text
（只保留含 [DL_MEMORY 或 Loading plugin 的行；不要整段粘贴聊天内容）
```

## 已经排除了什么

- [ ] 已重启 AstrBot
- [ ] `enabled` 与 `automatic_memory_enabled` 均为 `true`
- [ ] 是私聊，且发送者在 `allowed_sender_ids` 里
- [ ] 已看 `docs/TROUBLESHOOTING.md` 对应章节
