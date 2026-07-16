-- 回滚 119：确认后端已停止读写幂等记录后执行。

DROP FUNCTION IF EXISTS cleanup_expired_message_generation_requests();
DROP FUNCTION IF EXISTS claim_message_generation_request(
    UUID, UUID, UUID, VARCHAR, CHAR, VARCHAR, UUID
);
DROP TABLE IF EXISTS message_generation_requests;
