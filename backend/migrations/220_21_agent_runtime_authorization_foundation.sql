-- 220_21: Persistent Runtime interactions, grants, grant uses, and receipts.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_interactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id UUID NOT NULL UNIQUE
        REFERENCES agent_actions(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL
        REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'resolved', 'expired', 'cancelled')),
    prompt JSONB NOT NULL CHECK (
        jsonb_typeof(prompt) = 'object'
        AND pg_column_size(prompt) <= 65536
    ),
    prompt_hash TEXT NOT NULL CHECK (prompt_hash ~ '^[0-9a-f]{64}$'),
    response TEXT CHECK (response IN ('approve', 'deny')),
    response_hash TEXT CHECK (
        response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$'
    ),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    expires_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (status = 'open' AND response IS NULL AND resolved_at IS NULL)
        OR (
            status = 'resolved'
            AND response IS NOT NULL
            AND response_hash IS NOT NULL
            AND resolved_at IS NOT NULL
        )
        OR (status IN ('expired', 'cancelled') AND resolved_at IS NOT NULL)
    )
);

CREATE INDEX idx_agent_interactions_open
    ON agent_interactions(expires_at, id) WHERE status = 'open';

CREATE TABLE agent_authorization_grants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL
        REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    run_id UUID REFERENCES agent_runs(id) ON DELETE RESTRICT,
    action_id UUID REFERENCES agent_actions(id) ON DELETE RESTRICT,
    interaction_id UUID REFERENCES agent_interactions(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    grant_kind TEXT NOT NULL CHECK (grant_kind IN ('action', 'workflow')),
    workflow_key TEXT,
    arguments_hash TEXT CHECK (
        arguments_hash IS NULL OR arguments_hash ~ '^[0-9a-f]{64}$'
    ),
    effective_scope JSONB NOT NULL CHECK (
        jsonb_typeof(effective_scope) = 'object'
        AND pg_column_size(effective_scope) <= 65536
    ),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'revoked', 'expired')),
    nonce UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (
            grant_kind = 'action'
            AND action_id IS NOT NULL
            AND workflow_key IS NULL
            AND arguments_hash IS NOT NULL
        )
        OR (
            grant_kind = 'workflow'
            AND action_id IS NULL
            AND NULLIF(btrim(workflow_key), '') IS NOT NULL
        )
    ),
    CHECK (
        (status = 'revoked') = (revoked_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX uq_agent_action_grant
    ON agent_authorization_grants(action_id)
    WHERE grant_kind = 'action';
CREATE INDEX idx_agent_workflow_grants
    ON agent_authorization_grants(
        session_id, workflow_key, expires_at
    ) WHERE grant_kind = 'workflow' AND status = 'active';

CREATE TABLE agent_authorization_grant_uses (
    grant_id UUID NOT NULL
        REFERENCES agent_authorization_grants(id) ON DELETE RESTRICT,
    action_id UUID NOT NULL UNIQUE
        REFERENCES agent_actions(id) ON DELETE RESTRICT,
    arguments_hash TEXT NOT NULL
        CHECK (arguments_hash ~ '^[0-9a-f]{64}$'),
    used_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (grant_id, action_id)
);

CREATE TABLE agent_policy_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL
        REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    grant_id UUID REFERENCES agent_authorization_grants(id)
        ON DELETE RESTRICT,
    decision TEXT NOT NULL
        CHECK (decision IN ('allow', 'require_authorization', 'deny')),
    arguments_hash TEXT NOT NULL
        CHECK (arguments_hash ~ '^[0-9a-f]{64}$'),
    executor_type TEXT NOT NULL CHECK (
        executor_type = btrim(executor_type)
        AND length(executor_type) BETWEEN 1 AND 200
    ),
    executor_revision INTEGER NOT NULL CHECK (executor_revision > 0),
    policy_revision TEXT NOT NULL CHECK (
        policy_revision = btrim(policy_revision)
        AND length(policy_revision) BETWEEN 1 AND 200
    ),
    effective_scope JSONB NOT NULL CHECK (
        jsonb_typeof(effective_scope) = 'object'
        AND pg_column_size(effective_scope) <= 65536
    ),
    reason_codes TEXT[] NOT NULL CHECK (cardinality(reason_codes) > 0),
    obligations TEXT[] NOT NULL DEFAULT '{}',
    receipt_hash TEXT NOT NULL UNIQUE
        CHECK (receipt_hash ~ '^[0-9a-f]{64}$'),
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    CHECK (expires_at > evaluated_at),
    CHECK ((decision = 'allow') OR grant_id IS NULL),
    UNIQUE (
        action_id, arguments_hash, executor_type,
        executor_revision, policy_revision
    )
);

CREATE INDEX idx_agent_policy_receipts_action
    ON agent_policy_receipts(action_id, evaluated_at DESC);

ALTER TABLE agent_interactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_authorization_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_authorization_grant_uses ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_policy_receipts ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_interactions_owner_all ON agent_interactions
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_authorization_grants_owner_all
    ON agent_authorization_grants
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_authorization_grant_uses_owner_all
    ON agent_authorization_grant_uses
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_policy_receipts_owner_all ON agent_policy_receipts
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_interactions FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_authorization_grants FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_authorization_grant_uses FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_policy_receipts FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE
    agent_interactions, agent_authorization_grants,
    agent_authorization_grant_uses, agent_policy_receipts
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

RESET ROLE;
