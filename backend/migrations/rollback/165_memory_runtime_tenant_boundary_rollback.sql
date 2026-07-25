-- 回滚 165：仅撤销新增的 FORCE RLS、策略与 Runtime 能力。
-- 所有权恢复必须另行执行 deploy/rollback-memory-runtime-ownership.sh。

REVOKE EXECUTE ON FUNCTION commit_memory_session_flush(
    UUID, UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, JSONB, TEXT, TEXT, TEXT
) FROM everydayai_runtime, everydayai_worker;
REVOKE EXECUTE ON FUNCTION commit_memory_consolidation(
    UUID, UUID, UUID[], TEXT, JSONB, TEXT, TEXT, JSONB
) FROM everydayai_runtime, everydayai_worker;

REVOKE ALL ON TABLE
    memory_pipeline_state,
    memory_session_logs,
    memory_consolidation_runs
FROM everydayai_runtime, everydayai_worker;

DROP POLICY IF EXISTS tenant_memory_pipeline_state ON memory_pipeline_state;
DROP POLICY IF EXISTS tenant_memory_session_logs ON memory_session_logs;
DROP POLICY IF EXISTS tenant_memory_consolidation_runs
    ON memory_consolidation_runs;

DROP POLICY IF EXISTS tenant_memory_atoms ON memory_atoms;
CREATE POLICY tenant_memory_atoms
ON memory_atoms TO everydayai_runtime, everydayai_worker
USING (tenant_user_fact_visible(org_id, user_id))
WITH CHECK (tenant_user_fact_visible(org_id, user_id));

ALTER TABLE memory_pipeline_state NO FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_session_logs NO FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_consolidation_runs NO FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_atoms NO FORCE ROW LEVEL SECURITY;

DO $legacy_grants$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
        ON TABLE memory_session_logs, memory_consolidation_runs
        TO service_role;
        GRANT EXECUTE ON FUNCTION commit_memory_session_flush(
            UUID, UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, JSONB,
            TEXT, TEXT, TEXT
        ) TO service_role;
        GRANT EXECUTE ON FUNCTION commit_memory_consolidation(
            UUID, UUID, UUID[], TEXT, JSONB, TEXT, TEXT, JSONB
        ) TO service_role;
    END IF;
END
$legacy_grants$;
