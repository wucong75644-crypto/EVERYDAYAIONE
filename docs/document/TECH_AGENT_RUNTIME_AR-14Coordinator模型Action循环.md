# AR-14 Coordinator 模型—Action 执行循环

## 1. Owner 与边界

`RuntimeLoopCoordinator` 是代码层 Run、ModelAttempt、Action 推进的唯一聚合
Owner。Command Coordinator 只把 durable Command 关联到唯一 Run；Run scanner 独立
领取 `queued` 或 lease 已过期的 `running` Run，因此 Action 最后 blocker 唤醒后以及
进程重启后都不依赖新 Command。

AR-14 不接生产 ingress/startup，不实现 Executor Registry、Policy、Authorization 或
provider/executor 的具体 reconcile。Model reconcile 和 Action resolver 均为注入边界。

## 2. 持久化合同

220 扩展波次按以下顺序应用：

1. `220_01`：独立 `agent_model_results` 表，`model_step_id` 唯一，保存 output kind、
   text/structured content、schema revision 和数据库复核的 SHA-256。
2. `220_02`：Run scanner、fenced aggregate snapshot 和 ModelAttempt execution lease。
3. `220_03`：Attempt、Step、credits settlement 与 ModelResult 单事务 final terminal。
4. `220_04`：Action typed dispatch snapshot 和 accepted/unknown reconciliation scanner。

Worker 对新表无直权；表启用并强制 RLS。Worker 只获得窄 RPC EXECUTE，Runtime、
WeCom、Sync、legacy 与 PUBLIC 不获得执行权。

## 3. 控制流

```text
claim_next_agent_run
→ get_agent_run_aggregate
→ no/latest tool_calls Step: create ModelStep
→ prepare ModelAttempt
→ mark dispatching
→ Provider I/O（事务外，Run/ModelAttempt 独立续租）
→ final: Attempt + Step + credits + ModelResult
→ tool_calls: Attempt + Step + credits + Action batch + waiting_actions
→ Action typed claim
→ Executor I/O（事务外，ActionAttempt 独立续租）
→ terminal Result / accepted / unknown
→ last blocker wakes queued Run
→ next Run claim / ModelStep
```

`waiting_actions` 仅由 Action terminal RPC 唤醒；`waiting_interaction` 和 `paused` 不被
scanner 自动领取。cancel 继续由既有同锁序 RPC 决胜。

## 4. 恢复与重试

- Run scanner 并发依赖 Session → Run 锁与 `SKIP LOCKED`，只有一个 Worker取得新
  execution token；旧 token 的 mutation 失败关闭。
- `prepared` ModelAttempt 可继续首次 dispatch；`dispatching/unknown` 只进入注入式
  reconcile，禁止再次调用 Provider。
- Provider 首响应和 ModelAttempt renew 共享版本串行化，避免 lease heartbeat 与
  response-start CAS 竞态。
- Action `accepted/unknown` 不进入普通 Action claim，只进入 reconciliation scanner。
- dispatch 异常在仍持有 lease 时记录 unknown；lease/fencing 错误直接取消本地任务，
  不写伪终态。
- Run、ModelAttempt、ActionAttempt/reconciliation lease 分别续租，不复用
  CommandClaim lease。
- Run attempts 耗尽时 scanner 原子失败 Run、关闭旧 Attempt 并追加 durable event。

## 5. 回滚

回滚顺序为 `220_04 → 220_03 → 220_02 → 220_01`。存在 ModelResult 业务事实时
foundation rollback 抛出 `AGENT_MODEL_RESULT_ROLLBACK_FACTS_PRESENT`。停止新 Owner
后才能清理测试事实或执行 destructive rollback；生产事实不得删除。

## 6. 后续边界

AR-15 只通过 `get_agent_model_result` 或聚合读取获得权威完成内容并负责 Projection。
AR-16 提供真实 ModelAttempt reconcile、Executor resolver/registry、Policy 与
Authorization。AR-14 当前没有生产 composition，所有执行依赖显式注入。
