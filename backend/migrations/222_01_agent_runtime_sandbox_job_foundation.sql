-- 222_01: Persistent Sandbox Job facts. No execution owner is connected here.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_sandbox_json_is_safe(p_value JSONB)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
SELECT p_value IS NOT NULL
   AND p_value::TEXT !~* '"(code|prompt|description|filename|file_name|path|host_path|password|passwd|secret|token|api[_-]?key|authorization|cookie|exception|traceback|stderr|stdout)"[[:space:]]*:'
   AND p_value::TEXT !~* '(-----BEGIN [A-Z ]*PRIVATE KEY-----|postgres(ql)?://|redis://|oss://|file://|/Users/|/home/|/var/)'
   AND p_value::TEXT !~* '(password|passwd|secret|api[_-]?key|authorization)[[:space:]]*[=:]'
$$;

CREATE FUNCTION _agent_sandbox_summary_is_safe(p_value TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
SELECT p_value IS NULL OR (
    length(p_value) <= 8192
    AND p_value !~* '(password|passwd|secret|api[_-]?key|authorization|cookie|oss://|file://|/Users/|/home/|/var/)'
    AND p_value !~ '(eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|[A-Za-z0-9+/]{80,}={0,2})'
)
$$;

CREATE FUNCTION _agent_sandbox_manifest_is_valid(
    p_manifest JSONB, p_kind TEXT
) RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE item JSONB; allowed TEXT[]; identity_key TEXT; identity_pattern TEXT;
BEGIN
    IF (jsonb_typeof(p_manifest) = 'object'
        AND p_manifest - ARRAY['schema_revision','items'] = '{}'::JSONB
        AND p_manifest->>'schema_revision' = '1'
        AND jsonb_typeof(p_manifest->'items') = 'array'
        AND jsonb_array_length(p_manifest->'items') <= 100) IS NOT TRUE
    THEN RETURN FALSE; END IF;
    IF p_kind = 'input' THEN
        allowed := ARRAY['artifact_ref','content_sha256','size_bytes','media_type'];
        identity_key := 'artifact_ref';
        identity_pattern := '^artifact:[A-Za-z0-9_.:-]{1,240}$';
    ELSIF p_kind = 'artifact' THEN
        allowed := ARRAY['workspace_object_ref','content_sha256','size_bytes','media_type'];
        identity_key := 'workspace_object_ref';
        identity_pattern := '^workspace-object:sha256:[0-9a-f]{64}$';
    ELSIF p_kind = 'partial' THEN
        allowed := ARRAY['temporary_object_ref','content_sha256','size_bytes','media_type'];
        identity_key := 'temporary_object_ref';
        identity_pattern := '^sandbox-temp:[0-9a-f-]{36}$';
    ELSE RETURN FALSE; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(p_manifest->'items') LOOP
        IF (jsonb_typeof(item) = 'object' AND item - allowed = '{}'::JSONB
            AND item->>identity_key ~ identity_pattern
            AND item->>'content_sha256' ~ '^[0-9a-f]{64}$'
            AND item->>'size_bytes' ~ '^[0-9]{1,19}$'
            AND (item->>'size_bytes')::NUMERIC >= 0
            AND item->>'media_type' ~ '^[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+$'
            AND _agent_sandbox_json_is_safe(item)) IS NOT TRUE
        THEN RETURN FALSE; END IF;
    END LOOP;
    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN RETURN FALSE;
END;
$$;

CREATE FUNCTION _agent_sandbox_evidence_is_valid(p_value JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE code JSONB;
BEGIN
    IF (jsonb_typeof(p_value) = 'object'
        AND p_value - ARRAY['kind','reason_codes'] = '{}'::JSONB
        AND p_value->>'kind' ~ '^[A-Z][A-Z0-9_]{0,199}$'
        AND _agent_sandbox_json_is_safe(p_value)) IS NOT TRUE
    THEN RETURN FALSE; END IF;
    IF NOT (p_value ? 'reason_codes') THEN RETURN TRUE; END IF;
    IF jsonb_typeof(p_value->'reason_codes') <> 'array' THEN RETURN FALSE; END IF;
    FOR code IN SELECT value FROM jsonb_array_elements(
        p_value->'reason_codes')
    LOOP
        IF (jsonb_typeof(code) = 'string'
            AND code #>> '{}' ~ '^[A-Z][A-Z0-9_]{0,199}$') IS NOT TRUE
        THEN RETURN FALSE; END IF;
    END LOOP;
    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN RETURN FALSE;
END;
$$;

CREATE FUNCTION _agent_sandbox_receipt_is_valid(p_receipt JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    RETURN (jsonb_typeof(p_receipt) = 'object'
       AND _agent_sandbox_json_is_safe(p_receipt)
       AND _agent_sandbox_manifest_is_valid(
           p_receipt->'artifact_manifest','artifact')
       AND _agent_sandbox_manifest_is_valid(
           p_receipt->'partial_effects','partial')
       AND p_receipt->>'receipt_revision' ~ '^[1-9][0-9]{0,8}$'
       AND p_receipt->>'execution_outcome' IN (
           'success','error','timeout','interrupted')
       AND p_receipt->>'stdout_original_length' ~ '^[0-9]{1,19}$'
       AND p_receipt->>'stderr_original_length' ~ '^[0-9]{1,19}$'
       AND p_receipt->>'stdout_sha256' ~ '^[0-9a-f]{64}$'
       AND p_receipt->>'stderr_sha256' ~ '^[0-9a-f]{64}$'
       AND p_receipt->>'stdout_truncated' IN ('true','false')
       AND p_receipt->>'stderr_truncated' IN ('true','false')
       AND _agent_sandbox_summary_is_safe(p_receipt->>'stdout_summary')
       AND _agent_sandbox_summary_is_safe(p_receipt->>'stderr_summary')
       AND p_receipt->>'cleanup_status' IN (
           'not_required','pending','running','completed','failed','unknown')
       AND p_receipt->>'materialization_status' IN (
           'not_started','pending','completed','failed','unknown')
       AND (
           p_receipt->>'cleanup_status' = 'not_required'
           AND p_receipt->'cleanup_evidence' = '{}'::JSONB
           OR p_receipt->>'cleanup_status' <> 'not_required'
              AND _agent_sandbox_evidence_is_valid(
                  p_receipt->'cleanup_evidence')
       )
       AND COALESCE(p_receipt->'materialization_receipt','{}') = '{}'::JSONB
    ) IS TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$;

CREATE FUNCTION _agent_sandbox_receipt_hash(p_receipt JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
SELECT encode(sha256(convert_to(p_receipt::TEXT,'UTF8')),'hex')
$$;

CREATE TABLE agent_sandbox_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    action_id UUID NOT NULL UNIQUE REFERENCES agent_actions(id) ON DELETE RESTRICT,
    attempt_id UUID NOT NULL UNIQUE REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
    dispatch_intent_id UUID NOT NULL UNIQUE
        REFERENCES agent_action_dispatch_intents(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    external_idempotency_key TEXT NOT NULL UNIQUE CHECK (
        external_idempotency_key = btrim(external_idempotency_key)
        AND length(external_idempotency_key) BETWEEN 1 AND 300
    ),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    executor_type TEXT NOT NULL CHECK (
        executor_type = btrim(executor_type)
        AND length(executor_type) BETWEEN 1 AND 200
    ),
    executor_revision INTEGER NOT NULL CHECK (executor_revision > 0),
    runtime TEXT NOT NULL CHECK (runtime = 'python'),
    runtime_revision TEXT NOT NULL CHECK (
        runtime_revision = btrim(runtime_revision)
        AND length(runtime_revision) BETWEEN 1 AND 200
    ),
    workspace_scope_ref TEXT NOT NULL CHECK (
        workspace_scope_ref ~ '^ws-scope:[A-Za-z0-9_.:-]{1,240}$'
    ),
    code_ref TEXT NOT NULL CHECK (
        code_ref = 'agent-action:' || action_id::TEXT || ':arguments.code'
    ),
    code_sha256 TEXT NOT NULL CHECK (code_sha256 ~ '^[0-9a-f]{64}$'),
    input_manifest JSONB NOT NULL DEFAULT '{"schema_revision":1,"items":[]}' CHECK (
        jsonb_typeof(input_manifest) = 'object'
        AND jsonb_typeof(input_manifest->'items') = 'array'
        AND input_manifest->>'schema_revision' = '1'
        AND jsonb_array_length(input_manifest->'items') <= 100
        AND pg_column_size(input_manifest) <= 65536
        AND _agent_sandbox_manifest_is_valid(input_manifest,'input')
    ),
    resource_limits JSONB NOT NULL CHECK (
        jsonb_typeof(resource_limits) = 'object'
        AND pg_column_size(resource_limits) <= 65536
        AND _agent_sandbox_json_is_safe(resource_limits)
    ),
    status TEXT NOT NULL CHECK (status IN (
        'prepared', 'queued', 'claimed', 'starting', 'running',
        'cancel_requested', 'succeeded', 'failed', 'timed_out',
        'cancelled', 'unknown'
    )),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    claim_worker_id TEXT CHECK (
        claim_worker_id IS NULL OR (
            claim_worker_id = btrim(claim_worker_id)
            AND length(claim_worker_id) BETWEEN 1 AND 200
        )
    ),
    claim_token UUID UNIQUE,
    fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    lease_expires_at TIMESTAMPTZ,
    reconciliation_worker_id TEXT CHECK (
        reconciliation_worker_id IS NULL OR (
            reconciliation_worker_id = btrim(reconciliation_worker_id)
            AND length(reconciliation_worker_id) BETWEEN 1 AND 200
        )
    ),
    reconciliation_token UUID UNIQUE,
    reconciliation_lease_expires_at TIMESTAMPTZ,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    claimed_at TIMESTAMPTZ,
    starting_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    cancel_requested_at TIMESTAMPTZ,
    cancel_accepted_at TIMESTAMPTZ,
    cancel_confirmed_at TIMESTAMPTZ,
    terminal_at TIMESTAMPTZ,
    terminal_reason TEXT CHECK (
        terminal_reason IS NULL OR (
            terminal_reason = btrim(terminal_reason)
            AND terminal_reason ~ '^[A-Z][A-Z0-9_]{0,199}$'
        )
    ),
    execution_outcome TEXT CHECK (
        execution_outcome IS NULL OR execution_outcome IN (
            'success', 'error', 'timeout', 'interrupted'
        )
    ),
    receipt_revision INTEGER NOT NULL DEFAULT 1 CHECK (receipt_revision > 0),
    receipt_hash TEXT CHECK (
        receipt_hash IS NULL OR receipt_hash ~ '^[0-9a-f]{64}$'
    ),
    stdout_summary TEXT CHECK (_agent_sandbox_summary_is_safe(stdout_summary)),
    stdout_original_length BIGINT NOT NULL DEFAULT 0
        CHECK (stdout_original_length >= 0),
    stdout_sha256 TEXT CHECK (
        stdout_sha256 IS NULL OR stdout_sha256 ~ '^[0-9a-f]{64}$'
    ),
    stdout_truncated BOOLEAN NOT NULL DEFAULT FALSE,
    stderr_summary TEXT CHECK (_agent_sandbox_summary_is_safe(stderr_summary)),
    stderr_original_length BIGINT NOT NULL DEFAULT 0
        CHECK (stderr_original_length >= 0),
    stderr_sha256 TEXT CHECK (
        stderr_sha256 IS NULL OR stderr_sha256 ~ '^[0-9a-f]{64}$'
    ),
    stderr_truncated BOOLEAN NOT NULL DEFAULT FALSE,
    artifact_manifest JSONB NOT NULL DEFAULT '{"schema_revision":1,"items":[]}' CHECK (
        jsonb_typeof(artifact_manifest) = 'object'
        AND jsonb_typeof(artifact_manifest->'items') = 'array'
        AND artifact_manifest->>'schema_revision' = '1'
        AND jsonb_array_length(artifact_manifest->'items') <= 100
        AND pg_column_size(artifact_manifest) <= 262144
        AND _agent_sandbox_manifest_is_valid(artifact_manifest,'artifact')
    ),
    partial_effects JSONB NOT NULL DEFAULT '{"schema_revision":1,"items":[]}' CHECK (
        jsonb_typeof(partial_effects) = 'object'
        AND jsonb_typeof(partial_effects->'items') = 'array'
        AND partial_effects->>'schema_revision' = '1'
        AND jsonb_array_length(partial_effects->'items') <= 100
        AND pg_column_size(partial_effects) <= 262144
        AND _agent_sandbox_manifest_is_valid(partial_effects,'partial')
    ),
    materialization_status TEXT NOT NULL DEFAULT 'not_started' CHECK (
        materialization_status IN (
            'not_started', 'pending', 'completed', 'failed', 'unknown'
        )
    ),
    materialization_receipt JSONB NOT NULL DEFAULT '{}' CHECK (
        jsonb_typeof(materialization_receipt) = 'object'
        AND pg_column_size(materialization_receipt) <= 65536
        AND _agent_sandbox_json_is_safe(materialization_receipt)
    ),
    cleanup_status TEXT NOT NULL DEFAULT 'not_required' CHECK (
        cleanup_status IN (
            'not_required', 'pending', 'running', 'completed',
            'failed', 'unknown'
        )
    ),
    cleanup_attempts INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_attempts >= 0),
    cleanup_evidence JSONB NOT NULL DEFAULT '{}' CHECK (
        cleanup_evidence = '{}'::JSONB
        OR _agent_sandbox_evidence_is_valid(cleanup_evidence)
    ),
    partial_effects_recorded_at TIMESTAMPTZ,
    cleanup_deadline_at TIMESTAMPTZ,
    ambiguity_evidence JSONB NOT NULL DEFAULT '{}' CHECK (
        jsonb_typeof(ambiguity_evidence) = 'object'
        AND pg_column_size(ambiguity_evidence) <= 65536
        AND _agent_sandbox_json_is_safe(ambiguity_evidence)
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (claim_worker_id IS NULL AND claim_token IS NULL AND lease_expires_at IS NULL)
        OR (claim_worker_id IS NOT NULL AND claim_token IS NOT NULL
            AND lease_expires_at IS NOT NULL)
    ),
    CHECK (
        (reconciliation_worker_id IS NULL AND reconciliation_token IS NULL
         AND reconciliation_lease_expires_at IS NULL)
        OR (reconciliation_worker_id IS NOT NULL
            AND reconciliation_token IS NOT NULL
            AND reconciliation_lease_expires_at IS NOT NULL)
    ),
    CHECK (started_at IS NULL OR starting_at IS NOT NULL),
    CHECK (cancel_accepted_at IS NULL OR cancel_requested_at IS NOT NULL),
    CHECK (cancel_confirmed_at IS NULL OR cancel_accepted_at IS NOT NULL),
    CHECK (
        (status IN ('succeeded','failed','timed_out','cancelled')
         AND terminal_at IS NOT NULL AND terminal_reason IS NOT NULL)
        OR (status NOT IN ('succeeded','failed','timed_out','cancelled')
            AND terminal_at IS NULL)
    ),
    CHECK (status <> 'cancelled' OR cancel_confirmed_at IS NOT NULL),
    CHECK (
        status <> 'succeeded' OR (
            materialization_status = 'completed'
            AND cleanup_status IN ('not_required','completed')
            AND receipt_hash IS NOT NULL
        )
    ),
    CHECK (status <> 'unknown' OR ambiguity_evidence <> '{}'::JSONB),
    CHECK (
        cleanup_deadline_at IS NULL
        OR (
            partial_effects_recorded_at IS NOT NULL
            AND cleanup_deadline_at
                <= partial_effects_recorded_at + interval '24 hours'
        )
    )
);

CREATE INDEX idx_agent_sandbox_jobs_queue
    ON agent_sandbox_jobs(queued_at, id) WHERE status = 'queued';
CREATE INDEX idx_agent_sandbox_jobs_lease
    ON agent_sandbox_jobs(lease_expires_at, id)
    WHERE status IN ('claimed','starting','running','cancel_requested');
CREATE INDEX idx_agent_sandbox_jobs_reconcile
    ON agent_sandbox_jobs(updated_at, id) WHERE status = 'unknown';
CREATE INDEX idx_agent_sandbox_jobs_cleanup
    ON agent_sandbox_jobs(cleanup_deadline_at, id)
    WHERE cleanup_status IN ('pending','failed','unknown');
CREATE INDEX idx_agent_sandbox_jobs_run
    ON agent_sandbox_jobs(run_id, created_at, id);
CREATE INDEX idx_agent_sandbox_jobs_scope
    ON agent_sandbox_jobs(org_id, user_id, created_at, id);

ALTER TABLE agent_sandbox_jobs ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_sandbox_jobs_owner_all ON agent_sandbox_jobs
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_sandbox_jobs FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE agent_sandbox_jobs
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sandbox_worker, everydayai_sync, everydayai;
REVOKE ALL ON FUNCTION
    _agent_sandbox_json_is_safe(JSONB),
    _agent_sandbox_summary_is_safe(TEXT),
    _agent_sandbox_manifest_is_valid(JSONB,TEXT),
    _agent_sandbox_evidence_is_valid(JSONB),
    _agent_sandbox_receipt_is_valid(JSONB),
    _agent_sandbox_receipt_hash(JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sandbox_worker, everydayai_sync, everydayai;

RESET ROLE;
