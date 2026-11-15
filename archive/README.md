# 历史归档

本目录保存 `digital-life` 旧插件源码、旧发布包、旧插件测试与旧构建脚本，仅作历史查阅。
活动 `dev/` 只保留最新统一插件 `dev/astrbot_plugin_digital_life/` 及其共享核心模块。

```text
archive/
  plugins/
    astrbot_plugin_digital_memory_store/   旧数字记忆基座插件
    astrbot_plugin_memory_shadow/          旧 shadow 插件
  releases/                                旧插件/引擎发布包与清单
  tests/                                   旧插件相关测试
  scripts/                                 旧插件构建/安装脚本
```

## 运行归档测试

归档测试仍引用旧插件模块，需要把归档插件目录加入 `PYTHONPATH`：

```powershell
$env:PYTHONPATH = "$PWD\archive\plugins;$PWD\archive"
python -m unittest discover -s archive\tests -t archive -v
```

归档测试不再计入活动 `dev/tests` 回归；真实宿主、Windows 和长跑验收仍以当前统一插件为准。

最新插件只能使用 `dev/astrbot_plugin_digital_life/`，不要同时启用旧 shadow。
