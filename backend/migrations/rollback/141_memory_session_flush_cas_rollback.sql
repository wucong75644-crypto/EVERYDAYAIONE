-- 141 rollback: 停止原子 Session Flush，保留 cursor 与已提交 Session Log。

DROP FUNCTION IF EXISTS commit_memory_session_flush(
    UUID, UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, JSONB,
    TEXT, TEXT, TEXT
);
