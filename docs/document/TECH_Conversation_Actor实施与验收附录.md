# Conversation Actor 实施与验收附录

> 主设计：`TECH_Conversation_Actor持久执行架构.md`
> 状态：实施中（数据库队列、执行权、原子终态、纯生成执行器与 Web 投递基础设施已完成，业务入口尚未切换）

## 1. 可观测性

指标：

- pending serial 数量与最老等待时间。
- claim 延迟、执行时长、续约失败数。
- lease expired/reclaim 和 fencing rejection 数。
- commit/fail/cancel 次数和延迟。
- Redis 唤醒丢失后 DB 扫描认领数。
- 企业微信投递失败与重试次数。
- conversation 维度的 serial/branch 活跃数。

告警：

- 最老 pending 超过 2 分钟。
- running 租约连续过期。
- fencing rejection 持续增加。
- commit RPC 失败或积分事务不一致。
- 企业微信终态已提交但投递长期未成功。

## 2. 部署与回滚

### 2.1 部署

1. 部署兼容数据库字段和 RPC，不启用 Actor。
2. 部署 Worker 和协调器，feature flag 关闭。
3. 确认旧链路 Chat `running` 任务排空，再允许 Actor 认领，避免双执行模型重叠。
4. 影子扫描 pending 数据，不认领，验证指标。
5. 灰度 Web 新 conversation。
6. 灰度全部 Web Chat。
7. 接入企业微信。
8. 启用 branch。
9. 稳定观察后删除旧直接执行路径。

### 2.2 回滚

- feature flag 关闭后停止新 Actor 认领。
- 等待或安全中断 running Actor tasks。
- 应用回退到上一版本；新字段保持不删除。
- 只有确认无 pending/running Actor task 后才执行 rollback SQL。
- 已生成的新 revision 数据不回滚。

## 3. 实施任务

1. [x] 数据库队列字段、状态约束、索引与 enqueue/claim/renew RPC。
2. [x] 原子 commit/fail/cancel RPC 与积分等价契约测试。
3. [x] ConversationExecutionService、数据库扫描 Worker、Redis 唤醒与假执行器测试。
4. [x] ChatHandler Web 流式内核按职责拆分并保持行为等价。
5. [x] 通道无关生成内核、企微兼容入口和 ChatGenerationExecutor。
6. [x] Web fencing 进度、终态投递、槽位释放与默认关闭开关。
7. [x] Web enqueue、独立 Worker、恢复隔离、排队状态与取消。
8. 企业微信持久投递。
9. branch、摘要版本化、崩溃恢复和灰度开关。
10. 全量回归、压力测试、生产演练和旧路径删除。

每项独立迁移、测试、评审和确认，禁止跨项一次性切换。

### 3.1 阶段 5.2 开关与启停顺序

- `CONVERSATION_ACTOR_WORKER_ENABLED` 只控制独立 Worker 进程是否允许启动。
- `CONVERSATION_ACTOR_WEB_ENABLED` 只控制新的 Web Chat 是否进入 Actor 队列。
- 上线先启 Worker、保持 Web enqueue 关闭；健康检查通过后再灰度 Web enqueue。
- 回滚先关闭 Web enqueue，等待 Actor pending/running 排空，再停止 Worker。
- Worker 和数据库 claim 都要求 `delivery_context.actor=true`，不会认领旧 Chat。
- 取消经 `cancel_generation_turn` 原子终态使 token 失效；Worker 最迟在 5 秒续租周期内中止本地执行。

## 4. 测试矩阵

### 4.1 数据库

- 50 个并发 enqueue 保持稳定顺序。
- 同 conversation 只有一个 serial claim 成功。
- 不同 conversation 可并行 claim。
- branch 不占 serial owner。
- token 过期重认领后旧 token commit 必须失败。
- commit 重放不重复扣积分或推进 revision。
- cancel 与 commit 竞态只有一个终态。
- org/user/task/message 范围不匹配全部拒绝。

### 4.2 后端

- Redis 通知成功、失败和重复。
- Worker crash/重启/续约失败。
- Provider retry 复用 ContextSnapshot。
- pending/running 取消和 accumulated 内容恢复。
- DB commit 成功但 WebSocket/企微失败。
- task slot 所有终态均释放。

### 4.3 前端

- queued、running、streaming、completed 状态转换。
- 刷新恢复多个 pending Chat。
- 取消排队任务。
- WebSocket 断线后以 HTTP 状态恢复。

### 4.4 企业微信

- 同会话连续消息串行。
- 服务重启后最终主动投递。
- 投递重试不重复发送。
- Web 与企微对同一 conversation 的混合顺序。

## 5. 阶段验收门禁

每阶段必须提供：

- migration 正向/回滚静态与真实数据库验证。
- 新增核心模块覆盖率不低于 80%。
- 调用方和旧路径残留搜索。
- 文件、函数、复杂度和嵌套阈值。
- 后端全量回归；涉及前端时执行 Vitest、TypeScript 和生产构建。
- Redis 不可用、DB 瞬断、取消、重复请求和服务重启验证。

## 6. 设计自检

- [x] 架构现状、复用模块、约束和冲突完整。
- [x] 普通 serial、显式 branch 和媒体任务边界明确。
- [x] DB/Redis/Worker/Handler 职责明确。
- [x] 并发、超时、失败、取消、恢复均有策略。
- [x] 数据库、API、前端、企业微信连锁修改已纳入。
- [x] 部署和回滚顺序明确。
- [x] 不新增第三方依赖。
- [x] 新模块均按低于 500 行设计。
