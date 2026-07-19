-- 138 rollback: 应用回退后移除统一上下文 RPC 入口。
-- 新表保留为只读恢复数据，避免故障回滚时破坏已经提交的上下文事实。

DROP FUNCTION IF EXISTS commit_generation_turn(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB,
    JSONB, JSONB, JSONB, JSONB
);

DO $revoke$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        REVOKE INSERT, UPDATE, DELETE ON conversation_context_receipts
            FROM service_role;
        REVOKE INSERT, UPDATE, DELETE ON conversation_compactions
            FROM service_role;
        REVOKE INSERT, UPDATE, DELETE ON conversation_context_items
            FROM service_role;
        REVOKE INSERT, UPDATE, DELETE ON conversation_artifacts
            FROM service_role;
    END IF;
END
$revoke$;
