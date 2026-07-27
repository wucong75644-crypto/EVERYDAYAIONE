-- 220_25: Authorization resolution, recovery, cancellation, and reconcile entry.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE agent_interactions ADD COLUMN recovery_worker_id TEXT, ADD COLUMN recovery_token UUID UNIQUE, ADD COLUMN recovery_lease_expires_at TIMESTAMPTZ, ADD CHECK ( (recovery_token IS NULL) = (recovery_lease_expires_at IS NULL) );
ALTER FUNCTION open_agent_authorization_interaction( UUID, BIGINT, JSONB, TEXT, INTEGER) RENAME TO _open_agent_authorization_interaction_220_22;
ALTER FUNCTION resolve_agent_authorization_interaction( UUID, BIGINT, TEXT, TEXT, JSONB, TEXT, TEXT, INTEGER) RENAME TO _resolve_agent_authorization_interaction_220_22;
ALTER FUNCTION revoke_agent_authorization_grant(UUID) RENAME TO _revoke_agent_authorization_grant_220_22;
ALTER FUNCTION _agent_action_dispatch_snapshot(agent_action_attempts) RENAME TO _agent_action_dispatch_snapshot_220_04;
ALTER FUNCTION claim_next_agent_action_reconciliation(TEXT, INTEGER, INTEGER) RENAME TO _claim_next_agent_action_reconciliation_220_04;
REVOKE ALL ON FUNCTION _open_agent_authorization_interaction_220_22( UUID, BIGINT, JSONB, TEXT, INTEGER), _resolve_agent_authorization_interaction_220_22( UUID, BIGINT, TEXT, TEXT, JSONB, TEXT, TEXT, INTEGER), _revoke_agent_authorization_grant_220_22(UUID), _agent_action_dispatch_snapshot_220_04(agent_action_attempts), _claim_next_agent_action_reconciliation_220_04( TEXT, INTEGER, INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker, everydayai_sync, everydayai;
CREATE FUNCTION _agent_action_dispatch_snapshot( p_attempt agent_action_attempts
) RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$ SELECT to_jsonb(p_attempt) || jsonb_build_object( 'action', to_jsonb(action) || jsonb_build_object( 'scope_kind', runtime_session.scope_kind, 'scope_id', runtime_session.scope_id, 'policy_receipt_id', action.policy_snapshot->>'dispatch_policy_receipt_id')) FROM agent_actions action JOIN agent_runtime_sessions runtime_session ON runtime_session.id = action.session_id WHERE action.id = p_attempt.action_id
$$;

CREATE FUNCTION open_agent_authorization_interaction( p_action_id UUID, p_expected_action_version BIGINT, p_prompt JSONB, p_prompt_hash TEXT, p_ttl_seconds INTEGER DEFAULT 900
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_action agent_actions%ROWTYPE;
v_run agent_runs%ROWTYPE;
v_interaction agent_interactions%ROWTYPE;
BEGIN PERFORM _assert_agent_runtime_actor(TRUE);
IF jsonb_typeof(p_prompt) IS DISTINCT FROM 'object' OR NOT _agent_action_json_is_safe(p_prompt) OR p_prompt_hash !~ '^[0-9a-f]{64}$' OR p_ttl_seconds NOT BETWEEN 30 AND 86400 THEN RAISE EXCEPTION 'AGENT_AUTHORIZATION_INVALID_INTERACTION' USING ERRCODE = '22023';
END IF;
SELECT * INTO v_action FROM agent_actions WHERE id = p_action_id;
IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found');
END IF;
PERFORM 1 FROM agent_runtime_sessions WHERE id = v_action.session_id FOR UPDATE;
SELECT * INTO v_run FROM agent_runs WHERE id = v_action.run_id FOR UPDATE;
SELECT * INTO v_action FROM agent_actions WHERE id = p_action_id FOR UPDATE;
SELECT * INTO v_interaction FROM agent_interactions WHERE action_id = p_action_id FOR UPDATE;
IF FOUND THEN IF v_interaction.prompt_hash IS DISTINCT FROM p_prompt_hash THEN RETURN jsonb_build_object('outcome', 'interaction_conflict');
END IF;
RETURN jsonb_build_object( 'outcome', CASE WHEN v_interaction.status = 'open' THEN 'already_open' ELSE v_interaction.status END, 'interaction', to_jsonb(v_interaction));
END IF;
IF v_action.state_version <> p_expected_action_version OR v_action.status <> 'awaiting_authorization' THEN RETURN jsonb_build_object('outcome', 'stale_version');
END IF;
INSERT INTO agent_interactions( action_id, session_id, run_id, org_id, user_id, prompt, prompt_hash, expires_at ) VALUES ( v_action.id, v_action.session_id, v_action.run_id, v_action.org_id, v_action.user_id, p_prompt, p_prompt_hash, clock_timestamp() + make_interval(secs => p_ttl_seconds) ) RETURNING * INTO v_interaction;
UPDATE agent_runs SET open_interaction_count = open_interaction_count + 1, status = 'waiting_interaction', execution_token = NULL, lease_expires_at = NULL, state_version = state_version + 1, updated_at = clock_timestamp() WHERE id = v_run.id;
PERFORM append_agent_runtime_event( v_action.session_id, 'interaction.opened', v_action.run_id, v_action.model_step_id, v_interaction.id, 'system', session_user, jsonb_build_object( 'interaction_id', v_interaction.id, 'action_id', v_action.id), ARRAY['web_runtime', 'audit']::TEXT[]);
RETURN jsonb_build_object( 'outcome', 'opened', 'interaction', to_jsonb(v_interaction));
END;
$$;

CREATE FUNCTION resolve_agent_authorization_interaction( p_interaction_id UUID, p_expected_version BIGINT, p_response TEXT, p_response_hash TEXT, p_effective_scope JSONB, p_grant_kind TEXT DEFAULT 'action', p_workflow_key TEXT DEFAULT NULL, p_ttl_seconds INTEGER DEFAULT 900
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_interaction agent_interactions%ROWTYPE;
v_action agent_actions%ROWTYPE;
v_run agent_runs%ROWTYPE;
v_grant agent_authorization_grants%ROWTYPE;
BEGIN PERFORM _assert_agent_runtime_actor(FALSE);
IF p_response NOT IN ('approve', 'deny') OR p_response_hash !~ '^[0-9a-f]{64}$' OR jsonb_typeof(p_effective_scope) IS DISTINCT FROM 'object' OR NOT _agent_action_json_is_safe(p_effective_scope) OR p_grant_kind NOT IN ('action', 'workflow') OR p_ttl_seconds NOT BETWEEN 30 AND 86400 OR (p_grant_kind = 'workflow' AND NULLIF(btrim(p_workflow_key), '') IS NULL) OR (p_grant_kind = 'action' AND p_workflow_key IS NOT NULL) THEN RAISE EXCEPTION 'AGENT_AUTHORIZATION_INVALID_RESOLUTION' USING ERRCODE = '22023';
END IF;
SELECT * INTO v_interaction FROM agent_interactions WHERE id = p_interaction_id;
IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found');
END IF;
SELECT * INTO v_action FROM agent_actions WHERE id = v_interaction.action_id;
PERFORM 1 FROM agent_runtime_sessions WHERE id = v_action.session_id FOR UPDATE;
SELECT * INTO v_run FROM agent_runs WHERE id = v_action.run_id FOR UPDATE;
SELECT * INTO v_action FROM agent_actions WHERE id = v_action.id FOR UPDATE;
PERFORM 1 FROM agent_action_attempts WHERE action_id = v_action.id ORDER BY id FOR UPDATE;
SELECT * INTO v_interaction FROM agent_interactions WHERE id = p_interaction_id FOR UPDATE;
IF tenant_org_id() IS DISTINCT FROM v_action.org_id OR NOT EXISTS ( SELECT 1 FROM agent_runtime_sessions runtime_session WHERE runtime_session.id = v_action.session_id AND ( (runtime_session.scope_kind = 'user' AND runtime_session.user_id = tenant_actor_user_id()) OR (runtime_session.scope_kind = 'channel' AND EXISTS ( SELECT 1 FROM org_members member WHERE member.org_id = runtime_session.org_id AND member.user_id = tenant_actor_user_id() AND member.status = 'active' )) ) ) THEN RAISE EXCEPTION 'AGENT_AUTHORIZATION_SCOPE_MISMATCH' USING ERRCODE = '42501';
END IF;
IF v_interaction.status = 'resolved' THEN IF v_interaction.response_hash IS DISTINCT FROM p_response_hash THEN RETURN jsonb_build_object('outcome', 'resolution_conflict');
END IF;
SELECT * INTO v_grant FROM agent_authorization_grants WHERE interaction_id = v_interaction.id;
RETURN jsonb_build_object( 'outcome', 'already_resolved', 'interaction', to_jsonb(v_interaction), 'grant', CASE WHEN v_grant.id IS NULL THEN NULL ELSE to_jsonb(v_grant) END);
END IF;
IF v_interaction.status <> 'open' OR v_interaction.state_version <> p_expected_version THEN RETURN jsonb_build_object('outcome', 'stale_version');
END IF;
IF v_interaction.expires_at <= clock_timestamp() THEN UPDATE agent_interactions SET status = 'expired', resolved_at = clock_timestamp(), state_version = state_version + 1, updated_at = clock_timestamp() WHERE id = v_interaction.id;
UPDATE agent_runs SET open_interaction_count = open_interaction_count - 1 WHERE id = v_run.id;
PERFORM _close_agent_authorization_action( v_action.id, 'authorization_expired');
RETURN jsonb_build_object('outcome', 'expired');
END IF;
UPDATE agent_interactions SET status = 'resolved', response = p_response, response_hash = p_response_hash, resolved_at = clock_timestamp(), state_version = state_version + 1, updated_at = clock_timestamp() WHERE id = v_interaction.id RETURNING * INTO v_interaction;
IF v_run.open_interaction_count <= 0 THEN RAISE EXCEPTION 'AGENT_INTERACTION_COUNT_UNDERFLOW' USING ERRCODE = '55000';
END IF;
UPDATE agent_runs SET open_interaction_count = open_interaction_count - 1, state_version = state_version + 1, updated_at = clock_timestamp() WHERE id = v_run.id;
IF p_response = 'approve' THEN INSERT INTO agent_authorization_grants( session_id, run_id, action_id, interaction_id, org_id, user_id, grant_kind, workflow_key, arguments_hash, effective_scope, expires_at ) VALUES ( v_action.session_id, v_action.run_id, CASE WHEN p_grant_kind = 'action' THEN v_action.id END, v_interaction.id, v_action.org_id, v_action.user_id, p_grant_kind, p_workflow_key, CASE WHEN p_grant_kind = 'action' THEN v_action.arguments_hash END, p_effective_scope, clock_timestamp() + make_interval(secs => p_ttl_seconds) ) RETURNING * INTO v_grant;
PERFORM _recompute_agent_run_wait_state(v_run.id);
ELSE PERFORM _close_agent_authorization_action( v_action.id, 'authorization_denied');
END IF;
PERFORM append_agent_runtime_event( v_action.session_id, 'interaction.resolved', v_action.run_id, v_action.model_step_id, v_interaction.id, 'user', session_user, jsonb_build_object( 'interaction_id', v_interaction.id, 'action_id', v_action.id, 'response', p_response), ARRAY['web_runtime', 'audit']::TEXT[]);
RETURN jsonb_build_object( 'outcome', 'resolved', 'interaction', to_jsonb(v_interaction), 'grant', CASE WHEN v_grant.id IS NULL THEN NULL ELSE to_jsonb(v_grant) END);
END;
$$;

CREATE FUNCTION claim_next_agent_authorization_recovery( p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_candidate RECORD;
v_interaction agent_interactions%ROWTYPE;
v_action agent_actions%ROWTYPE;
v_grant agent_authorization_grants%ROWTYPE;
v_token UUID;
BEGIN PERFORM _assert_agent_runtime_actor(TRUE);
IF NULLIF(btrim(p_worker_id), '') IS NULL OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN RAISE EXCEPTION 'AGENT_AUTHORIZATION_RECOVERY_INVALID' USING ERRCODE = '22023';
END IF;
FOR v_candidate IN
    SELECT interaction.id, action.id AS action_id,
           action.session_id, action.run_id
    FROM agent_interactions interaction
    JOIN agent_actions action ON action.id = interaction.action_id
    WHERE interaction.status = 'resolved'
      AND interaction.response = 'approve'
      AND action.status = 'awaiting_authorization'
      AND (
          interaction.recovery_token IS NULL
          OR interaction.recovery_lease_expires_at <= clock_timestamp()
      )
    ORDER BY interaction.updated_at, interaction.id
    LIMIT 100
LOOP
    PERFORM 1 FROM agent_runtime_sessions
    WHERE id = v_candidate.session_id FOR UPDATE;
    PERFORM 1 FROM agent_runs
    WHERE id = v_candidate.run_id FOR UPDATE;
    SELECT * INTO v_action FROM agent_actions
    WHERE id = v_candidate.action_id FOR UPDATE;
    PERFORM 1 FROM agent_action_attempts
    WHERE action_id = v_action.id ORDER BY id FOR UPDATE;
    SELECT * INTO v_interaction FROM agent_interactions
    WHERE id = v_candidate.id FOR UPDATE;
    IF v_interaction.status <> 'resolved'
       OR v_interaction.response <> 'approve'
       OR v_action.status <> 'awaiting_authorization'
       OR (
           v_interaction.recovery_token IS NOT NULL
           AND v_interaction.recovery_lease_expires_at > clock_timestamp()
       )
    THEN
        CONTINUE;
    END IF;
    SELECT * INTO v_grant FROM agent_authorization_grants
    WHERE interaction_id = v_interaction.id FOR UPDATE;
    IF NOT FOUND
       OR v_grant.status <> 'active'
       OR v_grant.expires_at <= clock_timestamp()
    THEN
        PERFORM _close_agent_authorization_action(
            v_action.id, 'authorization_revoked'
        );
        RETURN jsonb_build_object('outcome', 'grant_invalid');
    END IF;
    v_token := gen_random_uuid();
    UPDATE agent_interactions
    SET recovery_worker_id = btrim(p_worker_id),
        recovery_token = v_token,
        recovery_lease_expires_at =
            clock_timestamp() + make_interval(secs => p_lease_seconds),
        state_version = state_version + 1,
        updated_at = clock_timestamp()
    WHERE id = v_interaction.id
    RETURNING * INTO v_interaction;
    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'interaction_id', v_interaction.id,
        'action', to_jsonb(v_action),
        'grant', to_jsonb(v_grant),
        'recovery_token', v_token,
        'state_version', v_interaction.state_version,
        'lease_expires_at', v_interaction.recovery_lease_expires_at
    );
END LOOP;
RETURN jsonb_build_object('outcome', 'not_found');
END;
$$;

CREATE FUNCTION renew_agent_authorization_recovery( p_interaction_id UUID, p_recovery_token UUID, p_expected_version BIGINT, p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_interaction agent_interactions%ROWTYPE;
BEGIN PERFORM _assert_agent_runtime_actor(TRUE);
SELECT * INTO v_interaction FROM agent_interactions WHERE id = p_interaction_id FOR UPDATE;
IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found');
END IF;
IF v_interaction.recovery_token IS DISTINCT FROM p_recovery_token THEN RETURN jsonb_build_object('outcome', 'ownership_lost');
END IF;
IF v_interaction.recovery_lease_expires_at <= clock_timestamp() OR v_interaction.state_version <> p_expected_version OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN RETURN jsonb_build_object('outcome', 'stale_version');
END IF;
UPDATE agent_interactions SET recovery_lease_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds), state_version = state_version + 1, updated_at = clock_timestamp() WHERE id = p_interaction_id RETURNING * INTO v_interaction;
RETURN jsonb_build_object( 'outcome', 'renewed', 'state_version', v_interaction.state_version, 'lease_expires_at', v_interaction.recovery_lease_expires_at);
END;
$$;

CREATE FUNCTION activate_agent_authorized_action( p_action_id UUID, p_expected_action_version BIGINT, p_interaction_id UUID, p_recovery_token UUID, p_expected_interaction_version BIGINT, p_policy_receipt_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_action agent_actions%ROWTYPE;
v_run agent_runs%ROWTYPE;
v_interaction agent_interactions%ROWTYPE;
v_grant agent_authorization_grants%ROWTYPE;
v_receipt agent_policy_receipts%ROWTYPE;
BEGIN PERFORM _assert_agent_runtime_actor(TRUE);
SELECT * INTO v_action FROM agent_actions WHERE id = p_action_id;
IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found');
END IF;
PERFORM 1 FROM agent_runtime_sessions WHERE id = v_action.session_id FOR UPDATE;
SELECT * INTO v_run FROM agent_runs WHERE id = v_action.run_id FOR UPDATE;
SELECT * INTO v_action FROM agent_actions WHERE id = p_action_id FOR UPDATE;
PERFORM 1 FROM agent_action_attempts WHERE action_id = v_action.id ORDER BY id FOR UPDATE;
SELECT * INTO v_interaction FROM agent_interactions WHERE id = p_interaction_id FOR UPDATE;
SELECT * INTO v_grant FROM agent_authorization_grants WHERE interaction_id = v_interaction.id FOR UPDATE;
PERFORM 1 FROM agent_authorization_grant_uses WHERE action_id = v_action.id FOR UPDATE;
SELECT * INTO v_receipt FROM agent_policy_receipts WHERE id = p_policy_receipt_id FOR UPDATE;
IF v_interaction.recovery_token IS DISTINCT FROM p_recovery_token THEN RETURN jsonb_build_object('outcome', 'ownership_lost');
END IF;
IF v_interaction.recovery_lease_expires_at <= clock_timestamp() OR v_interaction.state_version <> p_expected_interaction_version OR v_action.state_version <> p_expected_action_version OR v_action.status <> 'awaiting_authorization' THEN RETURN jsonb_build_object('outcome', 'stale_version');
END IF;
IF v_grant.status <> 'active' OR v_grant.expires_at <= clock_timestamp() OR v_receipt.action_id <> v_action.id OR v_receipt.grant_id <> v_grant.id OR v_receipt.decision <> 'allow' OR v_receipt.expires_at <= clock_timestamp() OR v_receipt.arguments_hash <> v_action.arguments_hash THEN RETURN jsonb_build_object('outcome', 'authorization_invalid');
END IF;
UPDATE agent_actions SET status = 'queued', policy_decision = 'preauthorized', policy_snapshot = policy_snapshot || jsonb_build_object( 'authorization_source', 'persisted_interaction', 'dispatch_policy_receipt_id', v_receipt.id, 'authorization_grant_id', v_grant.id), state_version = state_version + 1, updated_at = clock_timestamp() WHERE id = v_action.id RETURNING * INTO v_action;
UPDATE agent_interactions SET recovery_worker_id = NULL, recovery_token = NULL, recovery_lease_expires_at = NULL, state_version = state_version + 1, updated_at = clock_timestamp() WHERE id = v_interaction.id;
v_run := _recompute_agent_run_wait_state(v_run.id);
PERFORM append_agent_runtime_event( v_action.session_id, 'action.authorized', v_action.run_id, v_action.model_step_id, v_action.id, 'system', session_user, jsonb_build_object( 'action_id', v_action.id, 'policy_receipt_id', v_receipt.id), ARRAY['web_runtime', 'audit']::TEXT[]);
RETURN jsonb_build_object( 'outcome', 'activated', 'action_id', v_action.id, 'state_version', v_action.state_version, 'run_status', v_run.status);
END;
$$;

CREATE FUNCTION expire_agent_authorization_interaction( p_interaction_id UUID, p_expected_version BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_interaction agent_interactions%ROWTYPE;
v_action agent_actions%ROWTYPE;
v_run agent_runs%ROWTYPE;
BEGIN PERFORM _assert_agent_runtime_actor(TRUE);
SELECT * INTO v_interaction FROM agent_interactions WHERE id = p_interaction_id;
IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found');
END IF;
SELECT * INTO v_action FROM agent_actions WHERE id = v_interaction.action_id;
PERFORM 1 FROM agent_runtime_sessions WHERE id = v_action.session_id FOR UPDATE;
SELECT * INTO v_run FROM agent_runs WHERE id = v_action.run_id FOR UPDATE;
SELECT * INTO v_action FROM agent_actions WHERE id = v_action.id FOR UPDATE;
PERFORM 1 FROM agent_action_attempts WHERE action_id = v_action.id ORDER BY id FOR UPDATE;
SELECT * INTO v_interaction FROM agent_interactions WHERE id = p_interaction_id FOR UPDATE;
IF v_interaction.status <> 'open' OR v_interaction.state_version <> p_expected_version THEN RETURN jsonb_build_object('outcome', 'stale_version');
END IF;
IF v_interaction.expires_at > clock_timestamp() THEN RETURN jsonb_build_object('outcome', 'not_expired');
END IF;
UPDATE agent_interactions SET status = 'expired', resolved_at = clock_timestamp(), recovery_worker_id = NULL, recovery_token = NULL, recovery_lease_expires_at = NULL, state_version = state_version + 1, updated_at = clock_timestamp() WHERE id = v_interaction.id RETURNING * INTO v_interaction;
IF v_run.open_interaction_count <= 0 THEN RAISE EXCEPTION 'AGENT_INTERACTION_COUNT_UNDERFLOW' USING ERRCODE = '55000';
END IF;
UPDATE agent_runs SET open_interaction_count = open_interaction_count - 1 WHERE id = v_run.id;
PERFORM _close_agent_authorization_action( v_action.id, 'authorization_expired');
PERFORM append_agent_runtime_event( v_action.session_id, 'interaction.expired', v_action.run_id, v_action.model_step_id, v_interaction.id, 'system', session_user, jsonb_build_object( 'interaction_id', v_interaction.id, 'action_id', v_action.id), ARRAY['web_runtime', 'audit']::TEXT[]);
RETURN jsonb_build_object('outcome', 'expired');
END;
$$;

CREATE FUNCTION revoke_agent_authorization_grant(p_grant_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_grant agent_authorization_grants%ROWTYPE;
v_action agent_actions%ROWTYPE;
BEGIN PERFORM _assert_agent_runtime_actor(FALSE);
SELECT * INTO v_grant FROM agent_authorization_grants WHERE id = p_grant_id;
IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found');
END IF;
IF v_grant.action_id IS NOT NULL THEN SELECT * INTO v_action FROM agent_actions WHERE id = v_grant.action_id;
PERFORM 1 FROM agent_runtime_sessions WHERE id = v_action.session_id FOR UPDATE;
PERFORM 1 FROM agent_runs WHERE id = v_action.run_id FOR UPDATE;
PERFORM 1 FROM agent_actions WHERE id = v_action.id FOR UPDATE;
PERFORM 1 FROM agent_action_attempts WHERE action_id = v_action.id ORDER BY id FOR UPDATE;
PERFORM 1 FROM agent_interactions WHERE action_id = v_action.id FOR UPDATE;
END IF;
SELECT * INTO v_grant FROM agent_authorization_grants WHERE id = p_grant_id FOR UPDATE;
IF tenant_org_id() IS DISTINCT FROM v_grant.org_id OR NOT EXISTS ( SELECT 1 FROM agent_runtime_sessions runtime_session WHERE runtime_session.id = v_grant.session_id AND ( (runtime_session.scope_kind = 'user' AND runtime_session.user_id = tenant_actor_user_id()) OR (runtime_session.scope_kind = 'channel' AND EXISTS ( SELECT 1 FROM org_members member WHERE member.org_id = runtime_session.org_id AND member.user_id = tenant_actor_user_id() AND member.status = 'active' )) ) ) THEN RAISE EXCEPTION 'AGENT_AUTHORIZATION_SCOPE_MISMATCH' USING ERRCODE = '42501';
END IF;
IF v_grant.status = 'revoked' THEN RETURN jsonb_build_object( 'outcome', 'already_revoked', 'grant', to_jsonb(v_grant));
END IF;
UPDATE agent_authorization_grants SET status = 'revoked', revoked_at = clock_timestamp() WHERE id = p_grant_id RETURNING * INTO v_grant;
IF v_action.id IS NOT NULL AND NOT EXISTS ( SELECT 1 FROM agent_action_dispatch_intents WHERE action_id = v_action.id ) THEN PERFORM _close_agent_authorization_action( v_action.id, 'authorization_revoked');
END IF;
PERFORM append_agent_runtime_event( v_grant.session_id, 'authorization.revoked', v_grant.run_id, NULL, v_grant.id, 'user', session_user, jsonb_build_object('grant_id', v_grant.id), ARRAY['web_runtime', 'audit']::TEXT[]);
RETURN jsonb_build_object( 'outcome', 'revoked', 'grant', to_jsonb(v_grant));
END;
$$;

ALTER FUNCTION cancel_agent_run(UUID, BIGINT, TEXT) RENAME TO _cancel_agent_run_220_23;
REVOKE ALL ON FUNCTION _cancel_agent_run_220_23(UUID, BIGINT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker, everydayai_sync, everydayai;
CREATE FUNCTION cancel_agent_run( p_run_id UUID, p_expected_state_version BIGINT, p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE;
v_session_id UUID;
v_interaction agent_interactions%ROWTYPE;
v_result JSONB;
BEGIN IF session_user = 'everydayai_worker' THEN PERFORM _assert_agent_runtime_actor(TRUE);
ELSE PERFORM _assert_agent_runtime_actor(FALSE);
END IF;
SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
PERFORM 1 FROM agent_runtime_sessions WHERE id = v_session_id FOR UPDATE;
SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
PERFORM 1 FROM agent_actions WHERE run_id = p_run_id ORDER BY id FOR UPDATE;
PERFORM 1 FROM agent_action_attempts attempt JOIN agent_actions action ON action.id = attempt.action_id WHERE action.run_id = p_run_id ORDER BY attempt.id FOR UPDATE OF attempt;
PERFORM 1 FROM agent_interactions WHERE run_id = p_run_id ORDER BY id FOR UPDATE;
PERFORM 1 FROM agent_authorization_grants WHERE run_id = p_run_id ORDER BY id FOR UPDATE;
IF v_run.status NOT IN ('completed', 'failed', 'cancelled') AND v_run.state_version = p_expected_state_version THEN FOR v_interaction IN UPDATE agent_interactions SET status = 'cancelled', resolved_at = clock_timestamp(), recovery_worker_id = NULL, recovery_token = NULL, recovery_lease_expires_at = NULL, state_version = state_version + 1, updated_at = clock_timestamp() WHERE run_id = p_run_id AND status = 'open' RETURNING * LOOP PERFORM append_agent_runtime_event( v_interaction.session_id, 'interaction.cancelled', v_interaction.run_id, NULL, v_interaction.id, 'system', session_user, jsonb_build_object( 'interaction_id', v_interaction.id, 'action_id', v_interaction.action_id, 'reason', p_reason), ARRAY['web_runtime', 'audit']::TEXT[]);
END LOOP;
UPDATE agent_authorization_grants SET status = 'revoked', revoked_at = clock_timestamp() WHERE run_id = p_run_id AND status = 'active';
END IF;
v_result := _cancel_agent_run_220_23( p_run_id, p_expected_state_version, p_reason);
RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION cancel_agent_run(UUID, BIGINT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION cancel_agent_run(UUID, BIGINT, TEXT)
TO everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
CREATE FUNCTION claim_next_agent_action_reconciliation( p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 120, p_min_age_seconds INTEGER DEFAULT 30
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_candidate RECORD;
v_claim JSONB;
BEGIN PERFORM _assert_agent_runtime_actor(TRUE);
FOR v_candidate IN SELECT attempt.id, attempt.state_version FROM agent_action_attempts attempt JOIN agent_action_dispatch_intents intent ON intent.attempt_id = attempt.id WHERE attempt.status = 'dispatching' AND attempt.lease_expires_at <= clock_timestamp() ORDER BY attempt.updated_at, attempt.id LIMIT 100 LOOP PERFORM 1 FROM agent_runtime_sessions WHERE id = (SELECT session_id FROM agent_action_attempts WHERE id = v_candidate.id) FOR UPDATE;
PERFORM 1 FROM agent_runs WHERE id = (SELECT run_id FROM agent_action_attempts WHERE id = v_candidate.id) FOR UPDATE;
PERFORM 1 FROM agent_actions WHERE id = (SELECT action_id FROM agent_action_attempts WHERE id = v_candidate.id) FOR UPDATE;
UPDATE agent_action_attempts SET status = 'unknown', ambiguity_evidence = jsonb_build_object( 'kind', 'dispatch_intent_outcome_unproven'), retry_disposition = 'retry_after_reconcile', state_version = state_version + 1, updated_at = clock_timestamp() WHERE id = v_candidate.id AND status = 'dispatching';
UPDATE agent_actions SET status = 'unknown', retry_disposition = 'retry_after_reconcile', state_version = state_version + 1, updated_at = clock_timestamp() WHERE id = (SELECT action_id FROM agent_action_attempts WHERE id = v_candidate.id) AND status = 'running';
END LOOP;
FOR v_candidate IN SELECT attempt.id, attempt.state_version FROM agent_action_attempts attempt WHERE attempt.status IN ('accepted', 'unknown') AND (attempt.reconciliation_token IS NULL OR attempt.reconciliation_lease_expires_at <= clock_timestamp()) AND attempt.updated_at <= clock_timestamp() - make_interval(secs => p_min_age_seconds) ORDER BY attempt.updated_at, attempt.id LIMIT 100 LOOP v_claim := claim_agent_action_reconciliation( v_candidate.id, v_candidate.state_version, p_worker_id, p_lease_seconds);
IF v_claim->>'outcome' = 'claimed' THEN RETURN v_claim || jsonb_build_object( 'snapshot', _agent_action_dispatch_snapshot( (SELECT attempt FROM agent_action_attempts attempt WHERE attempt.id = v_candidate.id)));
END IF;
END LOOP;
RETURN jsonb_build_object('outcome', 'not_found');
END;
$$;

REVOKE ALL ON FUNCTION
    _agent_action_dispatch_snapshot(agent_action_attempts),
    open_agent_authorization_interaction(UUID, BIGINT, JSONB, TEXT, INTEGER),
    resolve_agent_authorization_interaction(
        UUID, BIGINT, TEXT, TEXT, JSONB, TEXT, TEXT, INTEGER
    ),
    revoke_agent_authorization_grant(UUID),
    claim_next_agent_action_reconciliation(TEXT, INTEGER, INTEGER),
    claim_next_agent_authorization_recovery(TEXT, INTEGER),
    renew_agent_authorization_recovery(UUID, UUID, BIGINT, INTEGER),
    activate_agent_authorized_action(
        UUID, BIGINT, UUID, UUID, BIGINT, UUID
    ),
    expire_agent_authorization_interaction(UUID, BIGINT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION claim_next_agent_authorization_recovery(TEXT, INTEGER), renew_agent_authorization_recovery(UUID, UUID, BIGINT, INTEGER), activate_agent_authorized_action( UUID, BIGINT, UUID, UUID, BIGINT, UUID), expire_agent_authorization_interaction(UUID, BIGINT)
TO everydayai_worker;
GRANT EXECUTE ON FUNCTION open_agent_authorization_interaction( UUID, BIGINT, JSONB, TEXT, INTEGER), claim_next_agent_action_reconciliation(TEXT, INTEGER, INTEGER)
TO everydayai_worker;
GRANT EXECUTE ON FUNCTION resolve_agent_authorization_interaction( UUID, BIGINT, TEXT, TEXT, JSONB, TEXT, TEXT, INTEGER), revoke_agent_authorization_grant(UUID)
TO everydayai_runtime, everydayai_wecom_runtime;
RESET ROLE;
