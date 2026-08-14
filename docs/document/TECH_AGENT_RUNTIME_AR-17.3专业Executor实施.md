# Agent Runtime AR-17.3 专业 Executor 实施合同

状态：仅非生产 Catalog，生产 ingress、EffectiveToolset、rollout 和 Owner 仍关闭。

## 范围

AR-17.3 为 23 项剩余工具建立唯一 Descriptor、专业 Executor 族和窄恢复边界：Remote Read（10）、Artifact/Data Job（3）、Async Media（2）、Child Run（3）、ERP Mutation（1）、Sync（1）、Workspace Mutation（2）和 Scheduled Task（1）。`code_execute` 继续复用 AR-17.2 Sandbox Executor。

每个工具使用独立 `runtime_<family>:<tool>` Executor type，避免 Registry 重复覆盖；族级 Capability 仍按专业类别收紧。旧 ToolExecutor 不被 Runtime Executor 直接调用。

## 共享合同

Provider Receipt 只允许 `completed`、`accepted`、`unknown`、`failed`、`cancelled`。响应丢失进入 `unknown`；`accepted/unknown` 只能由 Reconciler 读取 Provider 事实，不能普通重派。Provider task ref、status locator、callback correlation、request hash 和外部幂等键均与 Attempt 绑定。

Callback 先进入签名校验、敏感字段脱敏和幂等去重 Inbox，再参与状态转换。Cost 使用独立 `agent_action_cost_settlements`，以 `(action_id, attempt_id, kind)` 和 receipt hash 保证重复结算、释放、退款和 late adjustment 不重复。

Artifact 使用内容 SHA-256、lineage link 和 materialize checkpoint；partial 与最终 materialized 状态隔离，materialize 失败只重试物化阶段。复合工具只创建 Child Run，叶子 Action 仍独立经过 PolicyReceipt、DispatchIntent、Capability 与 Cost 边界。

## 数据库 lane

`226_01`～`226_06` 是不修改 212～225 的 additive lane，分别覆盖 Provider reconcile、Callback Inbox、Action Cost Ledger、Artifact lineage、Child Run 和 deleted_files/scheduled_tasks Runtime CAS。所有 Worker 写入通过 `SECURITY DEFINER` 窄 RPC，固定 `search_path`，并撤销业务表直权；rollback 在存在事实时失败关闭，不恢复旧宽权限 Owner。

## Catalog 门禁

`catalog/nonproduction.py` 和 `catalog/consistency.py` 是显式非生产构造器，不被生产 composition root 导入。联合目录必须包含 AR-17.2 的 18 个只读工具、`code_execute` 和 AR-17.3 的 23 个工具，共 42 项；缺失、重复、Descriptor revision/schema 或 Safety 不一致均失败关闭。

## 验证边界

单元测试使用 Fake Provider 验证 receipt、unknown/reconcile、callback 去重、Cost 幂等、Artifact checkpoint 和 42 项集合。真实 PostgreSQL、并发/RLS/权限、隔离 Mock Server 和 Provider callback 账户属于后续非生产验收；本实现不连接生产、不使用生产凭证或收费资源。

本阶段不代表 AR-17、AR-17.4 或整个 Agent Runtime 已完成。
