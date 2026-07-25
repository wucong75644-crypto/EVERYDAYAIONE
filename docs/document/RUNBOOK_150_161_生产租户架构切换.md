# RUNBOOK 150–164：生产租户架构切换

> 状态：生产已完成角色/owner 准备及 150–161；162 与旧配置导入待执行
> 范围：数据库角色、对象所有权、RLS、能力门面、治理控制面、配置控制面及旧配置导入
> 不包含：旧配置删除、旧表删除、配置消费者切换、Skill/MCP/Goal 功能开发

## 1. 目标与不可变边界

本流程把生产数据库从共享登录角色逐步切换到最小权限角色，并保持以下租户模型：

- 平台全局管理员管理平台级配置和治理能力。
- 企业、企业员工与其他企业相互隔离。
- 企业员工同时拥有个人配置，并继承企业共享配置。
- 散客使用个人 Scope，与企业数据隔离。
- Web、WebSocket、Conversation Actor、Worker 和企微共享同一租户身份语义。
- 服务器上的用户 Workspace 等价于该用户自己的本地工作区。

全过程必须遵守：

- 不直接手工执行迁移 SQL 片段。
- 不手工修改 `schema_migration_ledger`。
- 不删除或修改旧配置事实。
- 不把管理员、migrator、config import reader 的连接注入业务服务。
- 不在未完成服务切换验证前撤销旧角色兼容权限。
- 任一检查点失败立即停止，不跨检查点继续。
- 所有数据库结构变更只能在明确批准的维护窗口内执行。

## 2. 角色与服务映射

| 身份 | 用途 | 禁止事项 |
|---|---|---|
| `everydayai_owner` | NOLOGIN 对象 owner | 不得成为业务连接 |
| `everydayai_migrator` | Migration Runner | 不得成为业务连接 |
| `everydayai_config_import_reader` | 161 一次性旧配置快照 | 不得直读旧表 |
| `everydayai_runtime` | Web/API runtime | 不得执行 Worker/WeCom 专属能力 |
| `everydayai_wecom_runtime` | 企微入站 runtime | 不得执行 Web 认证能力 |
| `everydayai_worker` | Actor、后台任务 | 不得执行 Web/WeCom 入口能力 |
| `everydayai` | 旧应用和 Sync 过渡身份 | 最终只保留已审计的 Sync 用途 |

环境文件必须为 `0600`，并通过：

```bash
bash deploy/validate-tenant-db-env.sh /var/www/everydayai/backend
```

验证器要求 runtime、WeCom runtime、worker、migrator 与 sync 连接相互独立；
worker client 必须与 worker 使用同一身份。

## 3. 变更窗口前置材料

执行前必须记录：

- 已批准的 Git commit SHA。
- 全库备份位置、完成时间、校验结果和恢复负责人。
- 数据库管理员、执行人、复核人。
- 维护窗口开始与结束时间。
- Backend、Actor、WeCom、Sync 的健康基线。
- 当前数据库连接角色与连接数量。
- 当前迁移账本完整导出。
- 第一批和第二批目标对象的 owner、RLS、policy 与 ACL 快照。
- `organizations`、`org_members`、`org_configs` 和快麦旧凭证的计数基线。

没有经过恢复验证的备份，不得开始所有权转移。

## 4. 固定执行顺序

顺序不可交换：

1. 生产只读审计。
2. 创建独立数据库角色和环境文件。
3. 第一批 Agent Runtime 对象所有权转移。
4. 第二批 Runtime/Message 对象所有权转移。
5. 建立旧配置 export definer 的单表只读 ACL。
6. 使用标准 Migration Runner 一次性应用 150–164。
7. 执行旧配置 dry-run/import。
8. 分服务切换数据库角色并完成业务验证。
9. 确认旧角色连接归零。
10. 撤销旧角色的临时 owner 成员关系。

关键原因：迁移 152 会操作第二批 `wecom_get_or_create_user` 等函数权限，因此第二批
owner 转移必须发生在 152 之前，而不是仅在 153 之前。

标准 Migration Runner 的 `apply` 会应用全部 pending migration，不支持按 `--through`
分批 apply。因此两批 owner 和旧源表 ACL 必须先就绪，再一次性应用 150–164；不得假设 Runner 会
停在 151 或 160。

## 5. 检查点 A：生产只读审计

在仓库根目录执行：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/preflight-tenant-cutover.sh
```

脚本只接受 150–164 全部未应用或全部已应用状态；部分应用、failed、checksum 漂移、
部分角色、部分 owner 或未知 owner 都会失败关闭。成功时只输出阶段、事实计数和各角色
连接数，不输出连接串或配置值。

只读审计至少确认：

- 生产 commit 与计划 commit 的关系。
- 迁移账本已应用边界及 checksum 无漂移。
- 150–164 均未出现 `failed` 记录。
- 第一批 13 张表及资产函数存在。
- 第二批 18 张表、列 sequence 及固定业务函数存在。
- 当前 owner 只属于允许的旧角色或 `everydayai_owner`。
- 没有未知的 FORCE RLS 状态。
- 生产服务仍使用旧连接时，旧角色能够维持现有业务。

审计事务必须设置 `READ ONLY`，输出不得包含连接串、Token、Cookie、密钥或配置值。

判定：

- 结构与代码假设一致：进入检查点 B。
- 缺对象、签名不一致、未知 owner 或账本漂移：停止，另开修复任务。

## 6. 检查点 B：角色与环境准备

由管理员创建或更新角色：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
EVERYDAYAI_CONFIG_IMPORT_READER_PASSWORD='<独立强密码>' \
EVERYDAYAI_MIGRATOR_PASSWORD='<独立强密码>' \
EVERYDAYAI_RUNTIME_PASSWORD='<独立强密码>' \
EVERYDAYAI_WECOM_RUNTIME_PASSWORD='<独立强密码>' \
EVERYDAYAI_WORKER_PASSWORD='<独立强密码>' \
bash deploy/setup-tenant-db-roles.sh
```

执行命令时不得使用 shell trace。

从 `deploy/env-templates/` 安装：

- `.env.runtime`
- `.env.wecom-runtime`
- `.env.worker`
- `.env.worker-client`
- `.env.migrator`
- `.env.sync`

此时只准备文件，不修改 Systemd `EnvironmentFile`，不重启服务。

## 7. 检查点 C：第一批所有权

### 7.1 转移所有权

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/transfer-agent-runtime-ownership.sh
```

该脚本原子转移：

- `schema_migration_ledger`
- 第一批 13 张 Agent Runtime 表
- 三个资产登记相关函数

脚本会临时把旧角色加入 `everydayai_owner`，保证旧服务在 RLS 中间态继续工作。

所有权完成后不运行 Migration Runner。继续完成第二批所有权，旧服务继续使用临时
owner 兼容权限。

停止条件：

- 所有权脚本失败。
- 第一批 owner、旧服务兼容 ACL 或健康检查不符合预期。

## 8. 检查点 D：第二批所有权与迁移 150–164

### 8.1 转移第二批对象

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/transfer-runtime-message-ownership.sh
```

该脚本原子接管第二批 18 张表、相关列 sequence 和固定业务函数，同时保留旧角色兼容
权限。必须在迁移 152 前执行。

再次执行检查点 A 的 preflight，输出阶段必须为 `owners_ready`；其他阶段或非零退出
都停止迁移。

### 8.2 建立旧配置导出单表 ACL

该 ACL 只能由独立数据库管理员建立，不能由 migrator 越权授予：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
bash deploy/grant-legacy-config-export-access.sh
```

脚本只授予 `everydayai_owner` 对 `kuaimai_external_credentials` 的 `SELECT`，
不转移旧表 owner，也不授予 Reader 直表权限。

### 8.3 一次性应用 150–164

Migration Runner 计划必须按完整文件名保持：

```text
150_agent_runtime_tenant_defense.sql
151_agent_runtime_role_grants.sql
152_wecom_runtime_capability.sql
153_runtime_message_rls_and_auth.sql
154_wecom_message_rpc_facades.sql
155_web_wecom_oauth_capabilities.sql
156_governance_authority_foundation.sql
157_governance_write_capabilities.sql
158_configuration_control_plane_foundation.sql
159_configuration_management_core.sql
159_configuration_management_facades.sql
160_configuration_resolution_core.sql
160_configuration_resolution_facades.sql
161_configuration_legacy_import.sql
162_configuration_legacy_export_access.sql
163_conversation_actor_worker_discovery.sql
164_actor_task_execution_capabilities.sql
```

同编号迁移按完整文件名字典序执行；不得把 core/facades 合并或手工拆开。
确认计划无其他意外 pending 后执行：

```bash
cd /var/www/everydayai/backend
python scripts/migration_runner.py plan --applied-by tenant-cutover-150-164
python scripts/migration_runner.py apply --applied-by tenant-cutover-150-164
python scripts/migration_runner.py check --applied-by tenant-cutover-150-164
```

Migration Runner 任一文件失败会停止；不得跳过失败记录继续执行。

迁移完成后再次执行检查点 A 的 preflight，输出阶段必须为 `migrations_applied`，
然后才能进入旧配置导入。

验证范围：

- Web、WeCom、Worker 角色没有 owner 成员关系或 BYPASSRLS。
- Web 认证、WeCom 入站/消息/OAuth、治理能力只对指定角色开放。
- 企业、员工、个人、散客 Scope 均失败关闭。
- 治理审计表和配置敏感表均启用 FORCE RLS。
- Registry 固定为 15 个配置定义。
- Bundle Registry 固定为 11 个 Bundle。
- 新配置事实表在 161 前仍为空。

## 9. 检查点 E：旧配置导入

确认 `161_configuration_legacy_import.sql` 与
`162_configuration_legacy_export_access.sql` 已由上一检查点应用后，严格执行：

[RUNBOOK 161：旧配置原子迁移](RUNBOOK_161_旧配置迁移.md)

必须使用同一个 `IMPORT_ID` 完成 dry-run 和 apply，并由执行人与复核人双人确认。
导入成功只代表新控制面已有副本；旧消费者仍读取旧配置。

禁止：

- 导入后删除旧配置。
- 在同一窗口切换配置消费者。
- 因单条旧数据不合法而放宽迁移校验。
- 在日志或变更单记录普通配置值、Secret envelope 或旧密钥。

## 10. 检查点 F：分服务角色切换

服务切换必须逐个进行，每次只切一个能力域：

1. Conversation Actor/后台 Worker → `everydayai_worker`
2. WeCom 入站 → `everydayai_wecom_runtime`
3. Backend/Web API → `everydayai_runtime`
4. Sync 保持经过审计的 `.env.sync`

每个服务切换后必须验证：

- 服务启动和健康端点。
- Web 登录、刷新、登出。
- 企业员工与散客的聊天和 Workspace 隔离。
- WebSocket 订阅只能恢复当前用户/企业任务。
- Actor 入队、claim、续租、完成和取消。
- 企微身份解析、私聊、群聊、附件和 Outbox。
- 企业治理、个人配置、企业共享配置和平台配置权限。
- 跨企业、停用成员、空 Scope 和伪造 Scope 均被拒绝。

Conversation Actor 必须作为第一个独立切换单元。切换前确认迁移 163–164 的 10 个 Worker
Facade 均由 `everydayai_owner` 持有、启用 `SECURITY DEFINER`，且仅
`everydayai_worker` 可执行；该角色不得拥有 `tasks` 的 SELECT/INSERT/UPDATE/DELETE。
切换后至少完成一次真实 Actor task 的发现、claim、受控任务读取、续租和原子终态，并确认
日志中没有 `InsufficientPrivilege`、Scope mismatch 或 ownership lost 异常增长。失败时只
恢复 Actor unit 与旧数据库 URL，Backend、WeCom 和 Sync 不得跟随切换。

任一服务失败，先把该服务恢复到旧 URL，再评估数据库 rollback；不得同时继续切换
其他服务。

## 11. 检查点 G：最终权限收口

只有同时满足以下条件才能最终收口：

- 150–164 全部在账本中为 `applied`。
- 第一批、第二批和配置/治理对象 owner 均为 `everydayai_owner`。
- 所有业务服务已使用独立角色。
- 旧角色活动连接为零。
- 迁移后业务验证全部通过。
- 161 导入计数与审计计数一致。

执行：

```bash
ALLOW_TENANT_DB_ROLE_FINALIZE=true \
TENANT_SERVICES_USE_ISOLATED_ROLES=true \
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/finalize-tenant-db-role-cutover.sh
```

该操作只撤销旧角色的临时 `everydayai_owner` 成员关系，不删除数据。

## 12. 回滚边界

| 所处阶段 | 首选恢复方式 |
|---|---|
| 角色创建后、owner 转移前 | 不切服务即可停止 |
| 第一批 owner 转移后、150 前 | 使用第一批 owner rollback |
| 第二批 owner 转移后、152 前 | 使用第二批 owner rollback |
| 150–164 应用后、服务未切换 | 旧角色兼容权限继续承载，先停止推进 |
| 162 rollback | 先执行 `rollback-legacy-config-export-access.sh`，再执行 SQL rollback |
| 单个服务切换失败 | 只恢复该服务旧 URL |
| 161 导入成功后 | 不执行 161 rollback，不删除已导入事实 |
| 最终 owner 收口后 | 需要新变更窗口，不做现场临时扩权 |

所有权 rollback 脚本要求显式危险开关，并会在目标表仍 FORCE RLS 时失败关闭。
执行 rollback 前必须先按对应迁移逆序恢复 RLS 状态。

## 13. 完成标准

只有以下证据齐全才能宣布生产租户架构切换完成：

- 迁移账本 150–164 完整且 checksum 一致。
- 所有目标对象 owner 与 ACL 符合角色矩阵。
- FORCE RLS 表和 policy 与迁移合同一致。
- 服务只使用其指定数据库身份。
- 旧角色连接归零且临时 owner 成员关系已撤销。
- 企业间、企业员工间、个人与散客隔离测试通过。
- WebSocket、Actor、WeCom 和 Workspace 租户链路通过。
- 161 导入审计和配置事实数量一致。
- 旧配置仍保留，配置消费者切换作为独立后续任务。
