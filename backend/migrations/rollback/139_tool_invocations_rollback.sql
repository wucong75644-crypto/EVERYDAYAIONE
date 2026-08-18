-- 工具幂等表只允许在没有运行中或未知结果记录时回滚。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM tool_invocations
         WHERE status IN ('running', 'uncertain')
    ) THEN
        RAISE EXCEPTION 'ACTOR_TOOL_INVOCATIONS_UNSAFE_TO_ROLLBACK';
    END IF;
END;
$$;

DROP FUNCTION IF EXISTS complete_tool_invocation(UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT);
DROP FUNCTION IF EXISTS begin_tool_invocation(UUID, UUID, UUID, UUID, TEXT, TEXT, TEXT);
DROP TABLE IF EXISTS tool_invocations;
