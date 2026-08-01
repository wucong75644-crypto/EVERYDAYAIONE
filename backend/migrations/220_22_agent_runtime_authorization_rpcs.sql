-- 220_22: Narrow RPC surface for Runtime authorization facts.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION open_agent_authorization_interaction(
    p_action_id UUID, p_expected_action_version BIGINT,
    p_prompt JSONB, p_prompt_hash TEXT, p_ttl_seconds INTEGER DEFAULT 900
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_action agent_actions%ROWTYPE; v_interaction agent_interactions%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF jsonb_typeof(p_prompt) IS DISTINCT FROM 'object'
       OR NOT _agent_action_json_is_safe(p_prompt)
       OR p_prompt_hash !~ '^[0-9a-f]{64}$'
       OR p_ttl_seconds NOT BETWEEN 30 AND 86400 THEN
        RAISE EXCEPTION 'AGENT_AUTHORIZATION_INVALID_INTERACTION'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_action FROM agent_actions WHERE id = p_action_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    SELECT * INTO v_interaction FROM agent_interactions
     WHERE action_id = p_action_id FOR UPDATE;
    IF FOUND THEN
        IF v_interaction.prompt_hash IS DISTINCT FROM p_prompt_hash THEN
            RETURN jsonb_build_object('outcome', 'interaction_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', CASE WHEN v_interaction.status = 'open'
                            THEN 'already_open' ELSE v_interaction.status END,
            'interaction', to_jsonb(v_interaction));
    END IF;
    IF v_action.state_version <> p_expected_action_version
       OR v_action.status <> 'awaiting_authorization' THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    INSERT INTO agent_interactions(
        action_id, session_id, run_id, org_id, user_id,
        prompt, prompt_hash, expires_at
    ) VALUES (
        v_action.id, v_action.session_id, v_action.run_id,
        v_action.org_id, v_action.user_id, p_prompt, p_prompt_hash,
        clock_timestamp() + make_interval(secs => p_ttl_seconds)
    ) RETURNING * INTO v_interaction;
    RETURN jsonb_build_object(
        'outcome', 'opened', 'interaction', to_jsonb(v_interaction));
END;
$$;

CREATE FUNCTION resolve_agent_authorization_interaction(
    p_interaction_id UUID, p_expected_version BIGINT,
    p_response TEXT, p_response_hash TEXT,
    p_effective_scope JSONB, p_grant_kind TEXT DEFAULT 'action',
    p_workflow_key TEXT DEFAULT NULL, p_ttl_seconds INTEGER DEFAULT 900
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_interaction agent_interactions%ROWTYPE; v_action agent_actions%ROWTYPE;
    v_grant agent_authorization_grants%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_response NOT IN ('approve', 'deny')
       OR p_response_hash !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(p_effective_scope) IS DISTINCT FROM 'object'
       OR NOT _agent_action_json_is_safe(p_effective_scope)
       OR p_grant_kind NOT IN ('action', 'workflow')
       OR p_ttl_seconds NOT BETWEEN 30 AND 86400
       OR (p_grant_kind = 'workflow'
           AND NULLIF(btrim(p_workflow_key), '') IS NULL)
       OR (p_grant_kind = 'action' AND p_workflow_key IS NOT NULL) THEN
        RAISE EXCEPTION 'AGENT_AUTHORIZATION_INVALID_RESOLUTION'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_interaction FROM agent_interactions
     WHERE id = p_interaction_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    SELECT * INTO v_action FROM agent_actions
     WHERE id = v_interaction.action_id FOR UPDATE;
    IF tenant_org_id() IS DISTINCT FROM v_action.org_id OR NOT EXISTS (
        SELECT 1 FROM agent_runtime_sessions runtime_session
         WHERE runtime_session.id = v_action.session_id
           AND (
               (runtime_session.scope_kind = 'user'
                AND runtime_session.user_id = tenant_actor_user_id())
               OR (runtime_session.scope_kind = 'channel' AND EXISTS (
                   SELECT 1 FROM org_members member
                    WHERE member.org_id = runtime_session.org_id
                      AND member.user_id = tenant_actor_user_id()
                      AND member.status = 'active'
               ))
           )
    ) THEN
        RAISE EXCEPTION 'AGENT_AUTHORIZATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF v_interaction.status = 'resolved' THEN
        IF v_interaction.response_hash IS DISTINCT FROM p_response_hash THEN
            RETURN jsonb_build_object('outcome', 'resolution_conflict');
        END IF;
        SELECT * INTO v_grant FROM agent_authorization_grants
         WHERE interaction_id = v_interaction.id;
        RETURN jsonb_build_object(
            'outcome', 'already_resolved', 'interaction', to_jsonb(v_interaction),
            'grant', CASE WHEN v_grant.id IS NULL THEN NULL
                          ELSE to_jsonb(v_grant) END);
    END IF;
    IF v_interaction.status <> 'open'
       OR v_interaction.state_version <> p_expected_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF v_interaction.expires_at <= clock_timestamp() THEN
        UPDATE agent_interactions SET status = 'expired',
               resolved_at = clock_timestamp(),
               state_version = state_version + 1,
               updated_at = clock_timestamp()
         WHERE id = v_interaction.id;
        RETURN jsonb_build_object('outcome', 'expired');
    END IF;
    UPDATE agent_interactions SET status = 'resolved', response = p_response,
           response_hash = p_response_hash, resolved_at = clock_timestamp(),
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = v_interaction.id RETURNING * INTO v_interaction;
    IF p_response = 'approve' THEN
        INSERT INTO agent_authorization_grants(
            session_id, run_id, action_id, interaction_id, org_id, user_id,
            grant_kind, workflow_key, arguments_hash, effective_scope, expires_at
        ) VALUES (
            v_action.session_id, v_action.run_id,
            CASE WHEN p_grant_kind = 'action' THEN v_action.id END,
            v_interaction.id, v_action.org_id, v_action.user_id,
            p_grant_kind, p_workflow_key,
            CASE WHEN p_grant_kind = 'action'
                 THEN v_action.arguments_hash END,
            p_effective_scope,
            clock_timestamp() + make_interval(secs => p_ttl_seconds)
        ) RETURNING * INTO v_grant;
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'resolved', 'interaction', to_jsonb(v_interaction),
        'grant', CASE WHEN v_grant.id IS NULL THEN NULL ELSE to_jsonb(v_grant) END);
END;
$$;

CREATE FUNCTION revoke_agent_authorization_grant(p_grant_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_grant agent_authorization_grants%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    SELECT * INTO v_grant FROM agent_authorization_grants
     WHERE id = p_grant_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    IF tenant_org_id() IS DISTINCT FROM v_grant.org_id OR NOT EXISTS (
        SELECT 1 FROM agent_runtime_sessions runtime_session
         WHERE runtime_session.id = v_grant.session_id
           AND (
               (runtime_session.scope_kind = 'user'
                AND runtime_session.user_id = tenant_actor_user_id())
               OR (runtime_session.scope_kind = 'channel' AND EXISTS (
                   SELECT 1 FROM org_members member
                    WHERE member.org_id = runtime_session.org_id
                      AND member.user_id = tenant_actor_user_id()
                      AND member.status = 'active'
               ))
           )
    ) THEN
        RAISE EXCEPTION 'AGENT_AUTHORIZATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF v_grant.status = 'revoked' THEN
        RETURN jsonb_build_object('outcome', 'already_revoked',
                                  'grant', to_jsonb(v_grant));
    END IF;
    UPDATE agent_authorization_grants SET status = 'revoked',
           revoked_at = clock_timestamp()
     WHERE id = p_grant_id RETURNING * INTO v_grant;
    RETURN jsonb_build_object('outcome', 'revoked', 'grant', to_jsonb(v_grant));
END;
$$;

CREATE FUNCTION record_agent_policy_receipt(
    p_action_id UUID, p_arguments_hash TEXT,
    p_executor_type TEXT, p_executor_revision INTEGER,
    p_policy_revision TEXT, p_decision TEXT, p_grant_id UUID,
    p_effective_scope JSONB, p_reason_codes TEXT[],
    p_obligations TEXT[], p_receipt_hash TEXT,
    p_ttl_seconds INTEGER DEFAULT 300
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_action agent_actions%ROWTYPE; v_grant agent_authorization_grants%ROWTYPE;
    v_receipt agent_policy_receipts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_decision NOT IN ('allow', 'require_authorization', 'deny')
       OR p_arguments_hash !~ '^[0-9a-f]{64}$'
       OR p_receipt_hash !~ '^[0-9a-f]{64}$'
       OR p_executor_revision < 1 OR p_ttl_seconds NOT BETWEEN 5 AND 3600
       OR NULLIF(btrim(p_executor_type), '') IS NULL
       OR NULLIF(btrim(p_policy_revision), '') IS NULL
       OR cardinality(p_reason_codes) < 1
       OR jsonb_typeof(p_effective_scope) IS DISTINCT FROM 'object'
       OR NOT _agent_action_json_is_safe(p_effective_scope) THEN
        RAISE EXCEPTION 'AGENT_POLICY_INVALID_RECEIPT' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_action FROM agent_actions WHERE id = p_action_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    SELECT * INTO v_receipt FROM agent_policy_receipts
     WHERE action_id = p_action_id
       AND arguments_hash = p_arguments_hash
       AND executor_type = p_executor_type
       AND executor_revision = p_executor_revision
       AND policy_revision = p_policy_revision FOR UPDATE;
    IF FOUND THEN
        IF v_receipt.receipt_hash IS DISTINCT FROM p_receipt_hash THEN
            RETURN jsonb_build_object('outcome', 'receipt_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_recorded', 'receipt', to_jsonb(v_receipt));
    END IF;
    IF v_action.arguments_hash IS DISTINCT FROM p_arguments_hash THEN
        RETURN jsonb_build_object('outcome', 'arguments_conflict');
    END IF;
    IF p_decision = 'allow' AND p_grant_id IS NOT NULL THEN
        SELECT * INTO v_grant FROM agent_authorization_grants
         WHERE id = p_grant_id FOR UPDATE;
        IF NOT FOUND OR v_grant.status <> 'active'
           OR v_grant.expires_at <= clock_timestamp()
           OR v_grant.session_id <> v_action.session_id
           OR v_grant.org_id IS DISTINCT FROM v_action.org_id
           OR (
               v_grant.grant_kind = 'action'
               AND (v_grant.action_id <> v_action.id
                    OR v_grant.arguments_hash <> p_arguments_hash)
           ) THEN
            RETURN jsonb_build_object('outcome', 'grant_invalid');
        END IF;
        INSERT INTO agent_authorization_grant_uses(
            grant_id, action_id, arguments_hash
        ) VALUES (v_grant.id, v_action.id, p_arguments_hash)
        ON CONFLICT (action_id) DO NOTHING;
        IF NOT EXISTS (
            SELECT 1 FROM agent_authorization_grant_uses
             WHERE action_id = v_action.id AND grant_id = v_grant.id
               AND arguments_hash = p_arguments_hash
        ) THEN RETURN jsonb_build_object('outcome', 'grant_replay_conflict');
        END IF;
    ELSIF p_decision = 'allow' AND p_grant_id IS NULL THEN
        NULL;
    ELSIF p_grant_id IS NOT NULL THEN
        RETURN jsonb_build_object('outcome', 'grant_invalid');
    END IF;
    INSERT INTO agent_policy_receipts(
        action_id, session_id, run_id, org_id, user_id, grant_id, decision,
        arguments_hash, executor_type, executor_revision, policy_revision,
        effective_scope, reason_codes, obligations, receipt_hash, expires_at
    ) VALUES (
        v_action.id, v_action.session_id, v_action.run_id,
        v_action.org_id, v_action.user_id, p_grant_id, p_decision,
        p_arguments_hash, p_executor_type, p_executor_revision,
        p_policy_revision, p_effective_scope, p_reason_codes,
        COALESCE(p_obligations, '{}'), p_receipt_hash,
        clock_timestamp() + make_interval(secs => p_ttl_seconds)
    ) RETURNING * INTO v_receipt;
    RETURN jsonb_build_object(
        'outcome', 'recorded', 'receipt', to_jsonb(v_receipt));
END;
$$;

CREATE FUNCTION get_agent_dispatch_policy_receipt(
    p_action_id UUID, p_arguments_hash TEXT,
    p_executor_type TEXT, p_executor_revision INTEGER, p_policy_revision TEXT
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_receipt agent_policy_receipts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT receipt.* INTO v_receipt FROM agent_policy_receipts receipt
     LEFT JOIN agent_authorization_grants authorization_grant
       ON authorization_grant.id = receipt.grant_id
     WHERE receipt.action_id = p_action_id
       AND receipt.arguments_hash = p_arguments_hash
       AND receipt.executor_type = p_executor_type
       AND receipt.executor_revision = p_executor_revision
       AND receipt.policy_revision = p_policy_revision
       AND receipt.decision = 'allow'
       AND receipt.expires_at > clock_timestamp()
       AND (receipt.grant_id IS NULL OR (
           authorization_grant.status = 'active'
           AND authorization_grant.expires_at > clock_timestamp()))
     ORDER BY receipt.evaluated_at DESC LIMIT 1;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    RETURN jsonb_build_object('outcome', 'found', 'receipt', to_jsonb(v_receipt));
END;
$$;

REVOKE ALL ON FUNCTION
    open_agent_authorization_interaction(UUID, BIGINT, JSONB, TEXT, INTEGER),
    resolve_agent_authorization_interaction(
        UUID, BIGINT, TEXT, TEXT, JSONB, TEXT, TEXT, INTEGER),
    revoke_agent_authorization_grant(UUID),
    record_agent_policy_receipt(
        UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, UUID, JSONB,
        TEXT[], TEXT[], TEXT, INTEGER),
    get_agent_dispatch_policy_receipt(UUID, TEXT, TEXT, INTEGER, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    open_agent_authorization_interaction(UUID, BIGINT, JSONB, TEXT, INTEGER),
    record_agent_policy_receipt(
        UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, UUID, JSONB,
        TEXT[], TEXT[], TEXT, INTEGER),
    get_agent_dispatch_policy_receipt(UUID, TEXT, TEXT, INTEGER, TEXT)
TO everydayai_worker;
GRANT EXECUTE ON FUNCTION
    resolve_agent_authorization_interaction(
        UUID, BIGINT, TEXT, TEXT, JSONB, TEXT, TEXT, INTEGER),
    revoke_agent_authorization_grant(UUID)
TO everydayai_runtime, everydayai_wecom_runtime;

RESET ROLE;
