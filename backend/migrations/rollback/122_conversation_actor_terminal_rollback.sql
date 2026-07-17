-- 回滚 122：仅在 Actor feature flag 关闭且无 Actor task 运行时执行。

DROP FUNCTION IF EXISTS cancel_generation_turn(UUID, UUID, UUID);
DROP FUNCTION IF EXISTS fail_generation_turn(UUID, UUID, TEXT, TEXT);
DROP FUNCTION IF EXISTS commit_generation_turn(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB
);
