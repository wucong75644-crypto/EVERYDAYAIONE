# Agent Runtime AR-12：Action持久化与Tool终态

> 基线：`e9d16747`
> migration：`218`
> 范围：Action、ActionAttempt、ActionResult以及Tool Calls唯一原子终态。

## 1. 事务Owner

`complete_model_attempt_step_and_create_actions`是Tool Calls terminal唯一Owner。
它在同一PostgreSQL事务中完成ModelAttempt、ModelStep、AR-11积分结算、
完整Action批次、blocker、Run waiting、RunAttempt闭合、RuntimeEvent和Projection
Outbox。不得先调用AR-11非Tool terminal，也不得在RPC返回后调用
`set_agent_run_waiting`。

锁序固定为：

```text
Session → Run → ModelStep → ModelAttempt
→ Actions（稳定ID排序）→ ActionAttempts → settlement
```

有blocking Action时，Run从`running`变为`waiting_actions`并释放execution token和
lease；当前RunAttempt闭合。零blocking批次保持Run claim，使同一Owner可以继续创建
下一ModelStep。

## 2. 批次身份

数据库对Action ID、index、call identity、tool name、arguments hash、wave、
dependencies、blocking、Policy、retry disposition和Scope构造稳定排序的canonical
JSON，并重新计算`batch_hash`。调用方hash只用于端到端校验，不能成为数据库事实来源。
重放同时比较hash和持久批次；任一字段冲突均零mutation。

## 3. Action恢复

状态转移完全沿用AR-05 `domain/transitions.py`。`accepted → unknown`以及
`claimed → failed/cancelled`均保留。accepted/unknown不能普通claim，只能使用独立
reconciliation token和lease。completed与failed均必须形成唯一规范ActionResult；
Tool错误不会直接fail Run。

blocking Action首次形成可供模型消费的completed/failed Result时递减blocker。
最后一个blocker把Run从`waiting_actions`原子唤醒为`queued`，并只追加一次
`run.resumed`。

## 4. 取消

AR-12不提供独立`cancel_agent_action`。218替换`cancel_agent_run`，在一个事务内
取消全部活动Action和Attempt、清零blocker、关闭RunAttempt并终结Run。rollback
恢复217的原函数，避免遗留依赖死锁或永久`waiting_actions`。

## 5. 权限与回滚

三张表均启用并强制RLS，只有`everydayai_owner` policy。Runtime、WeCom、Worker、
Sync、legacy和PUBLIC无表直权；Worker仅获得窄RPC EXECUTE。内部canonical、
terminal和结算helper不向运行时角色授权。

迁移词法顺序：

1. `218_01_agent_runtime_action_foundation.sql`
2. `218_02_agent_runtime_action_tool_terminal.sql`
3. `218_03_agent_runtime_action_lifecycle.sql`
4. `218_04_agent_runtime_action_reconciliation.sql`

rollback严格执行04→03→02→01。存在Action事实时foundation rollback失败关闭。
