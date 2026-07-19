-- 143 rollback: 停止新的原子晋升，保留已完成 Run 与 Curated Memory。

DROP FUNCTION IF EXISTS commit_memory_consolidation(
    UUID, UUID, UUID[], TEXT, JSONB, TEXT, TEXT, JSONB
);
