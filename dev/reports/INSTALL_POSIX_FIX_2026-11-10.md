# 安装器 POSIX 只读目录修复

- 日期：2026-11-10
- 问题：`_force_rmtree` 用 `stat.S_IWRITE` 覆盖目录 mode，POSIX 上丢掉 read/execute，导致覆盖/升级/回滚的目录访问 error。
- 修复：`_make_writable` 用 `mode | S_IWUSR | S_IXUSR` 保留原权限。
- 回归：只读文件 + 只读目录树可删除。
- 测试：501/501（Windows）。
- 边界：未在 Linux 复跑；Windows chmod 语义不等价。
