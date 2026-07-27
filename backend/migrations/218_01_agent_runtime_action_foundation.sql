-- 218_01: Agent Runtime Action, Attempt, and Result persistence.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_actions (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    model_step_id UUID NOT NULL REFERENCES agent_model_steps(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    action_index INTEGER NOT NULL CHECK (action_index >= 0),
    stable_tool_call_id TEXT NOT NULL CHECK (length(stable_tool_call_id) BETWEEN 1 AND 300),
    provider_call_id TEXT CHECK (
        provider_call_id IS NULL OR length(provider_call_id) BETWEEN 1 AND 300
    ),
    tool_name TEXT NOT NULL CHECK (
        tool_name = lower(btrim(tool_name))
        AND tool_name ~ '^[a-z][a-z0-9_.:-]{0,199}$'
    ),
    arguments JSONB NOT NULL CHECK (
        jsonb_typeof(arguments) = 'object' AND pg_column_size(arguments) <= 262144
    ),
    arguments_hash TEXT NOT NULL CHECK (arguments_hash ~ '^[0-9a-f]{32}$'),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{32}$'),
    batch_hash TEXT NOT NULL CHECK (batch_hash ~ '^[0-9a-f]{32}$'),
    wave INTEGER NOT NULL DEFAULT 0 CHECK (wave >= 0),
    dependency_ids UUID[] NOT NULL DEFAULT '{}',
    blocking BOOLEAN NOT NULL DEFAULT TRUE,
    policy_decision TEXT NOT NULL CHECK (
        policy_decision IN ('preauthorized', 'requires_authorization', 'rejected')
    ),
    policy_snapshot JSONB NOT NULL CHECK (
        jsonb_typeof(policy_snapshot) = 'object'
        AND pg_column_size(policy_snapshot) <= 65536
    ),
    policy_revision TEXT NOT NULL CHECK (length(policy_revision) BETWEEN 1 AND 200),
    retry_disposition TEXT NOT NULL CHECK (retry_disposition IN (
        'retry_safe', 'retry_after_reconcile', 'retry_requires_user',
        'non_retryable', 'compensate'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'requested', 'awaiting_authorization', 'queued', 'running',
        'accepted', 'unknown', 'completed', 'failed', 'rejected', 'cancelled'
    )),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    terminal_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    started_at TIMESTAMPTZ,
    accepted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (model_step_id, action_index),
    UNIQUE (model_step_id, stable_tool_call_id),
    CHECK (cardinality(dependency_ids) <= 100),
    CHECK (NOT id = ANY(dependency_ids)),
    CHECK (
        (status IN ('completed', 'failed', 'rejected', 'cancelled')
         AND completed_at IS NOT NULL)
        OR (status NOT IN ('completed', 'failed', 'rejected', 'cancelled')
            AND completed_at IS NULL)
    )
);

CREATE INDEX idx_agent_actions_claim
    ON agent_actions(created_at, id) WHERE status = 'queued';
CREATE INDEX idx_agent_actions_reconcile
    ON agent_actions(updated_at, id) WHERE status IN ('accepted', 'unknown');
CREATE INDEX idx_agent_actions_run ON agent_actions(run_id, action_index);

CREATE TABLE agent_action_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    status TEXT NOT NULL CHECK (status IN (
        'claimed', 'dispatching', 'accepted', 'completed',
        'failed', 'unknown', 'cancelled'
    )),
    dispatch_phase TEXT NOT NULL CHECK (
        dispatch_phase IN ('claimed', 'request_started', 'accepted')
    ),
    worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 200),
    execution_token UUID NOT NULL UNIQUE,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 300),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{32}$'),
    external_receipt JSONB NOT NULL DEFAULT '{}' CHECK (
        jsonb_typeof(external_receipt) = 'object'
        AND pg_column_size(external_receipt) <= 65536
    ),
    ambiguity_evidence JSONB NOT NULL DEFAULT '{}' CHECK (
        jsonb_typeof(ambiguity_evidence) = 'object'
        AND pg_column_size(ambiguity_evidence) <= 65536
    ),
    retry_disposition TEXT NOT NULL CHECK (retry_disposition IN (
        'retry_safe', 'retry_after_reconcile', 'retry_requires_user',
        'non_retryable', 'compensate'
    )),
    reconciliation_token UUID UNIQUE,
    reconciliation_lease_expires_at TIMESTAMPTZ,
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    dispatched_at TIMESTAMPTZ,
    accepted_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (action_id, attempt_number),
    UNIQUE (action_id, idempotency_key),
    CHECK (
        (status = 'accepted' AND external_receipt <> '{}'::JSONB
         AND accepted_at IS NOT NULL)
        OR status <> 'accepted'
    ),
    CHECK (
        (status = 'unknown' AND ambiguity_evidence <> '{}'::JSONB)
        OR status <> 'unknown'
    ),
    CHECK (
        (status IN ('completed', 'failed', 'cancelled') AND ended_at IS NOT NULL)
        OR (status NOT IN ('completed', 'failed', 'cancelled') AND ended_at IS NULL)
    ),
    CHECK (
        (reconciliation_token IS NULL) =
        (reconciliation_lease_expires_at IS NULL)
    )
);

CREATE INDEX idx_agent_action_attempts_action
    ON agent_action_attempts(action_id, attempt_number DESC);
CREATE INDEX idx_agent_action_attempts_reconcile
    ON agent_action_attempts(reconciliation_lease_expires_at, id)
    WHERE status IN ('accepted', 'unknown');

CREATE TABLE agent_action_results (
    action_id UUID PRIMARY KEY REFERENCES agent_actions(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('success', 'empty', 'degraded', 'error')),
    result_hash TEXT NOT NULL CHECK (result_hash ~ '^[0-9a-f]{32}$'),
    summary TEXT NOT NULL CHECK (length(summary) <= 10000),
    data JSONB CHECK (
        data IS NULL OR
        (jsonb_typeof(data) IN ('object', 'array')
         AND pg_column_size(data) <= 1048576)
    ),
    artifact_ids UUID[] NOT NULL DEFAULT '{}'
        CHECK (cardinality(artifact_ids) <= 100),
    usage JSONB NOT NULL DEFAULT '{}' CHECK (
        jsonb_typeof(usage) = 'object' AND pg_column_size(usage) <= 65536
    ),
    cost JSONB NOT NULL DEFAULT '{}' CHECK (
        jsonb_typeof(cost) = 'object' AND pg_column_size(cost) <= 65536
    ),
    external_receipt JSONB NOT NULL DEFAULT '{}' CHECK (
        jsonb_typeof(external_receipt) = 'object'
        AND pg_column_size(external_receipt) <= 65536
    ),
    error_code TEXT CHECK (
        error_code IS NULL OR length(error_code) BETWEEN 1 AND 200
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((status = 'error') = (error_code IS NOT NULL))
);

ALTER TABLE agent_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_action_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_action_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_actions_owner_all ON agent_actions
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_action_attempts_owner_all ON agent_action_attempts
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_action_results_owner_all ON agent_action_results
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_actions FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_action_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_action_results FORCE ROW LEVEL SECURITY;

CREATE FUNCTION _agent_action_json_is_safe(p_value JSONB)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public
RETURN p_value IS NOT NULL
   AND p_value::TEXT !~* '"(password|passwd|secret|token|api[_-]?key|authorization|cookie)"[[:space:]]*:';

REVOKE ALL ON TABLE agent_actions, agent_action_attempts, agent_action_results
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
REVOKE ALL ON FUNCTION _agent_action_json_is_safe(JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

RESET ROLE;
