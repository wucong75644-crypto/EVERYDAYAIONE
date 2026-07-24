# 数据库租户纵深防御技术设计

> 状态：方案 A 已确认，待实施
> 日期：2026-07-23
> 范围：第一实施组——Registry、事务级 DB Context、Agent Runtime 租户表和数据库角色边界

## 1. 项目上下文

- **架构现状**：Web、Conversation Actor、企微和后台 Worker 共用本地 PostgreSQL。
  HTTP 入口用 `OrgScopedDB` 给已登记表追加 `org_id`，RPC 默认注入 `p_org_id`；
  QueryBuilder/RpcCaller 每次执行独立从连接池取连接并使用 autocommit。
- **可复用模块**：`OrgContext` 已完成用户与企业成员校验；`OrgScopedDB` 已有应用层过滤；
  Actor task、Conversation、Memory、Artifact 和 Asset 已持久化 `org_id/user_id` 或可追溯父事实。
- **设计约束**：个人空间固定为 `org_id IS NULL + user_id`，不能把全部 NULL scope 视为同一租户；
  Worker 必须先 claim 再进入精确租户事务；管理员跨租户访问必须使用受控入口。
- **潜在冲突**：生产 80 张含 `org_id` 的表均由应用角色拥有，0 个 policy、0 个 FORCE RLS；
  代码 Registry 仅登记 55 张，另有 26 张未分类；现有 7 张 ENABLE RLS 因 owner 绕过而无效。

## 2. 生产审计证据

| 项目 | 结果 |
|---|---:|
| 应用角色 | `everydayai`，非 superuser、无 BYPASSRLS |
| 含 `org_id` 的表 | 80 |
| 表 owner 为应用角色 | 80 |
| ENABLE RLS | 7 |
| FORCE RLS | 0 |
| policy | 0 |
| SECURITY DEFINER | 10 |
| 业务函数默认/PUBLIC EXECUTE | 需逐个收紧 |

明确未进入现有 `TENANT_TABLES` 的运行时事实包括：

- `conversation_artifacts`
- `conversation_attachment_refs`
- `conversation_channel_bindings`
- `conversation_compactions`
- `conversation_context_items`
- `conversation_context_receipts`
- `conversation_data_evidence`
- `message_generation_requests`
- `task_attachment_refs`
- `memory_atoms`
- `user_assets`
- `user_asset_refs`
- `user_activity_events`

`org_members/org_configs/org_invitations` 属于企业控制平面，不得简单套用普通事实表策略。
`tool_audit_log_*` 是父表分区，不单独进入应用 Registry。

## 3. 设计目标与非目标

目标：

1. 漏写应用层 `org_id` 条件时由 PostgreSQL 拒绝跨租户读写。
2. 个人空间同时按 `user_id` 隔离。
3. Web、Actor、企微和 Worker 在同一数据库 Context 合同下执行。
4. 管理员、全局扫描和迁移不通过普通 runtime 角色绕过。
5. 每一组策略可独立验证和撤销，不删除业务事实。

非目标：

- 本阶段不一次性对 80 张表启用 FORCE RLS。
- 不改变公开 API、前端状态或业务响应。
- 不把企业控制平面、ERP 全局扫描与普通 Session policy 混为一类。
- 不使用流量灰度；按数据库领域逐组上线并做真实隔离测试。

## 4. 租户 Registry

新增唯一 Registry，替代手写 `TENANT_TABLES`：

| 类别 | 含义 | Context |
|---|---|---|
| `USER_OR_ORG_FACT` | 个人或企业事实 | org 非空时校验成员；org 空时校验 user |
| `ORG_CONTROL` | 企业成员、配置、邀请 | 显式 actor + 企业权限 |
| `ORG_WORKER_FACT` | ERP、同步和定时任务 | worker 角色 + 精确 org |
| `SYSTEM_FACT` | 系统事实 | 普通 runtime 禁止直接访问 |
| `PARTITION_CHILD` | 父表继承策略 | 不允许独立注册 |

Registry 每项保存：表名、类别、直接 user 列或父表关联、是否允许个人 scope、允许角色。
启动时对 `pg_catalog` 做双向校验：

- 新增含 `org_id` 表未登记：启动失败。
- Registry 表不存在或缺少 `org_id`：启动失败。
- 分区子表被误登记：启动失败。

`OrgScopedDB` 继续保留，改为消费 Registry，作为第一道快速过滤；RLS 是第二道边界。

## 5. 数据库角色

| 角色 | LOGIN | 用途 |
|---|---|---|
| `everydayai_owner` | 否 | 第一实施组对象 owner |
| `everydayai_migrator` | 是 | 部署迁移，受控持有 owner 权限 |
| `everydayai_runtime` | 是 | Web API 普通请求 |
| `everydayai_wecom_runtime` | 是 | 企微入站消息面；属于 runtime Scope，但拥有独立能力集 |
| `everydayai_worker` | 是 | Actor、企微控制面和后台 claim/执行 |

生产现有 `everydayai` 不能继续同时承担 owner、runtime 和 migrator。
角色创建与首次所有权转移必须由 PostgreSQL 管理员执行独立运维脚本；普通 migration runner
没有 CREATEROLE/对象转移权限。

部署迁移改读独立 `MIGRATION_DATABASE_URL`，服务环境只保存对应
runtime/wecom-runtime/worker URL。三个服务角色均不加入 owner 角色，不能
`SET ROLE everydayai_owner`。

## 6. 事务级 DatabaseScope

```text
DatabaseScope
├── actor_user_id: UUID | None
├── org_id: UUID | None
├── access_kind: runtime | worker
├── request_id: str
└── require_user: bool
```

每次 Query/RPC 必须在同一个连接和事务内执行：

```sql
SELECT set_config('app.actor_user_id', :user_id, true);
SELECT set_config('app.org_id', :org_id, true);
SELECT set_config('app.access_kind', :access_kind, true);
-- 随后执行实际 SQL
```

第三个参数固定为 `true`，使值只在当前事务有效。连接归还池前自动清除，禁止 session 级 SET。
缺失或非法 UUID 解析为 NULL；需要用户身份的 policy 必须失败关闭。

由于当前 QueryBuilder/RpcCaller 独立取连接，Scope 必须成为 Builder/Caller 的不可变字段，
不能只在 FastAPI dependency 中提前设置。同步和异步执行路径都要在内部事务中先设置 Scope。

## 7. 第一组 RLS 策略

第一组覆盖 13 张 Agent Runtime 相关事实表。策略按两类生成：

### 7.1 直接身份表

表自身存在 `org_id + user_id` 时，`user_id` 在个人和企业场景都必须精确匹配当前用户；
`org_id` 只决定个人散客或企业成员上下文，不能扩大为企业内员工互相可见：

```sql
USING (
    user_id = tenant_actor_user_id()
    AND (
        (org_id IS NULL AND tenant_org_id() IS NULL)
        OR (
            org_id = tenant_org_id()
            AND tenant_actor_is_active_member(org_id)
        )
    )
)
```

`WITH CHECK` 使用同一谓词，阻止插入或更新到其他 scope。

### 7.2 父事实身份表

Context、Artifact、Attachment 等没有直接 `user_id` 的表，通过稳定外键关联
`conversations/tasks/user_assets`，同时校验：

- 子表 `org_id` 与父表 `org_id` NULL-safe 一致；
- `scope_type='user'` 的父会话始终要求 `user_id = tenant_actor_user_id()`；
- `scope_type='channel'` 的父会话要求企业一致且 actor 是 active member，允许企业 Channel 共享；
- 用户资产按 `storage_owner_key` 精确到用户，Channel 资产才允许企业成员共享。

禁止仅使用 `org_id IS NULL` 作为个人 policy。

## 8. Worker 与全局入口

- Conversation Actor 唤醒只携带 conversation ID；claim RPC 由 `everydayai_worker` 调用。
- 全局 claim/过期扫描改为最小化 SECURITY DEFINER 函数，只返回任务 ID、conversation ID、
  org_id 和 user_id，不返回业务正文。
- Worker claim 后为每个任务构建 `DatabaseScope`，后续读取和提交都在精确 scope 下执行。
- 企微入站在完成外部身份映射后建立 scope。
- 管理员跨租户读取使用独立 admin RPC；RPC 内校验 operator 身份和权限并写审计。
- 普通 runtime 角色不能设置 `access_kind=admin` 获得绕过能力。

## 9. SECURITY DEFINER 与函数授权

所有业务函数默认：

```sql
REVOKE ALL ON FUNCTION ... FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ... TO everydayai_runtime; -- 或 worker
```

SECURITY DEFINER 必须：

- 固定 `SET search_path = pg_catalog, public` 或使用全限定对象名；
- owner 为 `everydayai_owner`；
- 显式检查 actor、org、业务对象 scope；
- 只授予所需角色；
- 不接受调用方声明的“管理员模式”作为唯一授权证据。

现有 10 个 SECURITY DEFINER 按上述标准逐个审计；扩展函数不在业务授权清单中处理。

## 10. 边界场景

| 场景 | 处理策略 | 模块 |
|---|---|---|
| org/user 都为空 | 普通事实拒绝；仅受控 claim 允许 | DB Context/RLS |
| 个人表只有 org_id=NULL | 必须经自身或父表 user_id 校验 | policy |
| 员工被移除 | 下一事务成员 EXISTS 失败 | org_members |
| 连接池复用 | SET LOCAL + 事务结束自动清除 | LocalDB |
| 查询/RPC 抛错 | 事务回滚并清除 Context | LocalDB |
| Worker 并发 claim | SECURITY DEFINER + 现有锁/fencing | Actor |
| 管理员跨企业 | 独立 RPC + operator 审计 | Admin |
| 迁移失败 | 服务不重启；账本记录 failed | migration runner |
| policy 配错导致空结果 | 撤销 FORCE/policy，恢复应用过滤 | rollback |
| 大批 ERP 同步 | 后续独立 worker 策略，不进入第一组 | ERP |

## 11. 连锁修改清单

| 改动点 | 影响文件 | 同步内容 |
|---|---|---|
| Registry SSOT | `core/tenant_registry.py`, `core/org_scoped_db.py` | 替换 TENANT_TABLES |
| DatabaseScope | `core/db_scope.py` | 包装既有同步/异步 Query 和 RPC，同事务 SET LOCAL |
| HTTP Scope | `api/deps.py` | OrgContext 转 DatabaseScope |
| Actor Scope | `conversation_worker.py`, `conversation_execution.py` | claim 后按任务绑定 |
| 企微 Scope | `services/wecom/actor_enqueue.py` 等 | 绑定真实 user/org |
| 直接 pool 调用 | Agent Runtime/Memory/Artifact Repository | 改用 scoped connection |
| 迁移身份 | `deploy/run-migrations.sh`, 配置文档 | MIGRATION_DATABASE_URL |
| 角色与 RLS | 新迁移及运维脚本 | owner/runtime/worker、policy、grant |
| 静态合同 | 新测试 | Registry 与 pg_catalog/调用方防漏 |

## 12. 架构影响评估

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块边界 | DB 客户端成为统一 Scope 注入点 | 中 | Scope 不进入业务 Service |
| 数据流 | Request/Task 身份随查询进入 PG | 中 | 不可变对象、同事务设置 |
| 扩展性 | 每次事务增加 3 次 set_config | 中 | 合并为单条 SELECT，压测连接池 |
| 耦合度 | Web、Actor、企微、Worker 均受影响 | 高 | 按领域实施，不一次覆盖 80 表 |
| 一致性 | 与现有 OrgContext/OrgScopedDB 兼容 | 低 | 保留应用层过滤 |
| 可观测性 | 可统计 missing scope/RLS 拒绝 | 中 | 结构化日志与审计查询 |
| 可回滚性 | policy/角色可撤销，事实不变 | 中 | rollback 先撤 FORCE 再恢复 grant |

## 13. 文件规划

新增：

- `backend/core/tenant_registry.py`
- `backend/core/db_scope.py`
- `backend/migrations/150_agent_runtime_tenant_defense.sql`
- `backend/migrations/rollback/150_agent_runtime_tenant_defense_rollback.sql`
- `deploy/setup-tenant-db-roles.sh`
- `backend/tests/test_tenant_registry_contract.py`
- `backend/tests/test_db_scope.py`
- `backend/tests/test_agent_runtime_rls_migration.py`

修改：

- `backend/core/org_scoped_db.py`
- `backend/core/database.py`
- `backend/api/deps.py`
- Agent Runtime、Memory、Artifact 的直接连接调用方
- `backend/services/conversation_worker.py`
- `backend/services/conversation_execution.py`
- `deploy/run-migrations.sh`
- 部署与架构文档

## 14. 实施任务

1. Registry 分类、生产 schema 双向合同测试；不改变数据库行为。**已完成**：
   生产扫描 95 个 public 表/分区/物化视图，双向合同错误为 0。
2. DatabaseScope 同步/异步基础设施和连接复用/异常清理测试。**已完成**：
   新增显式 wrapper，复用原连接池；Scope、业务 SQL 和 count/RPC 固定在同一事务，
   异常自动退出事务，旧 LocalDBClient 保持零改动。
3. Web/Actor/企微调用链贯通 Scope，先保持 RLS 未强制。
   - **3.1 已完成**：HTTP 使用已验证 OrgContext；企微使用消息级服务副本，
     用户映射前后分别绑定企业 Scope 和完整用户/企业 Scope。
   - **3.2 已完成**：Worker 扫描/claim 使用无租户 Worker Scope；claim 后按任务身份
     拆分异步控制面、异步应用层和同步 Handler DB，覆盖执行、续租、提交、失败、
     进度 Sink、终态通知与 post hook。
   - **3.3a 已完成**：异步 raw SQL scoped pool 固定在事务内注入身份并管理提交/回滚，
     禁止显式结束事务及裸连接 checkout。
   - **3.3b 已完成**：迁移 Memory、Knowledge、Graph、Metrics 及 Agent 工具调用方，
     删除跨租户全局 Scheduler，并区分在线任务与启动/后台 Worker 身份。
     - **3.3b.1 已完成**：Memory 检索/写入从调用方 Scope 构造 raw SQL adapter，
       无 Scope 失败关闭；全局 Scheduler 已删除。
     - **3.3b.2 已完成**：Knowledge、Graph、Metrics、文件与 Sandbox 工具显式传递
       Scope；Seed、评分及清理任务使用显式 Worker 身份；无 Scope 连接失败关闭。
4. 创建 owner/migrator/runtime/worker 角色和独立密钥配置。
   - **4.1 已完成代码准备**：新增管理员执行的幂等角色脚本及合同测试；不连接生产、
     不转移 owner、不切换服务 URL。
   - **4.2 已完成**：迁移 Runner 与部署门禁强制使用 `MIGRATION_DATABASE_URL`，
     不回退应用 `DATABASE_URL` 或项目 Settings。
   - **4.3 已完成配置合同**：提供 runtime/worker/migrator 无凭证模板和失败关闭验证器；
     任务 5 grant/policy 与测试库隔离矩阵完成前，Systemd 保持原连接文件。
5. 迁移 Agent Runtime 13 表 owner、grant、policy，先在测试库验证。
   - **生产事实校正（2026-07-24）**：生产当前只有旧 `everydayai` 登录角色，目标
     30 表及迁移账本均由其持有；7 张表已 ENABLE RLS 但没有 policy，服务依赖 Owner
     bypass 正常运行。因此 Owner 转移前必须先给予旧角色临时 `everydayai_owner`
     成员关系；只保留 CRUD 会导致中间态拒绝访问。迁移账本也统一转给 NOLOGIN owner，
     migrator 通过成员关系执行 bootstrap 和 150–154。
   - **5.1 已完成代码准备**：管理员 owner 转移/回滚脚本覆盖精确 13 表；不启用 RLS，
     暂留旧应用角色 CRUD，runtime/worker 在 policy 生效前无目标表权限。
   - **5.2 已完成代码准备**：迁移 150 为精确 13 表实现用户私有、企业 Channel 共享、
     父事实与员工失权辅助谓词/policy；仅 ENABLE RLS，不启用 FORCE，也未应用真实数据库。
     辅助函数 EXECUTE 与表权限由 5.3 配套，150 不得单独上线。
   - **5.3a 已完成代码准备**：管理员脚本同步转移三项资产 SECURITY DEFINER 函数 owner；
     迁移 151 按 Web runtime/Worker 操作拆分首组表权限，普通角色无 DELETE、无资产表
     直权、无内部资产函数 EXECUTE。仅公开 `register_user_asset` 可调用。
   - **5.3b 待实施**：补齐 Web、企微、Actor Worker 的全部既有 RPC 与非首组表权限清单；
     完成前不得切换 Systemd 数据库角色。
     - **5.3b.1 第一子步已完成代码准备**：Backend/WeCom、Actor、Sync 分别加载 runtime、
       worker、独立 legacy 数据库角色文件；公共非数据库配置仍来自 `.env`。Web 内嵌
       BackgroundTaskWorker 的独立 worker client 属于下一子步，完成前不得部署这些 unit。
     - **5.3b.1 第二子步已完成代码准备**：Backend/Actor 额外加载 `.env.worker-client`；
       Web 后台恢复、清理、轮询、错误监控和知识 Seed 使用独立 Worker client/raw pool，
       缺少 `WORKER_DATABASE_URL` 失败关闭。全局扫描的受控 RPC 尚未完成，仍不得切换。
     - **5.3b.1 第三子步已完成代码准备**：WeCom 同时加载 runtime 与 worker-client；
       企业 bot 配置发现、Outbox claim/投递使用 Worker DB，入站消息和卡片事件使用
       runtime DB，并在数据库访问前绑定请求级 org/user Scope。Worker DB 在 WS 启动前
       初始化，缺失配置失败关闭；本阶段未部署或连接真实数据库。
     - **5.3b.2 已确认方案 A，待实施**：新增 `everydayai_wecom_runtime` 登录角色，
       `access_kind` 仍为 `runtime`；WeCom 消息面使用独立连接，Web runtime 不能执行
       企微身份能力，WeCom runtime 不能执行 Worker claim。实施按下述三个子波次完成，
       任一子波次缺失都不得切换服务。
       1. **5.3b.2a 已完成代码准备**：角色脚本新增独立密码、NOBYPASSRLS 和 owner
          成员关系拒绝；新增 `.env.wecom-runtime` 单键模板/0600 验证，连接串必须与
          runtime、worker、migrator、sync 全部不同；WeCom unit 以此文件覆盖
          `DATABASE_URL`，继续加载 `.env.worker-client`。本阶段未创建真实角色、角色文件
          或执行 systemctl；安全门面完成前不得部署该 unit。
       2. **5.3b.2b 已完成代码准备**：禁止 WeCom runtime 直读写
          `wecom_user_mappings`、`wecom_chat_targets`、`org_members`。新增 owner 持有的
          SECURITY DEFINER 业务门面，固定 `pg_catalog, public` search_path，校验
          `session_user=everydayai_wecom_runtime`、`access_kind=runtime`、
          `tenant_org_id()=p_org_id`、企业 active、corp 与企业配置匹配；原子承担用户
          映射/首次创建、最近 chat 更新、聊天目标 upsert 和必要的成员加入。内部辅助函数
          不授予任何登录角色，旧 `wecom_get_or_create_user` 撤销 PUBLIC 后仅保留 owner
          内部调用或由新门面替代。历史 `org_id=NULL` mapping/target 仅在 corp 唯一属于
          当前 active 企业时原子认领；冲突时失败关闭。迁移 150/151 保持 checksum 不变，
          角色匹配由新迁移 152 增量替换。
       3. **5.3b.2c-1 已完成代码准备**：管理员脚本原子接管本波次 18 张基础事实表、
          pg_catalog 发现的实际列序列和固定业务函数；撤销 PUBLIC/新角色权限并保留旧
          服务兼容权限。回滚要求服务先切回旧 URL、显式危险开关且目标表均未 FORCE RLS。
          **5.3b.2c-2 已完成数据库代码准备**：迁移 153 为第二批表建立 RLS/policy
          和六个 Web 认证门面；迁移 154 将既有 WeCom 消息函数收口为 owner-only
          core 与强 Scope SECURITY DEFINER 门面。Web/WeCom 均无 Worker/admin/内部
          helper 权限，旧角色只在连接切换窗口保留兼容行为。迁移尚未应用。
       本波次完成后仍不切换 Backend runtime：同一 FastAPI 进程内的企业治理、管理员、
       ERP、Scheduler 和系统监控权限尚未完成，必须等 5.3b 全域清单闭合。
   - **5.4 待实施**：全局管理员资产读取切换到审计化 SECURITY DEFINER RPC。
6. 启用 FORCE RLS，运行个人、企业 A/B、员工移除、Worker claim、管理员测试。
7. 部署生产并执行同一验收矩阵；无流量灰度。
8. 后续独立覆盖企业控制平面、ERP、审计和系统表。

每个任务完成后按项目规则独立报告并等待确认。

## 15. 部署与回滚

部署顺序：

1. 创建角色但不改变现有服务连接。
2. 部署 Scope 代码和 Registry。
3. 配置 migrator/runtime/wecom-runtime/worker 独立 URL。
4. 管理员转移首组表及资产函数 owner。
5. 连续应用 150、151 及 5.3b 权限迁移；不得在中间态切换服务连接。
6. 测试库执行真实隔离矩阵；生产 Schema-only 克隆库已通过 150–154 与 5 项角色矩阵。
7. 生产应用迁移，切换并重启对应服务，执行健康和跨租户验证。
8. 150–159、33 个对象 Owner、独立服务连接与旧连接归零全部验证后，撤销旧角色临时
   owner 成员关系；能力域未闭合时不得执行。

回滚顺序：

1. `NO FORCE ROW LEVEL SECURITY`。
2. 服务切回旧连接 URL。
3. 回滚 151 及 5.3b，撤销新角色表/function 权限。
4. 回滚 150，删除第一组 policy。
5. 执行管理员 owner 回滚，恢复旧 owner 并撤销新角色 schema USAGE。
6. 保留角色和业务事实，不 DROP 数据表。

### 15.1 任务 5.3b.2 文件与函数清单

新增文件：

- `deploy/env-templates/wecom-runtime.env.template`：独立无凭证连接合同。
- `backend/migrations/152_wecom_runtime_capability.sql`：WeCom runtime 角色匹配、
  安全门面与最小 EXECUTE；不在此文件切换服务。
- `backend/migrations/rollback/152_wecom_runtime_capability_rollback.sql`：撤销门面授权和
  新角色的 policy 参与资格，不删除业务事实。
- `deploy/transfer-runtime-message-ownership.sh`：第二批对象 owner/sequence 原子转移。
- `deploy/rollback-runtime-message-ownership.sh`：受显式危险开关和角色切回前置检查保护。
- `backend/tests/test_wecom_runtime_capability_migration.py`：静态签名、PUBLIC revoke、
  search_path、角色矩阵和无直表授权合同。
- `backend/tests/test_runtime_message_ownership_scripts.py`：第二波 owner 与回滚门禁合同。

修改文件及函数：

- `deploy/setup-tenant-db-roles.sh`：创建并收紧 `everydayai_wecom_runtime`。
- `deploy/validate-tenant-db-env.sh`：校验第五连接、0600 和全连接唯一性。
- `deploy/everydayai-wecom.service`：用 `.env.wecom-runtime` 替换 `.env.runtime`。
- `backend/migrations/152_wecom_runtime_capability.sql`：
  `tenant_database_role_matches_scope()` 增量接受 WeCom 角色作为 runtime 类别；既有
  150/151 文件不修改，Web/Worker 判断不变。
- `backend/services/wecom/user_mapping_service.py`：
  `get_or_create_user()`、`update_last_chatid()`、`upsert_chat_target()` 改为只调用安全门面，
  删除消息面的 mapping/target/member 直表操作。
- `backend/tests/test_tenant_db_roles_script.py`、
  `test_tenant_db_env_contract.py`、`test_service_database_role_files.py`：固定第五角色合同。
- `backend/tests/test_wecom_request_scope.py`、
  `test_wecom_ws_runner_main.py`、`test_wecom_message_service.py`：固定消息级 Scope、
  双客户端装配和门面调用。
- `docs/PROJECT_OVERVIEW.md`、`docs/FUNCTION_INDEX.md`、`docs/CURRENT_ISSUES.md`：
  同步角色、门面和阶段状态。

### 15.2 任务 5.3b.2 对象清单

第二批新增 owner 表固定为：`users`、`organizations`、`org_members`、`org_configs`、
`org_invitations`、
`wecom_user_mappings`、`wecom_chat_targets`、`conversations`、`messages`、`tasks`、
`credits_history`、`credit_transactions`、`image_generations`、`detail_projects`、
`detail_project_images`、`refresh_tokens`、`user_subscriptions`、`user_memory_settings`。
脚本同时接管这些表由列拥有的 sequence；首组 13 表不重复转移。
其中 `user_subscriptions` 在运行代码中存在 5 个调用点，但迁移目录没有建表来源，
属于 legacy baseline 对象；owner 脚本必须用 `to_regclass` 和 pg_catalog 校验真实表、
owner、列与 sequence，任一不一致失败关闭，禁止按仓库迁移历史猜测。

Web runtime 公开 RPC 清单固定为：
`claim_message_generation_request`、`prepare_generation`、
`attach_generation_external_task`、`fail_prepared_generation_task`、
`enqueue_generation_turn`、`bind_generation_turn`、`close_generation_turn`、
`cancel_generation_turn`、`deduct_credits_atomic`、`atomic_refund_credits`、
`partial_refund_credits`、`increment_message_count`、`record_user_activity`、
`register_user_asset`。后台清理、Actor claim/renew/commit/fail、管理员和 ERP RPC 不授予。

WeCom runtime 公开 RPC 清单固定为：新身份解析门面、新聊天目标门面、
`resolve_wecom_conversation`、`stage_wecom_attachment_v2`、
`enqueue_wecom_generation_turn_v2`、`update_wecom_conversation_setting`、
`record_user_activity`、`register_user_asset`。`wecom_get_or_create_user`、
`claim_legacy_wecom_conversation`、`current_attachment_parts`、
`bind_task_attachments`、`enqueue_wecom_task_record`、`increment_message_count` 和资产内部
helper 均只供 owner 持有的公开门面内部调用。

### 15.3 任务 5.3b.2 权限矩阵

| 能力 | Web runtime | WeCom runtime | Worker |
|---|---:|---:|---:|
| 普通用户事实 RLS | 是 | 是，仅消息链 | claim 后精确 Scope |
| WeCom 身份/目标门面 | 否 | 是 | 否 |
| mapping/target 直表访问 | 否 | 否 | 控制面按后续清单 |
| Actor enqueue（Web） | 是 | 否 | 否 |
| Actor enqueue（WeCom） | 否 | 是 | 否 |
| 全局扫描/claim | 否 | 否 | 是 |
| 管理员/跨租户资产 | 否 | 否 | 否，独立审计 RPC |
| 内部 SQL helper | 否 | 否 | 否 |

### 15.4 任务 5.3b.2 边界与验收

| 场景 | 处理 |
|---|---|
| org/user 均为空 | 身份门面拒绝；不创建用户或赠送积分 |
| org 有效、用户尚未映射 | 在同一 advisory-lock 事务创建 user/mapping/赠送积分/成员关系 |
| corp 不属于 org | 门面拒绝，不能借其他企业 bot 创建身份 |
| 企业或员工停用 | 下一事务拒绝；不依赖进程缓存 |
| 同一企微用户并发首次消息 | 唯一索引 + advisory xact lock，只创建一次 |
| chatid 跨企业重放 | org/corp/chatid 联合约束拒绝覆盖 |
| Web runtime 调 WeCom 门面 | 权限拒绝 |
| WeCom runtime 调 Worker/admin RPC | 权限拒绝 |
| PUBLIC/匿名调用业务函数 | 权限拒绝 |
| 连接池复用 | SET LOCAL 随事务清理，10,000 次 A/B 企业交替无泄漏 |
| 门面失败 | 整体事务回滚；旁路活跃日志失败不阻断主消息 |
| 中途回滚 | 先恢复旧 WeCom URL，再撤 152 和第二批 owner，不删除任何事实 |

## 16. 依赖与 API

- 不新增 Python/Node 依赖。
- 不新增或修改公开 HTTP API。
- 内部数据库错误统一映射为现有服务错误；不得向客户端暴露 policy、表名或 SQL。

## 17. 验收门禁

- Registry 与生产所有含 `org_id` 表双向一致。
- 同用户个人空间与其他用户个人空间互不可见。
- 企业 A/B 同 ID 猜测无法读取、更新、删除。
- 员工移除后下一事务立即失权。
- runtime 不能执行 worker/admin 专属 RPC。
- 连接池复用 10,000 次无 scope 泄漏。
- Actor claim、续租、提交和恢复保持 fencing 语义。
- policy 拒绝和缺失 Scope 有结构化日志。
- rollback 在测试库恢复旧行为且不丢数据。
