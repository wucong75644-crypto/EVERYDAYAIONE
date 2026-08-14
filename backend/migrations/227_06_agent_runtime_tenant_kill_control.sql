-- 227.06: tenant/provider/capability kill controls, owner fences and audit.
-- Additive only. This lane does not wire ingress, claim or dispatch gates.
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_tenant_gate_controls(
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    gate_scope TEXT NOT NULL CHECK (gate_scope IN ('tenant','provider','capability')),
    scope_key TEXT NOT NULL CHECK (length(btrim(scope_key)) BETWEEN 1 AND 200),
    ingress_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    claim_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    dispatch_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    kill_epoch BIGINT NOT NULL DEFAULT 0 CHECK (kill_epoch >= 0),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 500),
    updated_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (org_id, gate_scope, scope_key),
    CHECK ((gate_scope = 'tenant' AND scope_key = 'tenant')
        OR (gate_scope IN ('provider','capability') AND scope_key <> 'tenant')),
    CHECK (gate_scope = 'tenant'
        OR (NOT ingress_blocked AND NOT claim_blocked))
);

CREATE TABLE agent_runtime_owner_fences(
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_kind TEXT NOT NULL CHECK (owner_kind IN
        ('run','action','attempt','scheduler','sandbox')),
    owner_id UUID NOT NULL,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    execution_token UUID NOT NULL,
    tenant_kill_epoch BIGINT NOT NULL CHECK (tenant_kill_epoch >= 0),
    provider_kill_epoch BIGINT NOT NULL DEFAULT 0 CHECK (provider_kill_epoch >= 0),
    capability_kill_epoch BIGINT NOT NULL DEFAULT 0 CHECK (capability_kill_epoch >= 0),
    provider_revision TEXT CHECK (provider_revision IS NULL OR length(btrim(provider_revision)) BETWEEN 1 AND 200),
    capability_revision TEXT CHECK (capability_revision IS NULL OR length(btrim(capability_revision)) BETWEEN 1 AND 200),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    lease_expires_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('active','fenced','released','reconcile_only')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owner_kind, owner_id, execution_token)
);
CREATE INDEX idx_agent_runtime_owner_fences_scope
    ON agent_runtime_owner_fences(org_id, owner_kind, status, updated_at);

CREATE TABLE agent_runtime_kill_audit(
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL UNIQUE,
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    gate_scope TEXT NOT NULL CHECK (gate_scope IN ('tenant','provider','capability')),
    scope_key TEXT NOT NULL CHECK (length(btrim(scope_key)) BETWEEN 1 AND 200),
    previous_epoch BIGINT NOT NULL CHECK (previous_epoch >= 0),
    new_epoch BIGINT NOT NULL CHECK (new_epoch >= previous_epoch),
    previous_state JSONB NOT NULL CHECK (jsonb_typeof(previous_state) = 'object'),
    new_state JSONB NOT NULL CHECK (jsonb_typeof(new_state) = 'object'),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 500),
    impact_summary JSONB NOT NULL CHECK (jsonb_typeof(impact_summary) = 'object'),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((gate_scope = 'tenant' AND scope_key = 'tenant')
        OR (gate_scope IN ('provider','capability') AND scope_key <> 'tenant')),
    CHECK (impact_summary::TEXT !~* '(secret|token|credential|password|payload|prompt|path|stack|request)')
);

CREATE FUNCTION _agent_runtime_kill_audit_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'AGENT_RUNTIME_KILL_AUDIT_IMMUTABLE' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER agent_runtime_kill_audit_immutable
    BEFORE UPDATE OR DELETE ON agent_runtime_kill_audit
    FOR EACH ROW EXECUTE FUNCTION _agent_runtime_kill_audit_immutable();

ALTER TABLE agent_runtime_tenant_gate_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_tenant_gate_controls FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_owner_fences ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_owner_fences FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_kill_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_kill_audit FORCE ROW LEVEL SECURITY;

CREATE POLICY agent_runtime_tenant_gate_owner_all
    ON agent_runtime_tenant_gate_controls FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_runtime_owner_fences_owner_all
    ON agent_runtime_owner_fences FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_runtime_kill_audit_owner_all
    ON agent_runtime_kill_audit FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);

CREATE FUNCTION set_agent_runtime_tenant_gate(
    p_request_id UUID, p_org_id UUID, p_gate_scope TEXT, p_scope_key TEXT,
    p_blocked BOOLEAN, p_expected_state_version BIGINT, p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_actor UUID := tenant_actor_user_id();
    v_org UUID := tenant_org_id();
    v_gate agent_runtime_tenant_gate_controls%ROWTYPE;
    v_prior agent_runtime_kill_audit%ROWTYPE;
    v_hash TEXT;
    v_previous JSONB;
    v_new JSONB;
    v_epoch BIGINT;
    v_state_version BIGINT;
    v_request JSONB;
BEGIN
    IF session_user <> 'everydayai_runtime_admin'
       OR current_setting('app.access_kind', true) <> 'runtime_admin'
       OR NOT tenant_platform_admin() OR v_actor IS NULL
       OR v_org IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE = '42501';
    END IF;
    IF p_request_id IS NULL OR p_org_id IS NULL
       OR p_gate_scope NOT IN ('tenant','provider','capability')
       OR NULLIF(btrim(p_scope_key), '') IS NULL
       OR length(btrim(p_scope_key)) > 200
       OR (p_gate_scope = 'tenant' AND btrim(p_scope_key) <> 'tenant')
       OR (p_gate_scope <> 'tenant' AND btrim(p_scope_key) = 'tenant')
       OR p_expected_state_version < 0
       OR length(btrim(p_reason)) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_TENANT_GATE_INVALID' USING ERRCODE = '22023';
    END IF;
    v_request := jsonb_build_object(
        'org_id', p_org_id, 'gate_scope', p_gate_scope,
        'scope_key', btrim(p_scope_key), 'blocked', p_blocked,
        'expected_state_version', p_expected_state_version);
    v_hash := encode(sha256(convert_to(v_request::TEXT, 'UTF8')), 'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'agent-runtime-kill-request:' || p_request_id::TEXT, 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'agent-runtime-kill-gate:' || p_org_id::TEXT || ':' ||
        p_gate_scope || ':' || btrim(p_scope_key), 0));
    SELECT * INTO v_prior FROM agent_runtime_kill_audit
     WHERE request_id = p_request_id;
    IF FOUND THEN
        IF v_prior.actor_user_id IS DISTINCT FROM v_actor
           OR v_prior.org_id IS DISTINCT FROM p_org_id
           OR v_prior.gate_scope IS DISTINCT FROM p_gate_scope
           OR v_prior.scope_key IS DISTINCT FROM btrim(p_scope_key)
           OR v_prior.reason IS DISTINCT FROM btrim(p_reason)
           OR v_prior.request_hash IS DISTINCT FROM v_hash THEN
            RETURN jsonb_build_object('outcome','idempotency_conflict');
        END IF;
        RETURN v_prior.new_state || jsonb_build_object(
            'outcome','already_applied', 'audit_id',v_prior.id);
    END IF;
    SELECT * INTO v_gate FROM agent_runtime_tenant_gate_controls
     WHERE org_id=p_org_id AND gate_scope=p_gate_scope
       AND scope_key=btrim(p_scope_key) FOR UPDATE;
    IF NOT FOUND THEN
        IF p_expected_state_version <> 0 THEN
            RETURN jsonb_build_object('outcome','stale_version','state_version',0);
        END IF;
        v_gate.org_id := p_org_id;
        v_gate.gate_scope := p_gate_scope;
        v_gate.scope_key := btrim(p_scope_key);
        v_gate.ingress_blocked := p_gate_scope='tenant' AND p_blocked;
        v_gate.claim_blocked := p_gate_scope='tenant' AND p_blocked;
        v_gate.dispatch_blocked := p_blocked;
        v_gate.kill_epoch := CASE WHEN p_blocked THEN 1 ELSE 0 END;
        v_gate.state_version := 0;
        v_gate.reason := btrim(p_reason);
        v_gate.updated_by := v_actor;
        INSERT INTO agent_runtime_tenant_gate_controls(
            org_id,gate_scope,scope_key,ingress_blocked,claim_blocked,
            dispatch_blocked,kill_epoch,state_version,reason,updated_by)
        VALUES(v_gate.org_id,v_gate.gate_scope,v_gate.scope_key,
            v_gate.ingress_blocked,v_gate.claim_blocked,v_gate.dispatch_blocked,
            v_gate.kill_epoch,1,v_gate.reason,v_gate.updated_by)
        RETURNING * INTO v_gate;
    ELSE
        IF v_gate.state_version <> p_expected_state_version THEN
            RETURN jsonb_build_object('outcome','stale_version',
                'state_version',v_gate.state_version);
        END IF;
        v_previous := jsonb_build_object(
            'blocked', v_gate.dispatch_blocked, 'ingress_blocked',v_gate.ingress_blocked,
            'claim_blocked',v_gate.claim_blocked, 'dispatch_blocked',v_gate.dispatch_blocked,
            'kill_epoch',v_gate.kill_epoch, 'state_version',v_gate.state_version);
        v_gate.ingress_blocked := p_gate_scope='tenant' AND p_blocked;
        v_gate.claim_blocked := p_gate_scope='tenant' AND p_blocked;
        v_gate.dispatch_blocked := p_blocked;
        v_gate.kill_epoch := v_gate.kill_epoch
            + CASE WHEN p_blocked
                AND (v_previous->>'blocked') IS DISTINCT FROM 'true'
              THEN 1 ELSE 0 END;
        UPDATE agent_runtime_tenant_gate_controls SET
            ingress_blocked=v_gate.ingress_blocked, claim_blocked=v_gate.claim_blocked,
            dispatch_blocked=v_gate.dispatch_blocked, kill_epoch=v_gate.kill_epoch,
            state_version=state_version+1, reason=btrim(p_reason), updated_by=v_actor,
            updated_at=clock_timestamp()
        WHERE org_id=v_gate.org_id AND gate_scope=v_gate.gate_scope
          AND scope_key=v_gate.scope_key
        RETURNING * INTO v_gate;
    END IF;
    v_previous := COALESCE(v_previous, '{}'::JSONB);
    v_new := jsonb_build_object(
        'blocked',v_gate.dispatch_blocked,'ingress_blocked',v_gate.ingress_blocked,
        'claim_blocked',v_gate.claim_blocked,'dispatch_blocked',v_gate.dispatch_blocked,
        'kill_epoch',v_gate.kill_epoch,'state_version',v_gate.state_version);
    v_epoch := v_gate.kill_epoch;
    v_state_version := v_gate.state_version;
    INSERT INTO agent_runtime_kill_audit(
        request_id,actor_user_id,org_id,gate_scope,scope_key,previous_epoch,
        new_epoch,previous_state,new_state,reason,impact_summary,request_hash)
    VALUES(p_request_id,v_actor,p_org_id,p_gate_scope,btrim(p_scope_key),
        COALESCE((v_previous->>'kill_epoch')::BIGINT,0),v_epoch,v_previous,v_new,
        btrim(p_reason),jsonb_build_object('impact_type',
            CASE WHEN p_blocked THEN 'block' ELSE 'unblock' END,
            'scope',p_gate_scope,'scope_key',btrim(p_scope_key),
            'epoch',v_epoch),v_hash);
    RETURN jsonb_build_object('outcome','applied','org_id',p_org_id,
        'gate_scope',p_gate_scope,'scope_key',btrim(p_scope_key),
        'blocked',v_gate.dispatch_blocked,'kill_epoch',v_epoch,
        'state_version',v_state_version);
END;
$$;

CREATE FUNCTION get_agent_runtime_tenant_gate_status(p_org_id UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_actor UUID := tenant_actor_user_id(); v_org UUID := tenant_org_id();
BEGIN
    IF session_user <> 'everydayai_runtime_admin'
       OR current_setting('app.access_kind', true) <> 'runtime_admin'
       OR NOT tenant_platform_admin() OR v_actor IS NULL
       OR v_org IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE = '42501';
    END IF;
    RETURN jsonb_build_object(
        'outcome','found','org_id',p_org_id,
        'controls',(SELECT COALESCE(jsonb_agg(to_jsonb(g) ORDER BY g.gate_scope,g.scope_key),'[]'::JSONB)
            FROM agent_runtime_tenant_gate_controls g WHERE g.org_id=p_org_id),
        'accepted',(SELECT count(*) FROM agent_action_attempts a
            JOIN agent_actions x ON x.id=a.action_id
            WHERE x.org_id=p_org_id AND a.status='accepted'),
        'unknown',(SELECT count(*) FROM agent_action_attempts a
            JOIN agent_actions x ON x.id=a.action_id
            WHERE x.org_id=p_org_id AND a.status='unknown'),
        'reconcile_required',(SELECT count(*) FROM agent_runtime_provider_submission_facts f
            WHERE f.org_id=p_org_id AND f.state='reconcile_required'),
        'production_ready',FALSE,'production_enabled',FALSE);
END;
$$;

CREATE FUNCTION get_agent_runtime_owner_fence(
    p_owner_kind TEXT, p_owner_id UUID, p_execution_token UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_fence agent_runtime_owner_fences%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_fence FROM agent_runtime_owner_fences
     WHERE owner_kind=p_owner_kind AND owner_id=p_owner_id
       AND execution_token=p_execution_token;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    RETURN jsonb_build_object('outcome','found','fence',jsonb_build_object(
        'owner_kind',v_fence.owner_kind,'owner_id',v_fence.owner_id,
        'org_id',v_fence.org_id,'tenant_kill_epoch',v_fence.tenant_kill_epoch,
        'provider_kill_epoch',v_fence.provider_kill_epoch,
        'capability_kill_epoch',v_fence.capability_kill_epoch,
        'provider_revision',v_fence.provider_revision,
        'capability_revision',v_fence.capability_revision,
        'state_version',v_fence.state_version,'status',v_fence.status,
        'lease_expires_at',v_fence.lease_expires_at));
END;
$$;

REVOKE ALL ON TABLE agent_runtime_tenant_gate_controls,
    agent_runtime_owner_fences, agent_runtime_kill_audit
    FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
        everydayai_worker, everydayai_sync, everydayai,
        everydayai_agent_runtime_worker, everydayai_projection_worker,
        everydayai_authorization_worker, everydayai_sandbox_worker;
REVOKE ALL ON FUNCTION set_agent_runtime_tenant_gate(
    UUID,UUID,TEXT,TEXT,BOOLEAN,BIGINT,TEXT) FROM PUBLIC,
    everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_agent_runtime_worker,everydayai_projection_worker,
    everydayai_authorization_worker,everydayai_sandbox_worker;
REVOKE ALL ON FUNCTION get_agent_runtime_tenant_gate_status(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION get_agent_runtime_owner_fence(TEXT,UUID,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION set_agent_runtime_tenant_gate(
    UUID,UUID,TEXT,TEXT,BOOLEAN,BIGINT,TEXT) TO everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION get_agent_runtime_tenant_gate_status(UUID)
    TO everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION get_agent_runtime_owner_fence(TEXT,UUID,UUID)
    TO everydayai_agent_runtime_worker, everydayai_projection_worker,
       everydayai_authorization_worker, everydayai_sandbox_worker;

RESET ROLE;
