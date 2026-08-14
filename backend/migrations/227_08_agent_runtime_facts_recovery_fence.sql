-- 227.08: Runtime facts and recovery kill-epoch fencing.
-- Additive trigger guards; 227.02 through 227.07 remain immutable.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_runtime_assert_facts_epoch(
    p_attempt_id UUID, p_execution_token UUID, p_org_id UUID,
    p_provider TEXT, p_provider_revision TEXT, p_mode TEXT
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE a agent_action_attempts%ROWTYPE; x agent_actions%ROWTYPE;
    f agent_runtime_owner_fences%ROWTYPE; g agent_runtime_tenant_gate_controls%ROWTYPE;
    v_provider TEXT; v_capability TEXT; v_provider_epoch BIGINT:=0; v_capability_epoch BIGINT:=0;
BEGIN
    IF p_mode NOT IN ('new','post_dispatch','reconcile') THEN
        RAISE EXCEPTION 'RUNTIME_FACT_FENCE_MODE_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR SHARE;
    IF NOT FOUND OR a.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'RUNTIME_FACT_TENANT_SCOPE_MISMATCH' USING ERRCODE='42501';
    END IF;
    IF p_execution_token IS NULL THEN p_execution_token:=a.execution_token; END IF;
    IF a.execution_token IS DISTINCT FROM p_execution_token THEN
        RAISE EXCEPTION 'RUNTIME_EXECUTION_TOKEN_FENCED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO f FROM agent_runtime_owner_fences
     WHERE owner_kind='attempt' AND owner_id=a.id AND execution_token=a.execution_token FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_FENCE_MISSING' USING ERRCODE='42501';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'agent-runtime-kill-gate:'||p_org_id::TEXT||':tenant:tenant',0));
    SELECT * INTO g FROM agent_runtime_tenant_gate_controls
     WHERE org_id=p_org_id AND gate_scope='tenant' AND scope_key='tenant';
    IF p_mode='new' AND FOUND AND (g.dispatch_blocked OR f.tenant_kill_epoch<>g.kill_epoch) THEN
        RAISE EXCEPTION 'RUNTIME_KILL_EPOCH_FENCED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO x FROM agent_actions WHERE id=a.action_id;
    v_provider:=NULLIF(btrim(COALESCE(p_provider,x.policy_snapshot->>'provider',x.policy_snapshot->>'provider_name')),'');
    v_capability:=NULLIF(btrim(COALESCE(x.policy_snapshot->>'capability',x.policy_snapshot->>'capability_name')),'');
    IF v_capability IS NULL AND jsonb_typeof(x.policy_snapshot->'capability_requirements')='array' THEN
        v_capability:=NULLIF(btrim(x.policy_snapshot->'capability_requirements'->>0),'');
    END IF;
    IF v_provider IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'agent-runtime-kill-gate:'||p_org_id::TEXT||':provider:'||v_provider,0));
        SELECT * INTO g FROM agent_runtime_tenant_gate_controls
         WHERE org_id=p_org_id AND gate_scope='provider' AND scope_key=v_provider;
        IF FOUND THEN
            v_provider_epoch:=g.kill_epoch;
            IF p_mode='new' AND g.dispatch_blocked THEN
                RAISE EXCEPTION 'RUNTIME_PROVIDER_KILL_FENCED' USING ERRCODE='42501';
            END IF;
        END IF;
    END IF;
    IF v_capability IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'agent-runtime-kill-gate:'||p_org_id::TEXT||':capability:'||v_capability,0));
        SELECT * INTO g FROM agent_runtime_tenant_gate_controls
         WHERE org_id=p_org_id AND gate_scope='capability' AND scope_key=v_capability;
        IF FOUND THEN
            v_capability_epoch:=g.kill_epoch;
            IF p_mode='new' AND g.dispatch_blocked THEN
                RAISE EXCEPTION 'RUNTIME_CAPABILITY_KILL_FENCED' USING ERRCODE='42501';
            END IF;
        END IF;
    END IF;
    IF p_mode='new' AND f.provider_kill_epoch<>v_provider_epoch THEN
        RAISE EXCEPTION 'RUNTIME_PROVIDER_KILL_FENCED' USING ERRCODE='42501';
    END IF;
    IF p_mode='new' AND f.capability_kill_epoch<>v_capability_epoch THEN
        RAISE EXCEPTION 'RUNTIME_CAPABILITY_KILL_FENCED' USING ERRCODE='42501';
    END IF;
    IF p_provider_revision IS NOT NULL AND f.provider_revision IS NOT NULL
       AND f.provider_revision IS DISTINCT FROM btrim(p_provider_revision) THEN
        RAISE EXCEPTION 'RUNTIME_REVISION_FENCED' USING ERRCODE='42501';
    END IF;
END $$;

CREATE FUNCTION _agent_runtime_provider_facts_epoch_trigger()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
    PERFORM _agent_runtime_assert_facts_epoch(
        NEW.attempt_id,NEW.execution_token,NEW.org_id,NEW.provider,NEW.provider_revision,
        CASE WHEN TG_OP='INSERT' THEN 'new' ELSE 'post_dispatch' END);
    RETURN NEW;
END $$;
CREATE TRIGGER agent_runtime_provider_facts_epoch_fence
    BEFORE INSERT OR UPDATE ON agent_runtime_provider_submission_facts
    FOR EACH ROW EXECUTE FUNCTION _agent_runtime_provider_facts_epoch_trigger();

CREATE FUNCTION _agent_runtime_scheduler_facts_epoch_trigger()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
    PERFORM _agent_runtime_assert_facts_epoch(
        NEW.attempt_id,NEW.execution_token,NEW.org_id,NULL,NULL,
        CASE WHEN TG_OP='INSERT' OR (NEW.operation<>'cancel' AND NEW.state<>'cancel_requested')
             THEN 'new' ELSE 'post_dispatch' END);
    RETURN NEW;
END $$;
CREATE TRIGGER agent_runtime_scheduler_facts_epoch_fence
    BEFORE INSERT OR UPDATE ON agent_runtime_scheduler_cas_facts
    FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduler_facts_epoch_trigger();

CREATE FUNCTION _agent_runtime_sandbox_epoch_trigger()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE v_token UUID;
BEGIN
    SELECT execution_token INTO v_token FROM agent_action_attempts WHERE id=NEW.attempt_id;
    PERFORM _agent_runtime_assert_facts_epoch(
        NEW.attempt_id,v_token,NEW.org_id,NULL,NULL,
        CASE WHEN TG_OP='INSERT' OR NEW.status IN
                 ('prepared','queued','claimed','starting','running','succeeded','failed','timed_out')
             THEN 'new' ELSE 'post_dispatch' END);
    RETURN NEW;
END $$;
CREATE TRIGGER agent_runtime_sandbox_epoch_fence
    BEFORE INSERT OR UPDATE ON agent_sandbox_jobs
    FOR EACH ROW EXECUTE FUNCTION _agent_runtime_sandbox_epoch_trigger();

CREATE FUNCTION _agent_runtime_child_run_epoch_trigger()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE v_attempt agent_action_attempts%ROWTYPE;
BEGIN
    IF NEW.parent_action_id IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE action_id=NEW.parent_action_id AND org_id=NEW.org_id
     ORDER BY CASE WHEN status IN ('dispatching','accepted','unknown') THEN 0 ELSE 1 END, updated_at DESC
     LIMIT 1;
    PERFORM _agent_runtime_assert_facts_epoch(
        v_attempt.id,v_attempt.execution_token,NEW.org_id,NULL,NULL,
        CASE WHEN TG_OP='INSERT' OR NEW.status IN ('queued','running','waiting_actions','completed','failed')
             THEN 'new' ELSE 'post_dispatch' END);
    RETURN NEW;
END $$;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_attribute
       WHERE attrelid='agent_runs'::REGCLASS AND attname='parent_action_id'
         AND NOT attisdropped) THEN
        EXECUTE 'CREATE TRIGGER agent_runtime_child_run_epoch_fence
          BEFORE INSERT OR UPDATE ON agent_runs
          FOR EACH ROW WHEN (NEW.parent_action_id IS NOT NULL)
          EXECUTE FUNCTION _agent_runtime_child_run_epoch_trigger()';
    END IF;
END $$;

REVOKE ALL ON FUNCTION _agent_runtime_assert_facts_epoch(UUID,UUID,UUID,TEXT,TEXT,TEXT),
    _agent_runtime_provider_facts_epoch_trigger(),_agent_runtime_scheduler_facts_epoch_trigger(),
    _agent_runtime_sandbox_epoch_trigger(),_agent_runtime_child_run_epoch_trigger()
    FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai;

RESET ROLE;
