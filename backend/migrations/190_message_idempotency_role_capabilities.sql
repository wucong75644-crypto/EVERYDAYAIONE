-- 190: Bind message generation idempotency to isolated Runtime/Worker roles.
-- Prerequisite: transfer-runtime-message-ownership.sh transferred both functions.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION claim_message_generation_request(
    p_org_id UUID,
    p_user_id UUID,
    p_conversation_id UUID,
    p_idempotency_key VARCHAR,
    p_request_fingerprint CHAR(64),
    p_client_task_id VARCHAR,
    p_assistant_message_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_request public.message_generation_requests%ROWTYPE;
    v_outcome TEXT;
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR tenant_actor_user_id() IS DISTINCT FROM p_user_id
       OR tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'MESSAGE_IDEMPOTENCY_RUNTIME_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_idempotency_key IS NULL
       OR length(p_idempotency_key) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'IDEMPOTENCY_KEY_INVALID' USING ERRCODE = '22023';
    END IF;
    IF p_request_fingerprint IS NULL
       OR p_request_fingerprint !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'IDEMPOTENCY_FINGERPRINT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM public.conversations conversation
         WHERE conversation.id = p_conversation_id
           AND conversation.user_id = p_user_id
           AND conversation.org_id IS NOT DISTINCT FROM p_org_id
    ) THEN
        RAISE EXCEPTION 'IDEMPOTENCY_CONVERSATION_ACCESS_DENIED'
            USING ERRCODE = '42501';
    END IF;

    BEGIN
        INSERT INTO public.message_generation_requests(
            org_id, user_id, conversation_id, idempotency_key,
            request_fingerprint, client_task_id, assistant_message_id
        ) VALUES (
            p_org_id, p_user_id, p_conversation_id, p_idempotency_key,
            p_request_fingerprint, p_client_task_id, p_assistant_message_id
        ) RETURNING * INTO v_request;
        v_outcome := 'claimed';
    EXCEPTION WHEN unique_violation THEN
        SELECT *
          INTO v_request
          FROM public.message_generation_requests request
         WHERE request.user_id = p_user_id
           AND request.org_id IS NOT DISTINCT FROM p_org_id
           AND request.idempotency_key = p_idempotency_key;
        IF v_request.id IS NULL THEN
            RAISE;
        END IF;
        IF v_request.request_fingerprint <> p_request_fingerprint THEN
            v_outcome := 'fingerprint_mismatch';
        ELSE
            v_outcome := v_request.status;
        END IF;
    END;

    RETURN jsonb_build_object(
        'outcome', v_outcome,
        'request_id', v_request.id,
        'stored_fingerprint', v_request.request_fingerprint,
        'request_status', v_request.status,
        'stored_client_task_id', v_request.client_task_id,
        'stored_user_message_id', v_request.user_message_id,
        'stored_assistant_message_id', v_request.assistant_message_id,
        'stored_response_status', v_request.response_status,
        'stored_response_body', v_request.response_body,
        'stored_error_code', v_request.error_code
    );
END;
$$;

CREATE OR REPLACE FUNCTION cleanup_expired_message_generation_requests()
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_deleted BIGINT;
BEGIN
    IF session_user <> 'everydayai_worker'
       OR current_setting('app.access_kind', TRUE) <> 'worker'
       OR tenant_actor_user_id() IS NOT NULL
       OR tenant_org_id() IS NOT NULL THEN
        RAISE EXCEPTION 'MESSAGE_IDEMPOTENCY_CLEANUP_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    DELETE FROM public.message_generation_requests
     WHERE expires_at < NOW();
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;

REVOKE ALL ON FUNCTION claim_message_generation_request(
    UUID, UUID, UUID, VARCHAR, CHAR, VARCHAR, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
GRANT EXECUTE ON FUNCTION claim_message_generation_request(
    UUID, UUID, UUID, VARCHAR, CHAR, VARCHAR, UUID
) TO everydayai_runtime;

REVOKE ALL ON FUNCTION cleanup_expired_message_generation_requests()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
GRANT EXECUTE ON FUNCTION cleanup_expired_message_generation_requests()
TO everydayai_worker;

RESET ROLE;
