-- 220_01: Authoritative model output persisted independently from ModelStep.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_model_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_step_id UUID NOT NULL UNIQUE
        REFERENCES agent_model_steps(id) ON DELETE RESTRICT,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL
        REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    output_kind TEXT NOT NULL CHECK (output_kind IN ('text', 'structured')),
    text_content TEXT,
    structured_content JSONB,
    schema_revision TEXT,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (output_kind = 'text' AND text_content IS NOT NULL
         AND structured_content IS NULL AND schema_revision IS NULL)
        OR
        (output_kind = 'structured' AND text_content IS NULL
           AND structured_content IS NOT NULL
         AND NULLIF(BTRIM(schema_revision), '') IS NOT NULL)
    ),
    CHECK (
        (text_content IS NULL OR octet_length(text_content) <= 4194304)
        AND (structured_content IS NULL
             OR pg_column_size(structured_content) <= 4194304)
    )
);

CREATE UNIQUE INDEX uq_agent_model_results_run_step
    ON agent_model_results(run_id, model_step_id);

ALTER TABLE agent_model_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_model_results_owner_all ON agent_model_results
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_model_results FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE agent_model_results
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

RESET ROLE;
