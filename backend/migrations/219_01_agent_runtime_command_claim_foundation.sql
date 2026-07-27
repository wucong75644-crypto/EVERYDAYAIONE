-- 219_01: Durable Agent Runtime Command claim identity and lease state.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_command_claims (
    command_id UUID PRIMARY KEY REFERENCES agent_session_commands(id)
        ON DELETE RESTRICT,
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id)
        ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('user', 'channel', 'system')),
    scope_id TEXT NOT NULL,
    worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 200),
    fencing_token UUID NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    run_id UUID REFERENCES agent_runs(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (
        status IN ('claimed', 'completed', 'failed', 'attempts_exhausted')
    ),
    outcome TEXT CHECK (
        outcome IN ('completed', 'failed', 'attempts_exhausted')
    ),
    error_class TEXT CHECK (
        error_class IS NULL OR length(error_class) BETWEEN 1 AND 200
    ),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (status = 'claimed' AND finished_at IS NULL AND outcome IS NULL)
        OR (status <> 'claimed' AND finished_at IS NOT NULL
            AND outcome IS NOT NULL)
    )
);

CREATE INDEX idx_agent_command_claims_recovery
    ON agent_command_claims(lease_expires_at, command_id)
    WHERE status = 'claimed';

ALTER TABLE agent_command_claims ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_command_claims_owner_all ON agent_command_claims
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_command_claims FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE agent_command_claims
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

RESET ROLE;
