-- 140 rollback: 停止 Session Memory 写入，保留已经提交的事实与兼容字段。
-- 旧应用会忽略 additive 字段；不删除数据，避免回滚造成记忆溯源丢失。

DO $revoke$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        REVOKE INSERT, UPDATE, DELETE ON memory_session_logs
            FROM service_role;
    END IF;
END
$revoke$;
