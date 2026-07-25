-- 167: 闭合 WeCom runtime 消息能力与 Worker Outbox 能力。
-- 修复所有权转移脚本覆盖 154 ACL，以及 Worker 切换后仍依赖直表读取的问题。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _assert_wecom_delivery_worker_scope()
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_worker'
       OR current_setting('app.access_kind', TRUE) <> 'worker' THEN
        RAISE EXCEPTION 'WECOM_DELIVERY_WORKER_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

ALTER FUNCTION claim_conversation_delivery(INTEGER, INTEGER)
    RENAME TO _claim_conversation_delivery_core;
ALTER FUNCTION renew_conversation_delivery(UUID, UUID, INTEGER, JSONB)
    RENAME TO _renew_conversation_delivery_core;
ALTER FUNCTION complete_conversation_delivery(UUID, UUID, JSONB)
    RENAME TO _complete_conversation_delivery_core;
ALTER FUNCTION fail_conversation_delivery(UUID, UUID, TEXT, JSONB, INTEGER)
    RENAME TO _fail_conversation_delivery_core;

ALTER FUNCTION _claim_conversation_delivery_core(INTEGER, INTEGER)
    OWNER TO everydayai_owner;
ALTER FUNCTION _renew_conversation_delivery_core(UUID, UUID, INTEGER, JSONB)
    OWNER TO everydayai_owner;
ALTER FUNCTION _complete_conversation_delivery_core(UUID, UUID, JSONB)
    OWNER TO everydayai_owner;
ALTER FUNCTION _fail_conversation_delivery_core(
    UUID, UUID, TEXT, JSONB, INTEGER
) OWNER TO everydayai_owner;

CREATE FUNCTION claim_conversation_delivery(
    p_lease_seconds INTEGER DEFAULT 60,
    p_max_attempts INTEGER DEFAULT 8
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai' THEN
        PERFORM public._assert_wecom_delivery_worker_scope();
    END IF;
    RETURN public._claim_conversation_delivery_core(
        p_lease_seconds, p_max_attempts
    );
END;
$$;

CREATE FUNCTION renew_conversation_delivery(
    p_delivery_id UUID,
    p_lease_token UUID,
    p_lease_seconds INTEGER DEFAULT 60,
    p_delivered_items JSONB DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai' THEN
        PERFORM public._assert_wecom_delivery_worker_scope();
    END IF;
    RETURN public._renew_conversation_delivery_core(
        p_delivery_id, p_lease_token, p_lease_seconds, p_delivered_items
    );
END;
$$;

CREATE FUNCTION complete_conversation_delivery(
    p_delivery_id UUID,
    p_lease_token UUID,
    p_delivered_items JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai' THEN
        PERFORM public._assert_wecom_delivery_worker_scope();
    END IF;
    RETURN public._complete_conversation_delivery_core(
        p_delivery_id, p_lease_token, p_delivered_items
    );
END;
$$;

CREATE FUNCTION fail_conversation_delivery(
    p_delivery_id UUID,
    p_lease_token UUID,
    p_error TEXT,
    p_delivered_items JSONB,
    p_max_attempts INTEGER DEFAULT 8
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai' THEN
        PERFORM public._assert_wecom_delivery_worker_scope();
    END IF;
    RETURN public._fail_conversation_delivery_core(
        p_delivery_id, p_lease_token, p_error, p_delivered_items,
        p_max_attempts
    );
END;
$$;

CREATE FUNCTION worker_get_conversation_delivery_payload(
    p_delivery_id UUID,
    p_lease_token UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_delivery conversation_deliveries%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_message messages%ROWTYPE;
    v_message_id UUID;
BEGIN
    IF session_user <> 'everydayai' THEN
        PERFORM public._assert_wecom_delivery_worker_scope();
    END IF;

    SELECT * INTO v_delivery
      FROM public.conversation_deliveries
     WHERE id = p_delivery_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'DELIVERY_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_delivery.status <> 'delivering'
       OR v_delivery.lease_token IS DISTINCT FROM p_lease_token
       OR v_delivery.lease_expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    SELECT * INTO v_task
      FROM public.tasks
     WHERE id = v_delivery.task_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'DELIVERY_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;

    IF v_delivery.delivery_kind = 'assistant_terminal'
       AND v_task.status = 'failed' THEN
        RETURN jsonb_build_object(
            'outcome', 'loaded',
            'task', to_jsonb(v_task),
            'message', NULL
        );
    END IF;

    v_message_id := CASE v_delivery.delivery_kind
        WHEN 'web_user_message' THEN v_task.input_message_id
        ELSE v_task.assistant_message_id
    END;
    IF v_message_id IS NULL THEN
        RAISE EXCEPTION 'DELIVERY_MESSAGE_ID_MISSING'
            USING ERRCODE = 'P0002';
    END IF;

    SELECT * INTO v_message
      FROM public.messages
     WHERE id = v_message_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'DELIVERY_MESSAGE_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'loaded',
        'task', to_jsonb(v_task),
        'message', to_jsonb(v_message)
    );
END;
$$;

REVOKE ALL ON FUNCTION _assert_wecom_delivery_worker_scope()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
REVOKE ALL ON FUNCTION
    _claim_conversation_delivery_core(INTEGER, INTEGER),
    _renew_conversation_delivery_core(UUID, UUID, INTEGER, JSONB),
    _complete_conversation_delivery_core(UUID, UUID, JSONB),
    _fail_conversation_delivery_core(UUID, UUID, TEXT, JSONB, INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
REVOKE ALL ON FUNCTION
    claim_conversation_delivery(INTEGER, INTEGER),
    renew_conversation_delivery(UUID, UUID, INTEGER, JSONB),
    complete_conversation_delivery(UUID, UUID, JSONB),
    fail_conversation_delivery(UUID, UUID, TEXT, JSONB, INTEGER),
    worker_get_conversation_delivery_payload(UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

GRANT EXECUTE ON FUNCTION
    claim_conversation_delivery(INTEGER, INTEGER),
    renew_conversation_delivery(UUID, UUID, INTEGER, JSONB),
    complete_conversation_delivery(UUID, UUID, JSONB),
    fail_conversation_delivery(UUID, UUID, TEXT, JSONB, INTEGER),
    worker_get_conversation_delivery_payload(UUID, UUID)
TO everydayai_worker;

GRANT EXECUTE ON FUNCTION
    resolve_wecom_conversation(UUID, TEXT, TEXT, TEXT, UUID),
    stage_wecom_attachment_v2(
        UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
        BIGINT, JSONB, UUID
    ),
    enqueue_wecom_generation_turn_v2(
        JSONB, UUID, UUID, UUID, JSONB, JSONB
    ),
    update_wecom_conversation_setting(UUID, UUID, TEXT, TEXT, UUID),
    record_user_activity(
        UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
    )
TO everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION record_user_activity(
    UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
) TO everydayai_runtime, everydayai_worker;

DO $legacy_compatibility$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'everydayai') THEN
        GRANT EXECUTE ON FUNCTION
            claim_conversation_delivery(INTEGER, INTEGER),
            renew_conversation_delivery(UUID, UUID, INTEGER, JSONB),
            complete_conversation_delivery(UUID, UUID, JSONB),
            fail_conversation_delivery(UUID, UUID, TEXT, JSONB, INTEGER),
            worker_get_conversation_delivery_payload(UUID, UUID)
        TO everydayai;
    END IF;
END
$legacy_compatibility$;

RESET ROLE;
