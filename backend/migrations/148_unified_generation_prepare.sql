-- 148: 统一生成 Turn 事务与本地任务准备
-- 依赖 119 message_generation_requests、120 Turn foundation、121 Actor task fields。

ALTER TABLE tasks
    DROP CONSTRAINT IF EXISTS tasks_status_check,
    ADD CONSTRAINT tasks_status_check
        CHECK (status IN (
            'preparing', 'pending', 'running', 'completed', 'failed', 'cancelled'
        ));

CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_external_task_id
    ON tasks(external_task_id)
    WHERE external_task_id IS NOT NULL;

DROP INDEX IF EXISTS idx_credit_tx_task_unique;
DROP INDEX IF EXISTS uq_credit_tx_task_org;
CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_tx_pending_task_org ON credit_transactions (
    task_id,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::UUID)
) WHERE type = 'lock' AND status = 'pending';

CREATE OR REPLACE FUNCTION _prepare_generation_messages(
    p_operation TEXT, p_conversation_id UUID, p_org_id UUID, p_turn_id UUID,
    p_input_message JSONB,
    p_output_message JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_input messages%ROWTYPE;
    v_output messages%ROWTYPE;
    v_bound_task tasks%ROWTYPE;
    v_input_id UUID;
    v_output_id UUID;
    v_turn_id UUID;
BEGIN
    BEGIN
        v_output_id := (p_output_message->>'id')::UUID;
        IF NULLIF(p_input_message->>'id', '') IS NOT NULL THEN
            v_input_id := (p_input_message->>'id')::UUID;
        END IF;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END;
    IF v_output_id IS NULL
       OR (p_operation NOT IN ('retry', 'regenerate_single') AND v_input_id IS NULL) THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    IF p_operation IN ('retry', 'regenerate_single') THEN
        SELECT * INTO v_output FROM messages WHERE id = v_output_id FOR UPDATE;
        IF NOT FOUND OR v_output.conversation_id IS DISTINCT FROM p_conversation_id
           OR v_output.org_id IS DISTINCT FROM p_org_id
           OR v_output.role::TEXT <> 'assistant'
           OR (p_operation = 'retry' AND v_output.status::TEXT <> 'failed') THEN
            RAISE EXCEPTION 'GENERATION_PREPARE_MESSAGE_CONFLICT' USING ERRCODE = '23505';
        END IF;
        IF EXISTS (
            SELECT 1 FROM tasks WHERE assistant_message_id = v_output_id
              AND status IN ('preparing', 'pending', 'running')
        ) THEN
            RAISE EXCEPTION 'GENERATION_PREPARE_TASK_CONFLICT' USING ERRCODE = '23505';
        END IF;
        IF v_output.reply_to_message_id IS NOT NULL AND v_output.turn_id IS NOT NULL THEN
            v_input_id := v_output.reply_to_message_id;
            v_turn_id := v_output.turn_id;
        ELSE
            SELECT * INTO v_bound_task FROM tasks
             WHERE assistant_message_id = v_output_id
               AND input_message_id IS NOT NULL AND turn_id IS NOT NULL
             ORDER BY created_at DESC, id DESC LIMIT 1 FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'GENERATION_PREPARE_ANCHOR_MISSING' USING ERRCODE = 'P0002';
            END IF;
            v_input_id := v_bound_task.input_message_id;
            v_turn_id := v_bound_task.turn_id;
        END IF;
        IF p_turn_id IS NOT NULL AND p_turn_id IS DISTINCT FROM v_turn_id THEN
            RAISE EXCEPTION 'GENERATION_PREPARE_TURN_CONFLICT' USING ERRCODE = '23505';
        END IF;
    ELSE
        IF p_turn_id IS NULL THEN
            RAISE EXCEPTION 'GENERATION_PREPARE_ARGUMENT_INVALID' USING ERRCODE = '22023';
        END IF;
        v_turn_id := p_turn_id;
        INSERT INTO messages(
            id, conversation_id, org_id, role, content, status, credits_cost,
            client_request_id, created_at, turn_id
        ) VALUES (
            v_input_id, p_conversation_id, p_org_id, 'user',
            COALESCE(p_input_message->'content', '[]'::JSONB), 'completed', 0,
            NULLIF(p_input_message->>'client_request_id', ''),
            COALESCE(NULLIF(p_input_message->>'created_at', '')::TIMESTAMPTZ, NOW()),
            v_turn_id
        ) ON CONFLICT (id) DO NOTHING;
        INSERT INTO messages(
            id, conversation_id, org_id, role, content, status, credits_cost,
            generation_params, created_at, turn_id, reply_to_message_id
        ) VALUES (
            v_output_id, p_conversation_id, p_org_id, 'assistant',
            COALESCE(p_output_message->'content', '[]'::JSONB),
            COALESCE(NULLIF(p_output_message->>'status', ''), 'pending'), 0,
            p_output_message->'generation_params',
            COALESCE(NULLIF(p_output_message->>'created_at', '')::TIMESTAMPTZ, NOW()),
            v_turn_id, v_input_id
        ) ON CONFLICT (id) DO NOTHING;
    END IF;

    SELECT * INTO v_input FROM messages WHERE id = v_input_id FOR UPDATE;
    SELECT * INTO v_output FROM messages WHERE id = v_output_id FOR UPDATE;
    IF v_input.id IS NULL OR v_output.id IS NULL
       OR v_input.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_output.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_input.org_id IS DISTINCT FROM p_org_id OR v_output.org_id IS DISTINCT FROM p_org_id
       OR v_input.role::TEXT <> 'user' OR v_output.role::TEXT <> 'assistant' THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_MESSAGE_CONFLICT' USING ERRCODE = '23505';
    END IF;
    IF (v_input.turn_id IS NOT NULL AND v_input.turn_id IS DISTINCT FROM v_turn_id)
       OR (v_output.turn_id IS NOT NULL AND v_output.turn_id IS DISTINCT FROM v_turn_id)
       OR (v_output.reply_to_message_id IS NOT NULL
           AND v_output.reply_to_message_id IS DISTINCT FROM v_input_id) THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_TURN_CONFLICT' USING ERRCODE = '23505';
    END IF;
    UPDATE messages SET turn_id = v_turn_id WHERE id = v_input_id;
    UPDATE messages SET turn_id = v_turn_id, reply_to_message_id = v_input_id,
        content = CASE WHEN p_operation IN ('retry', 'regenerate_single')
            THEN COALESCE((p_output_message->'content')::TEXT, v_output.content) ELSE content END,
        status = CASE WHEN p_operation IN ('retry', 'regenerate_single')
            THEN COALESCE(NULLIF(p_output_message->>'status', ''), status::TEXT) ELSE status END,
        generation_params = CASE WHEN p_operation IN ('retry', 'regenerate_single')
            THEN COALESCE(p_output_message->'generation_params', v_output.generation_params)
            ELSE generation_params END,
        is_error = CASE WHEN p_operation IN ('retry', 'regenerate_single') THEN FALSE ELSE is_error END
     WHERE id = v_output_id;
    RETURN jsonb_build_object('turn_id', v_turn_id, 'input_message_id', v_input_id, 'output_message_id', v_output_id);
END;
$$;

CREATE OR REPLACE FUNCTION _prepare_generation_tasks(
    p_tasks JSONB,
    p_user_id UUID,
    p_org_id UUID,
    p_conversation_id UUID,
    p_input_id UUID,
    p_output_id UUID,
    p_turn_id UUID,
    p_base_context_revision BIGINT,
    p_context_through_message_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_data JSONB;
    v_id UUID;
    v_ids UUID[] := ARRAY[]::UUID[];
    v_status TEXT;
    v_inserted BIGINT := 0;
    v_rows BIGINT;
BEGIN
    FOR v_data IN SELECT value FROM jsonb_array_elements(p_tasks)
    LOOP
        BEGIN
            v_id := (v_data->>'id')::UUID;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'GENERATION_PREPARE_ARGUMENT_INVALID' USING ERRCODE = '22023';
        END;
        v_status := COALESCE(NULLIF(v_data->>'status', ''), 'preparing');
        IF v_id IS NULL OR COALESCE(v_data->>'type', '') NOT IN ('chat', 'image', 'video')
           OR v_status NOT IN ('preparing', 'pending', 'running') THEN
            RAISE EXCEPTION 'GENERATION_PREPARE_ARGUMENT_INVALID' USING ERRCODE = '22023';
        END IF;
        INSERT INTO tasks(
            id, external_task_id, client_task_id, user_id, org_id, conversation_id,
            type, status, model_id, placeholder_message_id, assistant_message_id,
            request_params, placeholder_created_at, input_message_id, turn_id,
            base_context_revision, context_through_message_id, execution_mode,
            delivery_context, image_index, batch_id
        ) VALUES (
            v_id, NULLIF(v_data->>'external_task_id', ''),
            NULLIF(v_data->>'client_task_id', ''), p_user_id, p_org_id,
            p_conversation_id, v_data->>'type', v_status,
            NULLIF(v_data->>'model_id', ''), p_output_id, p_output_id,
            COALESCE(v_data->'request_params', '{}'::JSONB),
            NULLIF(v_data->>'placeholder_created_at', '')::TIMESTAMPTZ,
            p_input_id, p_turn_id, p_base_context_revision,
            p_context_through_message_id,
            COALESCE(NULLIF(v_data->>'execution_mode', ''), 'serial'),
            COALESCE(v_data->'delivery_context', '{}'::JSONB),
            NULLIF(v_data->>'image_index', '')::INTEGER,
            NULLIF(v_data->>'batch_id', '')
        ) ON CONFLICT (id) DO NOTHING;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        v_inserted := v_inserted + v_rows;
        SELECT * INTO v_task FROM tasks WHERE id = v_id FOR UPDATE;
        IF v_task.id IS NULL OR v_task.user_id IS DISTINCT FROM p_user_id
           OR v_task.org_id IS DISTINCT FROM p_org_id
           OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
           OR v_task.type IS DISTINCT FROM v_data->>'type'
           OR v_task.external_task_id IS DISTINCT FROM NULLIF(v_data->>'external_task_id', '')
           OR v_task.client_task_id IS DISTINCT FROM NULLIF(v_data->>'client_task_id', '')
           OR v_task.model_id IS DISTINCT FROM NULLIF(v_data->>'model_id', '')
           OR v_task.assistant_message_id IS DISTINCT FROM p_output_id
           OR v_task.input_message_id IS DISTINCT FROM p_input_id
           OR v_task.turn_id IS DISTINCT FROM p_turn_id
           OR v_task.execution_mode IS DISTINCT FROM
              COALESCE(NULLIF(v_data->>'execution_mode', ''), 'serial')
           OR v_task.delivery_context IS DISTINCT FROM
              COALESCE(v_data->'delivery_context', '{}'::JSONB)
           OR v_task.request_params IS DISTINCT FROM
              COALESCE(v_data->'request_params', '{}'::JSONB) THEN
            RAISE EXCEPTION 'GENERATION_PREPARE_TASK_CONFLICT' USING ERRCODE = '23505';
        END IF;
        v_ids := array_append(v_ids, v_id);
    END LOOP;
    RETURN jsonb_build_object('task_ids', to_jsonb(v_ids), 'inserted_count', v_inserted);
END;
$$;

CREATE OR REPLACE FUNCTION prepare_generation(
    p_request_id UUID,
    p_operation TEXT,
    p_conversation_id UUID,
    p_user_id UUID,
    p_org_id UUID,
    p_turn_id UUID,
    p_input_message JSONB,
    p_output_message JSONB,
    p_tasks JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_request message_generation_requests%ROWTYPE;
    v_conversation conversations%ROWTYPE;
    v_output_id UUID;
    v_messages JSONB;
    v_tasks_result JSONB;
BEGIN
    IF p_request_id IS NULL
       OR p_conversation_id IS NULL
       OR p_user_id IS NULL
       OR p_operation NOT IN ('send', 'regenerate', 'retry', 'regenerate_single')
       OR p_input_message IS NULL OR jsonb_typeof(p_input_message) <> 'object'
       OR p_output_message IS NULL OR jsonb_typeof(p_output_message) <> 'object'
       OR p_tasks IS NULL OR jsonb_typeof(p_tasks) <> 'array'
       OR jsonb_array_length(p_tasks) NOT BETWEEN 1 AND 16 THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    BEGIN
        v_output_id := (p_output_message->>'id')::UUID;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END;
    IF v_output_id IS NULL THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_request
      FROM message_generation_requests
     WHERE id = p_request_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_request.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_request.user_id IS DISTINCT FROM p_user_id
       OR v_request.org_id IS DISTINCT FROM p_org_id
       OR v_request.assistant_message_id IS DISTINCT FROM v_output_id THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_REQUEST_MISMATCH' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = p_conversation_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_conversation.user_id IS DISTINCT FROM p_user_id
       OR v_conversation.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_REQUEST_MISMATCH' USING ERRCODE = '42501';
    END IF;

    v_messages := _prepare_generation_messages(
        p_operation, p_conversation_id, p_org_id, p_turn_id,
        p_input_message, p_output_message
    );
    v_tasks_result := _prepare_generation_tasks(
        p_tasks, p_user_id, p_org_id, p_conversation_id,
        (v_messages->>'input_message_id')::UUID,
        (v_messages->>'output_message_id')::UUID,
        (v_messages->>'turn_id')::UUID, v_conversation.context_revision,
        v_conversation.last_closed_message_id
    );

    UPDATE message_generation_requests
       SET user_message_id = (v_messages->>'input_message_id')::UUID, updated_at = NOW()
     WHERE id = p_request_id;

    RETURN jsonb_build_object(
        'request_id', p_request_id,
        'conversation_id', p_conversation_id,
        'turn_id', v_messages->'turn_id',
        'input_message_id', v_messages->'input_message_id',
        'output_message_id', v_messages->'output_message_id',
        'base_context_revision', v_conversation.context_revision,
        'context_through_message_id', v_conversation.last_closed_message_id,
        'task_ids', v_tasks_result->'task_ids',
        'already_prepared', (v_tasks_result->>'inserted_count')::BIGINT = 0
    );
END;
$$;

CREATE OR REPLACE FUNCTION attach_generation_external_task(
    p_task_id UUID,
    p_external_task_id TEXT,
    p_credit_transaction_id UUID,
    p_org_id UUID,
    p_actual_model_id TEXT DEFAULT NULL,
    p_actual_request_params JSONB DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_credit credit_transactions%ROWTYPE;
BEGIN
    IF p_task_id IS NULL OR NULLIF(BTRIM(p_external_task_id), '') IS NULL
       OR (p_actual_request_params IS NOT NULL
           AND jsonb_typeof(p_actual_request_params) <> 'object') THEN
        RAISE EXCEPTION 'GENERATION_ATTACH_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'GENERATION_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_task.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'GENERATION_TASK_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF p_credit_transaction_id IS NOT NULL THEN
        SELECT * INTO v_credit FROM credit_transactions
         WHERE id = p_credit_transaction_id FOR UPDATE;
        IF NOT FOUND OR v_credit.task_id IS DISTINCT FROM p_task_id
           OR v_credit.user_id IS DISTINCT FROM v_task.user_id
           OR v_credit.org_id IS DISTINCT FROM p_org_id
           OR v_credit.type <> 'lock' OR v_credit.status <> 'pending' THEN
            RAISE EXCEPTION 'GENERATION_ATTACH_CREDIT_MISMATCH' USING ERRCODE = '23505';
        END IF;
    END IF;
    IF v_task.external_task_id IS NOT NULL THEN
        IF v_task.external_task_id IS DISTINCT FROM p_external_task_id
           OR v_task.credit_transaction_id IS DISTINCT FROM p_credit_transaction_id
           OR (NULLIF(p_actual_model_id, '') IS NOT NULL
               AND v_task.model_id IS DISTINCT FROM p_actual_model_id)
           OR (p_actual_request_params IS NOT NULL
               AND v_task.request_params IS DISTINCT FROM p_actual_request_params) THEN
            RAISE EXCEPTION 'GENERATION_ATTACH_CONFLICT' USING ERRCODE = '23505';
        END IF;
        RETURN jsonb_build_object('task_id', v_task.id, 'already_attached', TRUE);
    END IF;
    IF v_task.status <> 'preparing' THEN
        RAISE EXCEPTION 'GENERATION_ATTACH_STATE_INVALID' USING ERRCODE = '55000';
    END IF;
    UPDATE tasks
       SET external_task_id = p_external_task_id,
           credit_transaction_id = p_credit_transaction_id,
           model_id = COALESCE(NULLIF(p_actual_model_id, ''), model_id),
           request_params = COALESCE(p_actual_request_params, request_params),
           status = 'pending'
     WHERE id = p_task_id
     RETURNING * INTO v_task;
    RETURN jsonb_build_object('task_id', v_task.id, 'already_attached', FALSE);
END;
$$;

CREATE OR REPLACE FUNCTION fail_prepared_generation_task(
    p_task_id UUID,
    p_terminal_reason TEXT,
    p_error_message TEXT,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
BEGIN
    IF p_task_id IS NULL OR NULLIF(BTRIM(p_terminal_reason), '') IS NULL THEN
        RAISE EXCEPTION 'GENERATION_FAIL_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'GENERATION_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_task.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'GENERATION_TASK_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'failed' THEN
        RETURN jsonb_build_object('task_id', v_task.id, 'already_failed', TRUE);
    END IF;
    IF v_task.status <> 'preparing' THEN
        RAISE EXCEPTION 'GENERATION_FAIL_STATE_INVALID' USING ERRCODE = '55000';
    END IF;
    UPDATE tasks
       SET status = 'failed', terminal_reason = LEFT(p_terminal_reason, 200),
           error_message = LEFT(p_error_message, 1000), completed_at = NOW()
     WHERE id = p_task_id
     RETURNING * INTO v_task;
    RETURN jsonb_build_object('task_id', v_task.id, 'already_failed', FALSE);
END;
$$;

REVOKE ALL ON FUNCTION prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) FROM PUBLIC;
REVOKE ALL ON FUNCTION _prepare_generation_messages(
    TEXT, UUID, UUID, UUID, JSONB, JSONB
) FROM PUBLIC;
REVOKE ALL ON FUNCTION _prepare_generation_tasks(
    JSONB, UUID, UUID, UUID, UUID, UUID, UUID, BIGINT, UUID
) FROM PUBLIC;
REVOKE ALL ON FUNCTION attach_generation_external_task(
    UUID, TEXT, UUID, UUID, TEXT, JSONB
) FROM PUBLIC;
REVOKE ALL ON FUNCTION fail_prepared_generation_task(UUID, TEXT, TEXT, UUID) FROM PUBLIC;

COMMENT ON FUNCTION prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) IS '原子创建或验证生成请求的 Turn、输入/输出消息与本地任务';
COMMENT ON FUNCTION _prepare_generation_messages(TEXT, UUID, UUID, UUID, JSONB, JSONB)
    IS 'prepare_generation 内部消息锚点准备函数，不作为独立业务入口';
COMMENT ON FUNCTION _prepare_generation_tasks(
    JSONB, UUID, UUID, UUID, UUID, UUID, UUID, BIGINT, UUID
) IS 'prepare_generation 内部本地任务准备函数，不作为独立业务入口';
COMMENT ON FUNCTION attach_generation_external_task(UUID, TEXT, UUID, UUID, TEXT, JSONB)
    IS '把供应商任务原子附加到已准备的本地媒体任务';
COMMENT ON FUNCTION fail_prepared_generation_task(UUID, TEXT, TEXT, UUID)
    IS '把尚未提交供应商的本地生成任务幂等置为失败';
