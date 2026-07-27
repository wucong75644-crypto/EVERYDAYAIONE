# 角色撤权后核心链路修复

> 状态：已部署，待真实 UI 联合验收
> 日期：2026-07-26

## 1. 目标

在旧 `everydayai` 不再继承 `everydayai_owner` 的终态下，恢复并验证：

- Web 新建对话和既有对话消息发送；
- Web 图片上传、资产登记和缩略图展示；
- 企业列表与待处理邀请读取；
- Conversation Actor 的企业 AI 配置解析、Memory 和 Knowledge 指标记录；
- 最终撤权后仍可重复执行的生产验收门禁。

修复不得恢复服务角色对业务表的宽权限，也不得重新授予旧角色 owner 能力。

## 2. 已验证根因

### 2.1 新建对话

`tenant_conversations_runtime` 的 `USING` 调用
`tenant_conversation_visible(id, org_id)`，后者通过 `id` 回查
`conversations`。生产回滚实验表明，同一 runtime Scope 下普通 INSERT 成功，
`INSERT ... RETURNING` 被 RLS 拒绝。新行返回阶段不能依赖对本表的再次可见性查询。

### 2.2 既有对话消息生成

生产 `prepare_generation(...)` ACL 仅包含 owner 和旧角色，没有
`everydayai_runtime`。旧 owner 继承撤销后，Web 生成入口稳定返回
`permission denied for function prepare_generation`。

### 2.3 Scoped RPC

`OrgScopedDB.rpc()` 默认向所有函数注入 `p_org_id`，但
`list_actor_organizations()` 和 `list_actor_pending_invitations()` 是依赖
DatabaseScope 的零参数能力。数据库因此无法匹配函数签名。

`ScopedRpcCaller` 与 `AsyncScopedRpcCaller` 没有复用 LocalDB RPC 对 dict/list
参数的 JSONB 适配。`register_user_asset(...)` 包含 JSONB 参数，生产上传后的资产
登记因此在客户端参数适配阶段失败。

### 2.4 Actor 企业 AI 配置

Conversation Actor 使用 Worker 角色，但旧 `OrgConfigResolver` 直接读取
`organizations` 和 `org_configs`。最终撤权后这些读取被拒绝。项目已经存在固定
AI Bundle、安全解密和 Scope 校验机制，应复用该控制面，不增加表级读取。

### 2.5 Knowledge 与 Memory

Actor 的普通聊天指标没有 task_id，会直写 `knowledge_metrics`；现有策略只允许
runtime，未覆盖携带可信用户/企业 Scope 的 Worker。

`memory_pipeline_state` 已允许 runtime/worker，并同时校验用户、企业和会话。
使用生产 Worker 角色和故障日志中的真实 Scope 进行回滚重放，所有辅助判定均为真，
写入不再报错。因此当前不修改 Memory 策略；通过撤权后真实 PostgreSQL测试防止
后续回归，只有再次稳定复现才扩大修复范围。

### 2.6 图片缩略图

原图与缩略图已成功写入 NAS 和 OSS，忽略 TLS 校验时两个对象均返回 HTTP 200。
`cdn.everydayai.com.cn` 证书在 2026-07-25 23:59:59 GMT 到期，浏览器拒绝加载。
必须续期或替换 CDN 证书，禁止前端降级 HTTP 或关闭证书校验。证书续期前，生产
已临时清空 `OSS_CDN_DOMAIN`，使新上传对象使用阿里云 OSS 官方 HTTPS 域名；旧
CDN URL 不会因此自动改写。

### 2.7 Web Chat 重复入队与治理契约

Web Chat 已通过 `prepare_generation(...)` 原子创建 Turn、输入/输出消息和 pending
task，随后 `ChatHandler.start()` 仍调用旧 `enqueue_generation_turn(...)`。Runtime
撤销该旧能力后，HTTP 因重复入队失败，但 Actor 仍可能消费 prepare 已提交的 task，
形成前端显示发送失败、稍后又出现回复的不一致。

prepare 返回权威 `context_anchor` 时，Web 链路只发布 Redis best-effort wakeup，不再
创建或入队 task；Redis 失败不改变已持久化任务的 HTTP 成功语义。旧 enqueue 函数
继续保留给 `enqueue_wecom_generation_turn(...)` 等合法数据库链路，Runtime 不得
恢复其 EXECUTE 权限。

`list_all_governed_organizations()` 与
`search_governed_user_by_phone(TEXT)` 不接收 `p_org_id`，必须由
`OrgScopedDB` 明确按无 org 参数 RPC 透传。`get_governed_actor_authority(UUID)`
返回 `TEXT`，成员任职路由必须直接按字符串解析 owner/admin 权限。

## 3. 技术方案

### 3.1 数据库迁移

新增迁移及对应 rollback：

1. 重建 `tenant_conversations_runtime`，直接以当前行字段和可信 Scope 判断可见性。
2. 向 `everydayai_runtime` 授予 `prepare_generation(...)` EXECUTE，并显式撤销
   其他非目标服务角色。
3. AI Provider Bundle 增加 generation actor 校验：允许 runtime，或携带有效
   actor/org Scope 的 Worker；向 Worker 只授予四个固定 AI Bundle。
4. `knowledge_metrics` 的 INSERT 策略覆盖 runtime/worker，统一要求
   `tenant_user_fact_visible(org_id, user_id)`；Worker 只增加 INSERT。

迁移只改变策略和函数 ACL，不改变表结构和 API 协议。rollback 恢复迁移前策略与
授权矩阵。

### 3.2 应用代码

- `core/db_scope.py`：RPC 参数中的 dict/list 使用 `Jsonb`，同步和异步共享同一
  构造函数。
- `core/org_scoped_db.py`：将两个 actor 自解析零参数治理能力加入明确的 no-org
  集合，并覆盖两个全局治理查询的真实无 org 参数签名；其他 RPC 保持原有注参行为。
- `services/handlers/chat/actor_enqueue.py`：权威 prepared anchor 只触发 Actor
  best-effort wakeup；缺少 prepared anchor 时明确失败，不回退旧原子 enqueue。
- `api/routes/org_members_assignments.py`：按治理 RPC 的 `TEXT` 返回契约解析权限。
- `services/org/config_resolver.py`：企业 AI Key 改走 `SecretBundleResolver` 固定
  Provider Bundle；调用它的 Adapter Factory 无需增加新职责，散客继续读取平台
  `.env` 默认值。
- 资产登记错误日志保留稳定业务上下文并记录数据库错误码，不记录密钥、密文或完整
  参数。

### 3.3 CDN

在 CDN/证书控制面为 `cdn.everydayai.com.cn` 部署有效证书，随后验证证书链、真实
原图和缩略图 HTTPS 200，以及浏览器上传后缩略图显示。当前直连 OSS 只是可回滚的
生产缓解；CDN 证书恢复前不重新启用 `OSS_CDN_DOMAIN`。

## 3.4 部署后事实

- 提交 `40538066536114ac0e7d8b8406cc24c66c57f187` 已完成全量部署。
- 迁移账本中 203 为 `applied:migration`，四服务和公网健康检查通过。
- runtime 生产回滚事务已验证对话 `INSERT ... RETURNING` 和
  `prepare_generation` EXECUTE。
- OSS URL 生成已验证 `cdn_enabled=false`，主机为
  `everydayai-images.oss-cn-hangzhou.aliyuncs.com`。
- Worker AI Bundle 权限已恢复，但生产配置注册表返回 `CONFIG_REGISTRY_DRIFT`；
  Adapter 当前降级平台默认 Key，企业 BYOK 不视为已验收。
- 真实 Web 图片消息在外层 `prepare_generation(...)` 内调用两个
  `SECURITY INVOKER` helper 时暴露第二层 ACL 缺口。迁移 204 仅向
  `everydayai_runtime` 授予 `_prepare_generation_messages(...)` 和
  `_prepare_generation_tasks(...)` EXECUTE，不改变函数安全模式或表权限。
- 迁移 204 后真实消息继续暴露 `tasks.queue_sequence` 默认值依赖的
  `task_queue_sequence_seq` 未授权。迁移 205 仅授予 Runtime `USAGE`；所有权切换
  脚本保留该终态能力，部署在迁移后、服务重启前以 Runtime 连接校验外层函数、两个
  helper 和精确序列，任一缺失即失败关闭。
- 迁移 205 后的生产回滚复现证明：等价直接 INSERT 可通过 RLS，但 148 的 INVOKER
  helper 在统一准备事务内仍被 `tasks` FORCE RLS 拒绝。206 不再扩张底层授权，而是
  将原实现重命名为 owner 私有函数，并提供同签名 `SECURITY DEFINER` 包装门面；
  包装门面校验真实 Runtime session、access kind、Actor、企业 Scope 与 active
  user/member 事实后才调用私有事务。Runtime 不再直接执行 helper 或使用任务序列。

## 4. 边界与失败行为

- 散客 `org_id=NULL` 必须仍可创建个人对话和写入个人指标。
- 企业用户必须是 active organization 的 active member。
- channel conversation 不允许散客访问。
- Worker 没有 actor、企业不匹配或用户失效时，AI Bundle 与指标写入失败关闭。
- JSONB 适配只处理 dict/list，UUID、时间、文本等现有参数语义不变。
- 资产登记仍是 best effort；失败不回滚已持久化文件，但日志必须可定位。
- CDN 证书未修复前，后端上传可成功，但图片展示仍标记为外部基础设施未恢复。
- 206 门面校验失败统一返回 `GENERATION_PREPARE_ROLE_SCOPE_MISMATCH`，不得降级为
  底表写入；跨用户、跨企业、失效成员和非 Runtime session 全部失败关闭。
- 207 的 `attach_generation_external_task` 与 `fail_prepared_generation_task`
  继续使用 Runtime Actor/Org Scope，并额外反查 task 的用户、企业与 active 成员
  事实；私有 owner 实现不向任何服务角色暴露。
- Provider Webhook 没有用户 Actor，必须使用 actorless Worker Scope；查询、claim、
  图片/视频结算、重试和指标能力必须在部署门禁中完整存在，不能回退为 Runtime
  连接或底表访问。
- Web prepare 重放只允许重复唤醒同一 conversation，不得改变 request_id、
  client_task_id、内部 task_id、Turn 或消息锚点。
- 个人空间 wakeup 使用 `personal` scope，企业空间使用对应 `org_id`；二者都不得
  调用旧 Runtime enqueue。
- `tenant-role-capabilities.sh` 是隔离角色集合与 Runtime 旧 enqueue 撤权的权威
  preflight；总入口在 `tenant-core.sh` 前执行它。完整角色集才查询 Runtime 权限，
  角色集为零时跳过，角色只存在一部分时失败关闭。

## 5. 计划修改位置

- `backend/migrations/203_post_owner_cutover_core_capabilities.sql`
- `backend/migrations/207_runtime_media_submission_capabilities.sql`
- `backend/migrations/rollback/203_post_owner_cutover_core_capabilities_rollback.sql`
- `backend/core/db_scope.py`
- `backend/core/org_scoped_db.py`
- `backend/services/org/config_resolver.py`
- 相关 migration、Scoped RPC、Org service、配置 Bundle、资产登记测试
- `deploy/preflight-tenant-cutover.sh`
- `docs/CURRENT_ISSUES.md`
- `docs/FUNCTION_INDEX.md`（仅在新增/签名变化时）
- `docs/PROJECT_OVERVIEW.md`

## 6. 验证

1. 迁移静态契约与 rollback 对称性。
2. Scoped RPC 同步/异步 JSONB 适配测试。
3. 零参数治理 RPC 不再注入 `p_org_id`，普通租户 RPC 继续注入。
4. AI Bundle runtime/worker 正向及越权反向测试。
5. 真实 PostgreSQL撤权后验证对话 RETURNING、生成能力、资产登记、治理 RPC、
   Worker scoped Memory 与 Knowledge 指标。
6. 前端定向测试及构建，确认无 UI 契约回归。
7. 生产部署后以真实 Web 文本、图片、企业微信和管理页面联合验收。
8. 以 Runtime Scope 恢复已创建外部任务但未 attach 的遗留记录，再由 Worker
   轮询/回调完成统一结算；核对任务终态、消息和积分事务一致。

## 7. 回滚

- 应用代码回滚到部署前提交；
- 执行 203 rollback，恢复此前策略和 ACL；
- 不恢复旧角色的 owner 继承；
- CDN 新证书异常时回切上一张仍有效证书；当前已过期证书不能作为可接受回滚目标。
