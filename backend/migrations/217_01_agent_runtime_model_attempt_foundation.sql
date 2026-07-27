-- 217_01: Persistent ModelAttempt foundation.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_model_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_step_id UUID NOT NULL REFERENCES agent_model_steps(id) ON DELETE RESTRICT,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    provider TEXT NOT NULL CHECK (length(BTRIM(provider)) BETWEEN 1 AND 100),
    provider_request_id TEXT,
    status TEXT NOT NULL DEFAULT 'prepared' CHECK (status IN (
        'prepared', 'dispatching', 'completed', 'failed', 'unknown', 'cancelled'
    )),
    dispatch_phase TEXT NOT NULL DEFAULT 'prepared' CHECK (dispatch_phase IN (
        'prepared', 'request_started', 'response_started'
    )),
    retry_disposition TEXT NOT NULL DEFAULT 'forbidden' CHECK (
        retry_disposition IN ('forbidden', 'reconcile_only', 'retry_safe')
    ),
    request_receipt JSONB NOT NULL CHECK (
        jsonb_typeof(request_receipt) = 'object'
        AND pg_column_size(request_receipt) <= 262144
    ),
    response_receipt JSONB CHECK (
        response_receipt IS NULL OR (
            jsonb_typeof(response_receipt) = 'object'
            AND pg_column_size(response_receipt) <= 262144
        )
    ),
    response_hash TEXT CHECK (
        response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$'
    ),
    ambiguity_evidence JSONB NOT NULL DEFAULT '{}' CHECK (
        jsonb_typeof(ambiguity_evidence) = 'object'
        AND pg_column_size(ambiguity_evidence) <= 262144
    ),
    usage JSONB NOT NULL DEFAULT '{}' CHECK (
        jsonb_typeof(usage) = 'object' AND pg_column_size(usage) <= 262144
    ),
    late_outcome TEXT CHECK (late_outcome IN ('completed', 'failed')),
    late_receipt_recorded_at TIMESTAMPTZ,
    worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 200),
    execution_token UUID NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    dispatched_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (model_step_id, attempt_number),
    UNIQUE (model_step_id, idempotency_key),
    CHECK (
        (status IN ('completed', 'failed', 'cancelled') AND completed_at IS NOT NULL)
        OR (status IN ('prepared', 'dispatching', 'unknown') AND completed_at IS NULL)
    ),
    CHECK (
        (late_outcome IS NULL AND late_receipt_recorded_at IS NULL)
        OR (late_outcome IS NOT NULL AND late_receipt_recorded_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX uq_agent_model_attempt_unresolved
    ON agent_model_attempts(model_step_id)
    WHERE status IN ('prepared', 'dispatching', 'unknown');
CREATE INDEX idx_agent_model_attempt_reconcile
    ON agent_model_attempts(lease_expires_at, created_at, id)
    WHERE status IN ('dispatching', 'unknown');
CREATE INDEX idx_agent_model_attempt_run
    ON agent_model_attempts(run_id, attempt_number);

ALTER TABLE agent_model_attempts ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_model_attempts_owner_all ON agent_model_attempts
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_model_attempts FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE agent_model_attempts
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

RESET ROLE;
