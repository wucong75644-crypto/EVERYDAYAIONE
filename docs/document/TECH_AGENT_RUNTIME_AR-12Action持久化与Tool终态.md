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
→ Actions（稳定ID排序）→ ActionAttempts（稳定ID排序）
→ settlement → Result/Event/Outbox
```

有blocking Action时，Run从`running`变为`waiting_actions`并释放execution token和
lease；当前RunAttempt闭合。零blocking批次保持Run claim，使同一Owner可以继续创建
下一ModelStep。

## 2. 批次身份

数据库使用PostgreSQL内建`sha256(bytea)`生成64位小写十六进制hash。对Action ID、
index、call identity、tool name、arguments hash、request hash、wave、dependencies、
blocking、Policy、retry disposition和Scope构造稳定排序的canonical JSON，并重新计算
`batch_hash`。调用方hash只用于端到端校验，不能成为数据库事实来源。重放同时比较hash
和持久批次；任一字段冲突均零mutation。

单Action `request_hash`由数据库根据Session、Run、ModelStep、Action ID/index、
stable/provider call identity、规范tool name、arguments hash、wave/dependencies、
blocking、Policy decision/snapshot/revision、retry disposition以及org/user Scope重算。

## 3. Action恢复

状态转移完全沿用AR-05 `domain/transitions.py`。`accepted → unknown`以及
`claimed → failed/cancelled`均保留。accepted/unknown不能普通claim，只能使用独立
reconciliation token和lease。completed与failed均必须形成唯一规范ActionResult；
Tool错误不会直接fail Run。

普通claim必须携带稳定`claim_request_id`。数据库持久化claim batch事实以及Attempt上的
request ID；相同Worker/request重放返回完全相同的Attempt、token和lease，不领取第二批。
跨Worker复用或相同request改变batch参数均冲突。按Worker/request的readback在cancel后
返回闭合事实，不续租。

blocking Action首次形成可供模型消费的completed/failed Result时递减blocker。
最后一个blocker把Run从`waiting_actions`原子唤醒为`queued`，并只追加一次
`run.resumed`。

## 4. 取消

AR-12不提供独立`cancel_agent_action`。218替换`cancel_agent_run`，在一个事务内
取消全部活动Action和Attempt、清零blocker、关闭RunAttempt并终结Run。rollback
恢复217的原函数，避免遗留依赖死锁或永久`waiting_actions`。

可执行Action不得依赖`policy_decision=rejected`的Action；该输入在Tool terminal
结算前整批失败关闭。`requires_authorization`依赖合法但不会ready，本期不提供授权或
暗中批准入口，Run cancel仍能完整收敛。

## 5. 权限与回滚

四张表均启用并强制RLS，只有`everydayai_owner` policy。Runtime、WeCom、Worker、
Sync、legacy和PUBLIC无表直权；Worker仅获得窄RPC EXECUTE。内部canonical、
terminal和结算helper不向运行时角色授权。

迁移词法顺序：

1. `218_01_agent_runtime_action_foundation.sql`
2. `218_01a_agent_runtime_action_terminal_helpers.sql`
3. `218_02_agent_runtime_action_tool_terminal.sql`
4. `218_02a_agent_runtime_action_result_helpers.sql`
5. `218_03_agent_runtime_action_lifecycle.sql`
6. `218_04_agent_runtime_action_reconciliation.sql`

rollback严格执行04→03→02a→02→01a→01。存在Action或claim batch事实时foundation
rollback失败关闭。
