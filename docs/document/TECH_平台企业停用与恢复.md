# 平台企业停用与恢复技术设计

## 状态与权限

`suspend_governed_organization(UUID)` 和 `restore_governed_organization(UUID)` 只授权
`everydayai_runtime`。函数要求 Runtime、active Actor、super_admin、平台
`org_id=NULL`，行锁目标企业后执行条件状态转换，并在同一事务写
`governance_audit_log`。

## API

- `POST /org/admin/{org_id}/suspend`
- `POST /org/admin/{org_id}/restore`

两者使用 `PlatformDB`。不存在、状态冲突、权限不足和未知数据库故障分别映射为
404、409、403、503，客户端不接收数据库内部文本。
路由放在 `backend/api/routes/org_lifecycle.py`，共享依赖工厂位于
`org_dependencies.py`，避免既有 `org.py` 超过项目 500 行硬阈值。

## suspended 执行边界

企业登录、上下文、治理、配置 Bundle、Sync 和 WeCom 已有 active 校验。迁移 217
补齐邀请发现；迁移 218 为 Actor、媒体和定时任务发现补 active 过滤，并以数据库触发器
阻断 Runtime、Worker、WeCom、Sync 对 suspended 企业任务、Agent Runtime、企微
Inbox 及消息 Outbox 的后续写入。无法撤销的外部调用可能已经在途，但其后续数据库
提交失败关闭。

## 迁移与回滚

迁移 Runner 在单事务内应用迁移和账本记录。217 rollback 移除生命周期能力并恢复旧邀请
发现；218 rollback 删除执行 Fence 并恢复旧发现函数。回滚不修改企业当前状态或业务数据。

## 部署前迁移证据

External 验证必须从账本已到 216、且 217/218 均为 pending 的无数据隔离库开始。测试
通过项目 Migration Runner 依次应用 217、218，执行
`deploy/preflight/organization-lifecycle.sh`，再逆序 rollback 218、217。对象查询必须
证明 Fence 函数和 12 个 trigger 先消失、生命周期函数后消失，同时 synthetic 企业、
成员、配置标记、治理审计及企业当前状态保持不变。随后重新应用 217、218并再次通过
preflight。

External 权限矩阵同时验证 Runtime 平台 Scope 的唯一允许组合，缺失/未知/disabled/
普通/企业角色 Actor、org-bound 或错误 access kind，以及 Worker、WeCom Runtime、
Sync、PUBLIC、service_role、legacy 和独立未授权角色均失败关闭。函数必须归
`everydayai_owner`、启用 `SECURITY DEFINER`、固定
`search_path=pg_catalog, public`，Runtime 无 grant option、无继承旁路且不能直写
`organizations`。

仓库当前没有可复用的生产 schema-only 快照或 disposable clone 工具。
`deploy/init-database.sql` 与 017–149 历史迁移链存在已确认漂移，不能用“全新安装成功”
替代“当前生产结构兼容”证据，也不应在本任务修复。若需要重新取得当前生产结构，只能在
单独授权后执行只读 `pg_dump --schema-only --no-owner --no-privileges`，输出到临时目录，
不得包含表数据。生产主机本地安全扫描当前仍有疑似固定值来源无法稳定定位以及未完成
语义分类的高熵字面量，因此 schema 传输方案已停止；在独立安全任务闭环前，不得把
schema clone 列为 217/218 的部署证据。
