# RUNBOOK 171–185：Worker Control 与 Sync 数据域生产恢复

> 状态：代码与测试完成，生产待执行  
> 原则：任何检查失败立即停止；不得跳过 owner、账本或真实链路验证。

## 1. 当前已知状态

- 生产代码文件已同步，但 Backend、Sync、WeCom、Actor 进程启动时间早于同步时间。
- 迁移账本 165–170 为 `applied`，171–178 无记录，179 为 `failed`。
- 179 的事务已回滚，`scheduled_task_runs` 尚无 fencing 三列。
- 恢复完成前不得重启四个服务，避免磁盘新代码提前加载。

## 2. 本地发布门禁

1. 定向测试、External PostgreSQL 事务测试、迁移 180–185 角色矩阵、并发 claim
   与 rollback 事务验证全部通过。
2. `bash -n` 通过所有新增及修改的部署脚本。
3. 迁移 171–180 均存在精确 rollback。
4. 工作区无本任务之外的暂存内容。
5. `everydayai_sync` 独立角色及 `.env.sync` 已准备，但服务尚未切换。

## 3. 生产只读复核

运行 `deploy/preflight-tenant-cutover.sh`。在 179 failed 尚未协调时，
Worker Control 子门禁必须失败并明确报告 `WORKER_CONTROL_MIGRATION_INVALID`。
同时核对服务 active、启动时间、四表/序列 owner、账本与 fencing 三列。

## 4. 管理员所有权转移

使用独立管理员连接执行：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/transfer-worker-control-ownership.sh
```

转移后只读确认四表及 `error_logs_id_seq` 均归 `everydayai_owner`；旧角色保留兼容
CRUD/sequence 权限，Runtime/Worker 尚不因该步骤获得直表权限。

随后执行 Sync 数据域转移：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/transfer-sync-domain-ownership.sh
```

确认脚本列出的 ERP/快麦表、序列、物化视图与函数均归 `everydayai_owner`；
旧角色仅保留迁移穿越期兼容权限，`everydayai_sync` 尚不获得迁移 181–185 的能力。

## 5. failed 账本协调

先证明 179 没有残留列、触发器、函数新重载或 policy，并核对失败记录 error_summary，
再由管理员在单事务中删除
`179_scheduled_run_fencing.sql` 的唯一 failed 记录。禁止修改 checksum、伪造 applied
或直接执行迁移 SQL。删除后立即运行标准 Runner `plan`，预期 pending 精确为 171–185。

## 6. 标准迁移与服务切换

使用 `MIGRATION_DATABASE_URL` 和标准 Runner 应用 171–185。部署门禁会在 apply 前再次
验证 Worker Control owner。全部迁移及账本成功后，按顺序重启：

1. Backend
2. Sync
3. WeCom
4. Conversation Actor

每个服务必须 active 且 readiness/log 门禁通过后才能继续下一个。

## 7. 验收

- 171–185 全部 `applied`，failed 为 0。
- fencing 三列、触发器、token 版函数和迁移 180 两项 FORCE RLS 存在。
- Worker 对四表无任何直表权限；Runtime 对任务表 CRUD、运行表只读。
- ERP/快麦 Sync 域 FORCE RLS 生效；Sync 与 Runtime 均无外部同步队列表直权，
  只能使用各自窄 RPC。
- 两个并发 Sync claim 对同一请求最多一个成功；错误 token 不能续租或提交终态。
- Web 登录/刷新/登出、普通 Chat、企微消息、图片/视频终态、错误日志、知识指标正常。
- 企业 A 无法读取企业 B 定时任务；停用员工失去访问；真实定时任务只产生一个结果消息。
- 最近日志不再增长 `InsufficientPrivilege`、role scope mismatch、lease lost 异常。

## 8. 回滚边界

服务尚未重启时，迁移失败由 Runner 保持单迁移事务边界并停止部署。服务已重启后需要回滚：

1. 先切回兼容代码与服务数据库配置。
2. 逆序执行 185 至 171 rollback。
3. 确认两张定时表不再 FORCE RLS。
4. 执行 `rollback-sync-domain-ownership.sh`，再执行
   `rollback-worker-control-ownership.sh`。

不得只删除账本、只改 owner 或只重启服务。
