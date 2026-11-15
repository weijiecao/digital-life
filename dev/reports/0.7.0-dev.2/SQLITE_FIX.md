# dev.2：SQLite 临时连接释放修复

基线：远端 `4c07d5d53f79ea3187ab6aefd6177a66fa805f6a`。已核对其源码树与 dev.1 本地提交 7a30ef0 完全相同。
修复提交：`c11374257c2fae9662292bca7855ca8aabfa7cad`。分支：`fix/windows-sqlite-lifecycle`。
版本：`0.7.0-dev.2`。本次未推送远端，未创建 Release/tag。

## 已确认的问题与修改

`with sqlite3.connect(...)` 只管理事务提交/回滚，不关闭连接。dev.1 的备份、校验、迁移和测试夹具存在这种遗漏。Linux 允许删除仍打开的文件，原测试可能因此通过；Windows 临时目录清理可能报 PermissionError。

本次将业务短期连接统一改为 managed_database：在事务上下文外层 finally 显式 close，覆盖正常返回、目标连接打开失败、backup 抛错、schema 校验失败和跨库回执校验失败。测试夹具使用 closing 加事务上下文，避免测试本身遗留连接。未增加 sleep/gc.collect 或吞掉 PermissionError。

修改文件：application/storage.py、application/migration.py、tests/completion_suite.py；另更新 README 与三个版本标识。长期业务连接仍由原运行时生命周期关闭，没有改动记忆组织和恢复协议。

## 验证范围

- 修复前：新增 8 项资源释放检查全部失败，稳定证明连接未明确关闭。
- 修复后：这 8 项与新增的提交/回滚语义检查全部通过。
- 全量离线回归：原插件 20 + 功能集成 47 + 核心 137 = **204 个独立用例通过**。unittest 外层显示 22，是 20 项加两个子进程包装器，不重复相加。
- 测试持有连接强引用，逐一确认连接已 close 且不能继续 execute；不依赖垃圾回收时机。成功快照测试还立即重命名并删除副本。
- 运行环境是 Linux/Python 3.12，**未在 Windows 实机复跑，不宣称已通过 Windows 验收**。未提供的本机完整错误栈也不能全部归因于这一个缺陷。
- 从提交源码 ZIP 重新构建，安装包 SHA-256 一致。
- Git bundle 已在仅导入基线的新仓库中验证，导入后提交 ID 精确等于 `c11374257c2fae9662292bca7855ca8aabfa7cad`。

## Windows 复跑

在源码目录使用安装好插件依赖的 Python 执行：

```powershell
python -m unittest discover -s tests -t . -v
```

若仅复跑新增功能包装器：

```powershell
python -m unittest tests.test_completion -v
```

仍有 PermissionError 时应保留完整 traceback（含被占用路径、用例名、Python 版本），不要把跳过清理或删除失败视为通过。

## 保留原提交对象

工程包现在包含增量 `windows-sqlite-fix.bundle`。需要已有基线 `4c07d5d53f79ea3187ab6aefd6177a66fa805f6a`。在基线仓库内执行：

```powershell
git bundle verify PATH/windows-sqlite-fix.bundle
git fetch PATH/windows-sqlite-fix.bundle refs/heads/fix/windows-sqlite-lifecycle
git switch -c fix/windows-sqlite-lifecycle FETCH_HEAD
```

这样保留修复提交原始 ID；不会只导入源码后重建另一个提交。另附普通 patch 与完整源码 ZIP，任选一种接入方式，不要重复应用。

升级插件后完整重启 AstrBot。dev.2 仍是开发版；真实宿主、Windows 和长跑发布门槛不变。

## 安装包

`astrbot_plugin_digital_life-0.7.0-dev.2.zip`；222,031 B；78 文件。

SHA-256：`06756a5f79f172e70e6be596b45047b64047a12d72f53d6d25b1ac9e0f02a929`。
