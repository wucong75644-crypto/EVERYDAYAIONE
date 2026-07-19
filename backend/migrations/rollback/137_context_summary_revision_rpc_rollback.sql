-- 回滚 137：停止调用后删除 ContextSummary 原子提交 RPC。

DROP FUNCTION IF EXISTS apply_context_summary(
    UUID, BIGINT, BIGINT, UUID, TEXT, INTEGER
);
