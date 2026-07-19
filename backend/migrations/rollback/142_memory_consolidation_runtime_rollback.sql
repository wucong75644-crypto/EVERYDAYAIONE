-- 142 rollback: 停止 Consolidation Run 写入，保留来源标记与已晋升事实。

DO $revoke$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        REVOKE INSERT, UPDATE, DELETE ON memory_consolidation_runs
            FROM service_role;
    END IF;
END
$revoke$;
