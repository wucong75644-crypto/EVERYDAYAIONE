-- 165: Memory Runtime 四表租户边界与最小角色能力。
-- 前置：先执行 deploy/transfer-memory-runtime-ownership.sh。
-- 依赖 150_agent_runtime_tenant_defense.sql 与 164_actor_task_execution_capabilities.sql。

DO $preflight$
DECLARE
    invalid_owners TEXT;
BEGIN
    SELECT string_agg(c.relname || '=' || owner_role.rolname, ', ' ORDER BY c.relname)
      INTO invalid_owners
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = c.relowner
     WHERE n.nspname = 'public'
       AND c.relname = ANY(ARRAY[
           'memory_pipeline_state',
           'memory_session_logs',
           'memory_consolidation_runs',
           'memory_atoms'
       ])
       AND owner_role.rolname <> 'everydayai_owner';
    IF invalid_owners IS NOT NULL THEN
        RAISE EXCEPTION 'MEMORY_RUNTIME_OWNER_INVALID: %', invalid_owners;
    END IF;
END
$preflight$;

ALTER TABLE memory_pipeline_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_pipeline_state FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_session_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_session_logs FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_consolidation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_consolidation_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_atoms ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_atoms FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_memory_pipeline_state ON memory_pipeline_state;
CREATE POLICY tenant_memory_pipeline_state
ON memory_pipeline_state
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (
    current_user = 'everydayai_owner'
    OR (
        tenant_user_fact_visible(org_id, user_id)
        AND tenant_conversation_visible(session_id, org_id)
    )
)
WITH CHECK (
    current_user = 'everydayai_owner'
    OR (
        tenant_user_fact_visible(org_id, user_id)
        AND tenant_conversation_visible(session_id, org_id)
    )
);

DROP POLICY IF EXISTS tenant_memory_session_logs ON memory_session_logs;
CREATE POLICY tenant_memory_session_logs
ON memory_session_logs
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (
    current_user = 'everydayai_owner'
    OR (
        user_id = tenant_actor_user_id()
        AND EXISTS (
            SELECT 1
              FROM conversations conversation
             WHERE conversation.id = memory_session_logs.conversation_id
               AND conversation.user_id = memory_session_logs.user_id
               AND tenant_conversation_visible(
                   conversation.id,
                   conversation.org_id
               )
        )
    )
)
WITH CHECK (
    current_user = 'everydayai_owner'
    OR (
        user_id = tenant_actor_user_id()
        AND EXISTS (
            SELECT 1
              FROM conversations conversation
             WHERE conversation.id = memory_session_logs.conversation_id
               AND conversation.user_id = memory_session_logs.user_id
               AND tenant_conversation_visible(
                   conversation.id,
                   conversation.org_id
               )
        )
    )
);

DROP POLICY IF EXISTS tenant_memory_consolidation_runs
    ON memory_consolidation_runs;
CREATE POLICY tenant_memory_consolidation_runs
ON memory_consolidation_runs
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (
    current_user = 'everydayai_owner'
    OR (
        user_id = tenant_actor_user_id()
        AND NOT EXISTS (
            SELECT 1
              FROM unnest(source_log_ids) source_log_id
              LEFT JOIN memory_session_logs session_log
                ON session_log.id = source_log_id
               AND session_log.user_id = memory_consolidation_runs.user_id
             WHERE session_log.id IS NULL
        )
    )
)
WITH CHECK (
    current_user = 'everydayai_owner'
    OR (
        user_id = tenant_actor_user_id()
        AND NOT EXISTS (
            SELECT 1
              FROM unnest(source_log_ids) source_log_id
              LEFT JOIN memory_session_logs session_log
                ON session_log.id = source_log_id
               AND session_log.user_id = memory_consolidation_runs.user_id
             WHERE session_log.id IS NULL
        )
    )
);

DROP POLICY IF EXISTS tenant_memory_atoms ON memory_atoms;
CREATE POLICY tenant_memory_atoms
ON memory_atoms
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (
    current_user = 'everydayai_owner'
    OR tenant_user_fact_visible(org_id, user_id)
)
WITH CHECK (
    current_user = 'everydayai_owner'
    OR tenant_user_fact_visible(org_id, user_id)
);

REVOKE ALL ON TABLE
    memory_pipeline_state,
    memory_session_logs,
    memory_consolidation_runs
FROM PUBLIC, everydayai_runtime, everydayai_worker;

GRANT SELECT, INSERT, UPDATE
ON TABLE memory_pipeline_state, memory_session_logs
TO everydayai_runtime, everydayai_worker;
GRANT SELECT, INSERT
ON TABLE memory_consolidation_runs
TO everydayai_runtime, everydayai_worker;

REVOKE ALL ON FUNCTION commit_memory_session_flush(
    UUID, UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, JSONB, TEXT, TEXT, TEXT
) FROM PUBLIC;
REVOKE ALL ON FUNCTION commit_memory_consolidation(
    UUID, UUID, UUID[], TEXT, JSONB, TEXT, TEXT, JSONB
) FROM PUBLIC;
DO $legacy_revoke$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        REVOKE ALL ON TABLE
            memory_pipeline_state,
            memory_session_logs,
            memory_consolidation_runs
        FROM service_role;
        REVOKE ALL ON FUNCTION commit_memory_session_flush(
            UUID, UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, JSONB,
            TEXT, TEXT, TEXT
        ) FROM service_role;
        REVOKE ALL ON FUNCTION commit_memory_consolidation(
            UUID, UUID, UUID[], TEXT, JSONB, TEXT, TEXT, JSONB
        ) FROM service_role;
    END IF;
END
$legacy_revoke$;
GRANT EXECUTE ON FUNCTION commit_memory_session_flush(
    UUID, UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, JSONB, TEXT, TEXT, TEXT
) TO everydayai_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION commit_memory_consolidation(
    UUID, UUID, UUID[], TEXT, JSONB, TEXT, TEXT, JSONB
) TO everydayai_runtime, everydayai_worker;
