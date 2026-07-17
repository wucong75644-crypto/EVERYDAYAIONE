-- 回滚 123：仅在 Actor feature flag 关闭且无 Actor task 运行时执行。

DROP FUNCTION IF EXISTS update_generation_progress(UUID, UUID, TEXT, JSONB);
