# 定时任务委托执行边界

## 目标

定时任务由后台服务触发，但其 LLM、工具和消息副作用必须等同于任务创建者
本人在所属企业或个人空间内操作。Worker 只负责跨租户扫描、领取、积分锁和
执行终态，不能因后台执行获得不受约束的业务表访问权。

## 已确认的系统约束

1. 控制面使用 `everydayai_worker`，只调用任务级 `SECURITY DEFINER` 能力。
2. 应用面使用 `everydayai_runtime`，每次查询在事务内注入
   `actor_user_id + org_id + access_kind=runtime + request_id`。
3. `OrgScopedDB` 继续作为应用层租户过滤；PostgreSQL RLS 是最终边界。
4. 同一执行的数据库门面只绑定一个 `scheduled_task.id`，禁止跨任务复用。
5. 无人值守执行默认不能调用 `manage_scheduled_task`，也不能读取虚构的
   `scheduled_<id>` 会话上下文。
6. 工具描述白名单和 `ToolExecutor` handler 白名单同时生效，防止隐藏工具
   被程序化调用。
7. `scheduled_tasks` 与 `scheduled_task_runs` 归 `everydayai_owner`，启用
   FORCE RLS；Web Runtime 只能访问当前 active 企业，Worker 保持零直表权限。

## 数据与控制流

```text
BackgroundTaskWorker
  ├─ Worker DB: scan / claim / run / credit / terminal
  └─ ScheduledTaskExecutor
       └─ Runtime scoped DB(user_id, org_id, scheduled:<task_id>)
            ├─ ScheduledTaskAgent / ToolExecutor
            └─ MessageGateway
```

Conversation Actor 使用相同的双连接原则：

- 进程入口通过 `WORKER_DATABASE_URL` 创建异步 Worker 控制连接；
- handler factory 通过 `DATABASE_URL` 创建 Runtime 应用连接；
- `ActorTaskDatabases` 在每次 claim 后构造任务级 scope。

## 失败语义

- Runtime 连接缺失或角色错误：应用查询由角色门禁/RLS拒绝，任务进入既有失败
  与退款流程，不回退到 Worker 直表访问。
- `user_id` 或 `org_id` 非法：`DatabaseScope` 构造失败，Agent 不启动。
- 被禁工具：LLM 不可见；即使直接调用 executor，也返回 unknown tool。
- 立即执行入口：先将任务切到 `running`，再用 Worker 控制连接创建 run；
  已在运行的任务返回 409。
- 消息推送失败：维持既有 best-effort 行为，不改变任务数据库终态。

## 实施文件

- `backend/services/background_task_worker.py`
- `backend/services/web_database_runtime.py`
- `backend/services/scheduler/scanner.py`
- `backend/services/scheduler/task_executor.py`
- `backend/services/agent/scheduled_task_agent.py`
- `backend/services/agent/tool_executor.py`
- `backend/api/routes/scheduled_tasks.py`
- `backend/conversation_worker_main.py`
- `deploy/everydayai-conversation-actor.service`

## Fencing token 与租约

迁移 `179_scheduled_run_fencing.sql` 在 `scheduled_task_runs` 增加
`execution_token` 和 `lease_expires_at`。创建 run 时数据库签发 token，执行器
每 30 秒续租；租约丢失时取消仍在运行的 Agent。

任务读取、积分锁定与结算、成功/失败终态、结果消息写入都通过
`_assert_scheduled_run_scope` 验证
`task_id + run_id + execution_token + running + lease`。结果消息的会话定位、
必要时创建、消息插入和预览更新在同一事务中完成，不存在先校验后写入的
时间窗口。

run 离开 `running` 时触发器清空 token 和租约，并退回该 run 遗留的 pending
积分锁。因此正常终态和卡死恢复使用相同的执行权失效语义。

## 验证

- 服务环境文件合同：Actor 同时具备 Runtime 主连接和 Worker 客户端连接。
- 数据库 scope 单测：验证角色、用户、企业和 request id。
- 工具合同单测：验证两个禁用工具在描述层和执行层均不可达。
- 调度集成测试：验证 Agent 收到 Runtime scoped DB，控制终态仍走 Worker store。
- 路由测试：验证立即执行使用双连接并保持权限检查。
- fencing 合同测试：验证所有数据库副作用依赖有效 token/租约，终态清理
  token，租约丢失取消执行。
- 真实 PostgreSQL 角色矩阵：验证跨企业不可见、停用员工不可见、Worker 直表拒绝。

## 回滚

先执行 `179_scheduled_run_fencing_rollback.sql` 恢复旧函数授权并删除新增列，
再回滚 Python 调用点。不能只回滚数据库或只把 Actor service 改回
`.env.worker`，否则分别会造成协议不匹配或工具面 Worker 直连。
