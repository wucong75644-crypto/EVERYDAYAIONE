-- 127: Conversation Actor 租户 RPC 契约
-- OrgScopedDB 会为租户业务 RPC 自动注入 p_org_id。这里提供显式租户门面，
-- 在进入 120/121/125 的原子核心函数前校验 conversation/task 所属组织。

CREATE OR REPLACE FUNCTION bind_generation_turn(
    p_conversation_id UUID,
    p_task_id UUID,
    p_input_message_id UUID,
    p_turn_id UUID,
    p_execution_mode TEXT,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_org_id UUID;
BEGIN
    SELECT org_id INTO v_org_id
      FROM conversations
     WHERE id = p_conversation_id;
    IF NOT FOUND OR v_org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'TURN_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN bind_generation_turn(
        p_conversation_id,
        p_task_id,
        p_input_message_id,
        p_turn_id,
        p_execution_mode
    );
END;
$$;

CREATE OR REPLACE FUNCTION close_generation_turn(
    p_conversation_id UUID,
    p_task_id UUID,
    p_output_message_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_org_id UUID;
BEGIN
    SELECT org_id INTO v_org_id
      FROM conversations
     WHERE id = p_conversation_id;
    IF NOT FOUND OR v_org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'TURN_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN close_generation_turn(
        p_conversation_id,
        p_task_id,
        p_output_message_id
    );
END;
$$;

CREATE OR REPLACE FUNCTION enqueue_generation_turn(
    p_task_data JSONB,
    p_input_message_id UUID,
    p_turn_id UUID,
    p_execution_mode TEXT,
    p_delivery_context JSONB,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task_org_id UUID;
BEGIN
    BEGIN
        v_task_org_id := NULLIF(p_task_data->>'org_id', '')::UUID;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'ACTOR_ENQUEUE_ID_INVALID' USING ERRCODE = '22023';
    END;
    IF v_task_org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'ACTOR_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN enqueue_generation_turn(
        p_task_data,
        p_input_message_id,
        p_turn_id,
        p_execution_mode,
        p_delivery_context
    );
END;
$$;

CREATE OR REPLACE FUNCTION enqueue_wecom_generation_turn(
    p_task_data JSONB,
    p_input_message_id UUID,
    p_output_message_id UUID,
    p_turn_id UUID,
    p_input_content JSONB,
    p_delivery_context JSONB,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task_org_id UUID;
BEGIN
    BEGIN
        v_task_org_id := NULLIF(p_task_data->>'org_id', '')::UUID;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'WECOM_ACTOR_ENQUEUE_ID_INVALID'
            USING ERRCODE = '22023';
    END;
    IF v_task_org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'WECOM_ACTOR_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN enqueue_wecom_generation_turn(
        p_task_data,
        p_input_message_id,
        p_output_message_id,
        p_turn_id,
        p_input_content,
        p_delivery_context
    );
END;
$$;

REVOKE ALL ON FUNCTION bind_generation_turn(
    UUID, UUID, UUID, UUID, TEXT, UUID
) FROM PUBLIC;
REVOKE ALL ON FUNCTION close_generation_turn(
    UUID, UUID, UUID, UUID
) FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_generation_turn(
    JSONB, UUID, UUID, TEXT, JSONB, UUID
) FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_wecom_generation_turn(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
) FROM PUBLIC;

COMMENT ON FUNCTION bind_generation_turn(
    UUID, UUID, UUID, UUID, TEXT, UUID
) IS '租户范围校验后调用 Turn 绑定原子核心';
COMMENT ON FUNCTION close_generation_turn(
    UUID, UUID, UUID, UUID
) IS '租户范围校验后调用 Turn 关闭原子核心';
COMMENT ON FUNCTION enqueue_generation_turn(
    JSONB, UUID, UUID, TEXT, JSONB, UUID
) IS '租户范围校验后调用 Actor 原子入队核心';
COMMENT ON FUNCTION enqueue_wecom_generation_turn(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
) IS '租户范围校验后调用企微 Actor 原子入队核心';
