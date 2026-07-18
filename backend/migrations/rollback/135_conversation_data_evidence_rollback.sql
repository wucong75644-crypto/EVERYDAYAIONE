-- 回滚 135：先切回 7 参数 Actor commit 调用后执行。

DROP FUNCTION IF EXISTS commit_generation_turn(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB
);
DROP TABLE IF EXISTS conversation_data_evidence;
