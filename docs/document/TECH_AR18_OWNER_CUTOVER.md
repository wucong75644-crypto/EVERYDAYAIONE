# AR-18 Runtime Owner 切换设计

状态：仅设计，未执行生产切换。适用基线：AR-17 T1/T5 前置完成、production flags 默认关闭。

## 1. 现状与目标

当前 Conversation Actor 负责会话任务的 claim、模型生成和终端投影交付；旧 Scheduler 通过 `ScheduledWorkerStore`、`ScheduledTaskScanner` 和 `ScheduledTaskExecutor` 独立 claim、执行和恢复定时任务；旧 ToolLoop 仍作为兼容链存在。Runtime `ActionLoopDriver` 已经拥有 Action/Attempt 的 dispatch、authorization gate、lease/fencing、completion、reconcile 和 cancel 应用入口。

AR-18 的目标不是删除所有旧代码，而是在维护窗口内把 Runtime action 的外部副作用、完成、取消和 reconcile 归 Runtime ActionLoop 唯一负责。Conversation Actor 继续负责 ingress/session serialization，Projection 继续负责投影，Callback inbox 继续负责经签名验证后的 callback fact ingress；这些组件不得直接终结 Runtime action。

## 2. Owner 矩阵

| 能力 | 切换后唯一事实/副作用 Owner | 允许的其他职责 | 禁止 |
| --- | --- | --- | --- |
| Web/WeCom ingress | Runtime ingress RPC / Runtime command claim | Actor 排队、会话顺序化 | 直接调用旧 ToolLoop 产生 Runtime 副作用 |
| Model/Action dispatch | Runtime `ActionLoopDriver` | Runtime worker heartbeat、lease renewal | Worker 直接访问业务表或 provider |
| ERP/Media provider | Runtime executor + Provider Submission Facts | isolated mock/non-prod adapter | legacy dispatcher、旧 completion handler |
| accepted/unknown | Runtime reconciliation claim/loop | Callback inbox 写入 redacted fact | 普通 retry 或重新 submit |
| cancel/completion | Runtime ActionLoop/facts owner | provider cancel/readback | legacy handler 直接写 terminal state |
| Projection | `CompatibilityProjectionWorker` / projection repository | 兼容读模型更新、dead item recovery | 修改 Runtime aggregate 或 action facts |
| Callback | `CallbackInbox` + callback facts ingress | 签名校验、敏感字段脱敏 | callback 直接完成 action |
| Scheduler | Runtime-owned Scheduler CAS bridge（新 Runtime task） | 旧任务 drain/read-only 兼容 | 旧 `ScheduledTaskScanner` claim Runtime task |
| Child Run | Runtime `ChildRunService` + facts repository | parent ActionLoop 发起生命周期命令 | 父 Run 权限扩大、旧 owner 直接完成 child |

## 3. 维护窗口与切换顺序

### 3.1 前置检查

维护窗口开始时冻结 Runtime catalog/provider revision、生产 flags 和 capability 配置。确认 Runtime、Projection、Authorization、Sandbox Worker heartbeat；确认没有 `UNKNOWN`/`ACCEPTED` 项被错误放入普通 dispatch 队列，确认 callback inbox 和 reconcile worker 可读。若任一检查失败，保持旧 owner，不开始切换。

### 3.2 Drain 顺序

1. 停止新的 legacy scheduled-task claim；已领取的旧任务只允许完成 SAFE/只读路径，禁止为 Runtime action 产生外部副作用。
2. 停止新的 legacy ToolLoop 非 SAFE/code-execute 执行入口；已经存在的 SAFE 兼容任务按旧 lease 完成或失败关闭。
3. 停止新的旧 Chat/WeCom action side-effect enqueue；保留 ingress 返回可重试的关闭结果，不丢弃已持久化 Runtime command。
4. 等待 Actor generation claim、旧 scheduler lease 和旧 completion handler 的 in-flight 数归零；超时项转为 dead/recovery 记录，不直接重派。
5. 对已有 accepted/unknown、callback 和 reconcile 项执行 readback/reconcile drain，不执行普通重派。

### 3.3 开启顺序

1. 开启 Runtime DB RPC 和 Runtime worker claim gate，确认实际 worker role、`app.access_kind`、RLS/ACL 和 fixed `search_path`。
2. 开启 Runtime ingress command claim；每个 command 先绑定 tenant/run/action/attempt、request hash 和 catalog/provider revision。
3. 开启 Runtime ActionLoop dispatch；authorization gate、PolicyReceipt、Dispatch Intent 和 execution token 必须先成功。
4. 开启 Runtime reconcile/callback consumer；callback 只入事实，readback/reconcile 才能改变 action 状态。
5. 最后开放 Runtime-owned Scheduler CAS 的新任务 claim。旧 scheduled task 仍保持 drain/compatibility 状态，直到独立迁移完成。

每一步都以 heartbeat、claim 数、fencing conflict、terminal/unknown/reconcile 计数和无越权日志为观察条件；任何一项异常立即停止后续开启。

## 4. 状态、恢复与回滚

- `SUBMISSION_PENDING`、`SUBMITTED`、`READBACK_CONFIRMED`、`ACCEPTED`、`UNKNOWN`、`RECONCILE_REQUIRED` 的状态变更只能由 Runtime facts + revision/state-version fencing 完成。
- submit 超时、连接断开或响应不确定进入 `UNKNOWN`；`UNKNOWN`/`ACCEPTED` 只允许 reconcile/readback/cancel owner 处理。
- cancel 先写 cancel intent，再由 Runtime ActionLoop 持有当前 execution/reconciliation token 执行 provider cancel；不允许 legacy handler 直接写 `CANCELLED`。
- Worker crash 后由 recovery claim 取得新的受控 lease；旧 token 不能提交 completion、cancel 或事实更新。
- 回滚只关闭 Runtime ingress/claim gate，并恢复尚未产生外部副作用的兼容入口；已经 `ACCEPTED`/`UNKNOWN` 的项继续由 Runtime reconcile owner 处理，绝不交回普通 retry。
- 若出现租户、request hash、provider revision、execution token、state version 或 capability 不匹配，立即 failure-closed，保留事实供审计，不自动补偿。

## 5. 兼容路径与删除边界

暂时保留：Conversation Actor 的 session/generation lease、WebSocket terminal delivery、Projection compatibility worker、Callback inbox ingress、旧 Scheduler 的只读任务展示和未迁移任务 drain。旧 ToolLoop 只保留 SAFE/只读兼容行为，并继续拒绝非 SAFE owner 责任。

切换观察期稳定后，才可删除或撤销：旧 Runtime action enqueue、旧非 SAFE ToolLoop dispatch、旧 completion handler 的 Runtime 分支、旧 Scheduler 对 Runtime task 的 claim 权限，以及不再有调用方的宽权限 RPC。删除前必须有调用方扫描、事实 readback、dead recovery 和 rollback rehearsal 证据；本设计阶段不删除代码或权限。

## 6. 数据库与权限合同

本设计不新增 migration；只有发现真实数据库 contract 变化时，才另开 additive migration lane。现有 227 lane 的身份和顺序保持不变。

Runtime worker 只获得窄 `SECURITY DEFINER` RPC 的 `EXECUTE` 权限，函数固定 `search_path=pg_catalog,public`，入口显式校验 tenant/run/action/attempt、access kind、revision、request hash 和 fencing token。Runtime facts 表使用 RLS/FORCE RLS；worker 对业务表和 facts 表均无直接 SELECT/INSERT/UPDATE/DELETE 权限。Projection、Callback、Scheduler CAS 使用各自 port/RPC，不共享宽权限连接。任何 legacy role 不得同时拥有 Runtime owner RPC，避免双 Owner。

## 7. 前置条件与验证

AR-18 生产切换前必须具备真实 staging：受控 PostgreSQL、credential backend、tenant-scoped object store、isolated Provider、Runtime/Projection/Authorization/Sandbox Worker，以及可观测 heartbeat/readiness/dead-recovery 入口。本地 profile 只能证明合同和恢复模拟，不能证明 production/staging ready。

必须先完成 disposable dry-run：old owner drain、Runtime claim、duplicate side-effect prevention、accepted/unknown reconcile、cancel/completion fencing、Runtime gate rollback。随后在 staging 重复 migration apply/readback/rollback、RLS/ACL、并发 CAS、crash/restart/drain 和 Web/WeCom ingress 验收。生产维护窗口、真实凭证、Provider 和 owner 切换需另行明确授权。
