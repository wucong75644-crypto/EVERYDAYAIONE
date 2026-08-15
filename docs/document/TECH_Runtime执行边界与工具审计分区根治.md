# Runtime 执行边界与工具审计分区根治

## 目标与范围

本设计修复 Conversation Actor 回退后生产链路暴露的四项边界违约：Actor 绕过
HandlerFactory、知识去重中途提交 Scoped Transaction、LocalDB `NOT IN` 参数错误，以及
工具审计分区依赖未落地的外部调度。ERP 授权、浏览器过期会话和未知订单类型不在范围内。

## 已确认系统约束

1. ChatHandler 的 `org_id` 与不可变 `RequestContext` 只能由统一工厂在执行入口注入。
2. `AsyncScopedConnectionPool` 是事务唯一 Owner；内部服务函数不得 `commit`、`rollback`
   或修改 autocommit。
3. LocalDB 的 `IN` 与 `NOT IN` 共享逐值占位符和参数顺序合同；空集合沿用既有语义。
4. 工具审计写入成功不依赖未安装的 pg_cron、服务重启或人工运维。
5. Runtime 只能执行任务绑定的审计 RPC，不能获得表 DDL 或分区维护函数执行权。

## 实施设计

### Actor 上下文

`ChatGenerationExecutor` 的生产默认路径通过 `HandlerFactory.get` 创建 ChatHandler，使用
task 的 user/org 与 claim task ID 构造一次 RequestContext。测试注入的自定义 factory
保持原单参数合同；`ChatToolMixin` fallback 仅保留为防御性告警。

### 知识事务

`dedup_by_hash` 和 `dedup_by_vector` 只接收 cursor 并执行查询或更新，不再持有 connection。
`add_knowledge` 的 Scoped Connection context 负责整个“去重→淘汰→写入”的原子提交与异常
回滚，避免中途提交破坏 RLS Scope 和后续操作原子性。

### 查询构造器

`QueryBuilder._build_where` 对 `IN/NOT IN` 统一展开 `%s`，按过滤器顺序追加参数。修复位于
LocalDB 兼容层，因此库存清理、采购超期和统一过滤器调用方共享同一合同。

### 审计分区

迁移 `229_tool_audit_partition_lifecycle.sql` 重定义 owner-only 维护函数：

- 首先读取 PostgreSQL 目录；当前月、未来两月均存在且无过期分区时直接返回。
- 需要维护时取得事务级 advisory lock，创建缺失分区并拒绝同名非分区关系。
- 删除上界完整早于 90 天截止点的月分区。
- `record_runtime_tool_audit` 在完成参数和 task Scope 校验后、INSERT 前调用维护函数。
- Runtime 仅保留审计 RPC EXECUTE；维护函数不向任何服务角色授权。

迁移应用时立即执行一次维护。部署脚本随后运行只读契约验证，缺少分区、Owner、
SECURITY DEFINER、并发锁或最小权限时停止发布。

## 兼容、失败与回滚

- API、RPC 签名、表字段及工具结果行为不变；旧应用代码可与新迁移共存。
- 分区维护或审计写入失败仍只降低审计能力，不阻塞用户工具结果，但会保留 warning。
- 回滚恢复迁移 196 的函数定义和权限；新建分区及数据保留。
- 90 天策略删除的数据不可恢复。首次生产应用预计删除 2026-04 分区，约 2885 行。

## 验证合同

- Actor 单测证明默认生产 factory 注入 user/org/request ID。
- 知识重复写单测证明不调用 connection commit。
- 同步、异步和 Scoped QueryBuilder 验证 `NOT IN (%s, ...)` 与参数顺序。
- 迁移执行 apply→rollback→reapply，并验证当前月及未来两月分区、权限和并发维护合同。
- 生产发布后验证工具审计新增记录，且不再出现分区、显式 commit 和 RequestContext fallback
  三类日志；库存全量任务下一周期不得再出现 `$2` 语法错误。
