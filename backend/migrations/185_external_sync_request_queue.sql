-- 185: Backend 只提交外部同步请求，Sync 以租约和 fencing token 消费。

SET LOCAL ROLE everydayai_owner;

CREATE TABLE kuaimai_external_sync_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id),
    source TEXT NOT NULL CHECK (source IN ('thinktank', 'viperp')),
    sync_type TEXT NOT NULL CHECK (sync_type IN ('daily', 'manual', 'backfill')),
    start_date DATE,
    end_date DATE,
    dimension TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    execution_token UUID,
    lease_expires_at TIMESTAMPTZ,
    error_message TEXT,
    created_by UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_external_sync_request_claim
ON kuaimai_external_sync_requests(status, created_at);
CREATE UNIQUE INDEX uq_external_sync_request_active
ON kuaimai_external_sync_requests(org_id, source)
WHERE status IN ('queued', 'running');

ALTER TABLE kuaimai_external_sync_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE kuaimai_external_sync_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY external_sync_request_owner
ON kuaimai_external_sync_requests
FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY external_sync_request_legacy
ON kuaimai_external_sync_requests
FOR ALL TO everydayai
USING (session_user = 'everydayai')
WITH CHECK (session_user = 'everydayai');

CREATE OR REPLACE FUNCTION runtime_enqueue_external_sync(
    p_org_id UUID,
    p_source TEXT,
    p_sync_type TEXT,
    p_start_date DATE,
    p_end_date DATE,
    p_dimension TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_org_admin();
    v_org UUID := public.tenant_org_id();
    v_id UUID;
BEGIN
    IF p_org_id IS DISTINCT FROM v_org
       OR p_source NOT IN ('thinktank', 'viperp')
       OR p_sync_type NOT IN ('daily', 'manual', 'backfill')
       OR p_dimension NOT IN ('shop', 'sku', 'item', 'day', 'brand', 'distributor')
       OR (p_start_date IS NOT NULL AND p_end_date IS NOT NULL
           AND p_start_date > p_end_date) THEN
        RAISE EXCEPTION 'EXTERNAL_SYNC_REQUEST_INVALID'
            USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.configuration_entries entry
         WHERE entry.scope_kind = 'organization'
           AND entry.org_id = v_org
           AND entry.config_key IN (
               'kuaimai_external.' || p_source || '.cookie',
               'kuaimai_external.' || p_source || '.company_id'
           )
           AND entry.status = 'active'
         GROUP BY entry.org_id
        HAVING COUNT(*) = 2
    ) THEN
        RAISE EXCEPTION 'EXTERNAL_SYNC_CONFIGURATION_MISSING'
            USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.kuaimai_external_sync_requests (
        org_id, source, sync_type, start_date, end_date,
        dimension, created_by
    ) VALUES (
        v_org, p_source, p_sync_type, p_start_date, p_end_date,
        p_dimension, v_actor
    )
    ON CONFLICT (org_id, source)
        WHERE status IN ('queued', 'running')
    DO UPDATE SET updated_at = NOW()
    RETURNING id INTO v_id;
    RETURN jsonb_build_object('queued', TRUE, 'request_id', v_id);
END;
$$;

CREATE OR REPLACE FUNCTION sync_claim_external_sync(p_lease_seconds INTEGER)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_request public.kuaimai_external_sync_requests%ROWTYPE;
    v_token UUID := gen_random_uuid();
BEGIN
    IF session_user <> 'everydayai_sync'
       OR p_lease_seconds < 30 OR p_lease_seconds > 3600 THEN
        RAISE EXCEPTION 'EXTERNAL_SYNC_CLAIM_DENIED'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.kuaimai_external_sync_requests
       SET status = 'failed',
           error_message = 'EXTERNAL_SYNC_ATTEMPTS_EXHAUSTED',
           execution_token = NULL,
           lease_expires_at = NULL,
           finished_at = NOW(),
           updated_at = NOW()
     WHERE status = 'running'
       AND lease_expires_at < NOW()
       AND attempt_count >= 3;
    SELECT * INTO v_request
      FROM public.kuaimai_external_sync_requests request
     WHERE request.status = 'queued'
        OR (
            request.status = 'running'
            AND request.lease_expires_at < NOW()
            AND request.attempt_count < 3
        )
     ORDER BY request.created_at
     FOR UPDATE SKIP LOCKED
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    UPDATE public.kuaimai_external_sync_requests
       SET status = 'running',
           attempt_count = attempt_count + 1,
           execution_token = v_token,
           lease_expires_at = NOW() + make_interval(secs => p_lease_seconds),
           started_at = COALESCE(started_at, NOW()),
           updated_at = NOW()
     WHERE id = v_request.id
    RETURNING * INTO v_request;
    RETURN to_jsonb(v_request);
END;
$$;

CREATE OR REPLACE FUNCTION sync_finish_external_sync(
    p_request_id UUID,
    p_execution_token UUID,
    p_success BOOLEAN,
    p_error_message TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_sync' THEN
        RAISE EXCEPTION 'EXTERNAL_SYNC_FINISH_DENIED'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.kuaimai_external_sync_requests
       SET status = CASE WHEN p_success THEN 'completed' ELSE 'failed' END,
           error_message = CASE
               WHEN p_success THEN NULL ELSE LEFT(p_error_message, 1000)
           END,
           execution_token = NULL,
           lease_expires_at = NULL,
           finished_at = NOW(),
           updated_at = NOW()
     WHERE id = p_request_id
       AND execution_token = p_execution_token
       AND status = 'running'
       AND lease_expires_at > NOW();
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION sync_renew_external_sync(
    p_request_id UUID,
    p_execution_token UUID,
    p_lease_seconds INTEGER
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_sync'
       OR p_lease_seconds < 30 OR p_lease_seconds > 3600 THEN
        RAISE EXCEPTION 'EXTERNAL_SYNC_RENEW_DENIED'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.kuaimai_external_sync_requests
       SET lease_expires_at = NOW() + make_interval(secs => p_lease_seconds),
           updated_at = NOW()
     WHERE id = p_request_id
       AND execution_token = p_execution_token
       AND status = 'running'
       AND lease_expires_at > NOW();
    RETURN FOUND;
END;
$$;

REVOKE ALL ON TABLE kuaimai_external_sync_requests
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
REVOKE ALL ON FUNCTION runtime_enqueue_external_sync(
    UUID, TEXT, TEXT, DATE, DATE, TEXT
), sync_claim_external_sync(INTEGER),
   sync_finish_external_sync(UUID, UUID, BOOLEAN, TEXT),
   sync_renew_external_sync(UUID, UUID, INTEGER)
FROM PUBLIC, everydayai, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION runtime_enqueue_external_sync(
    UUID, TEXT, TEXT, DATE, DATE, TEXT
) TO everydayai_runtime;
GRANT EXECUTE ON FUNCTION sync_claim_external_sync(INTEGER),
    sync_finish_external_sync(UUID, UUID, BOOLEAN, TEXT),
    sync_renew_external_sync(UUID, UUID, INTEGER)
TO everydayai_sync;

RESET ROLE;
