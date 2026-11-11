# Memory Engine RC2（离线候选包）

状态：R0 / R1 NO-GO。该包用于隔离验证，不是 V1 生产插件，不自动部署。

运行要求：Python 3.10–3.12；精确上下文预算需安装 requirements-release.txt。
包内不包含模型权重。BGE 是可选实验路径；没有通过真实查询的误注入与召回联合验收，不能默认为生产配置。

解压后可执行：

```
python -m pip install -r requirements-release.txt
python -m memory_engine.cli --db local-test.sqlite3 health
python -m memory_engine.cli --db local-test.sqlite3 ingest --scope demo --seq 1 --text "周末去木桥市集"
python -m memory_engine.cli --db local-test.sqlite3 context --scope demo --query "木桥市集" --rendered --budget-tokens 200
```

入库与更正 CLI 默认计算文本 SHA-256。重复 seq 而文本不同返回失败；不要通过固定 --hash 掩盖改写输入。

使用解压包中的 verify_r0_bundle.py 可验证原 ZIP（需要同目录的外部 JSON 清单）：

```
python verify_r0_bundle.py --bundle /path/to/memory-engine-r0-rc2.zip --exact-budget
```

该验证检查整包与每个文件哈希，并在隔离子进程验证导入、CLI、入库幂等/冲突、更正/历史、重启、撤回和最终 token 预算。
BUNDLE_MANIFEST.json 的 source_sha256 标识实际打包源码；base_commit 仅表示补丁起点，不冒充已提交的源码。

上线仍需要：独立质量验收、目标 Windows CI、真实平台 72h 与用户效果记录。离线 smoke PASS 不构成上线许可。
