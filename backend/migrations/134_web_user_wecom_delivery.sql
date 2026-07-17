-- 134: Web 用户消息通过事务 Outbox 镜像到已绑定的企业微信会话。
-- 依赖 124/128。目标上下文只复制真实企微 task，不推导或猜测通道地址。

ALTER TABLE conversation_deliveries
    ADD COLUMN IF NOT EXISTS delivery_kind TEXT
        NOT NULL DEFAULT 'assistant_terminal';

ALTER TABLE conversation_deliveries
    DROP CONSTRAINT IF EXISTS conversation_deliveries_task_channel_unique,
    DROP CONSTRAINT IF EXISTS conversation_deliveries_task_channel_kind_unique,
    ADD CONSTRAINT conversation_deliveries_task_channel_kind_unique
        UNIQUE (task_id, channel, delivery_kind),
    DROP CONSTRAINT IF EXISTS conversation_deliveries_kind_check,
    ADD CONSTRAINT conversation_deliveries_kind_check
        CHECK (delivery_kind IN ('assistant_terminal', 'web_user_message'));

CREATE OR REPLACE FUNCTION create_actor_terminal_delivery()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_channel TEXT;
BEGIN
    IF NEW.status NOT IN ('completed', 'failed')
       OR OLD.status IS NOT DISTINCT FROM NEW.status
       OR NOT (NEW.delivery_context @> '{"actor": true}'::JSONB) THEN
        RETURN NEW;
    END IF;

    v_channel := NEW.delivery_context->>'channel';
    IF v_channel <> 'wecom' THEN
        RETURN NEW;
    END IF;

    INSERT INTO conversation_deliveries(
        task_id, channel, delivery_kind, target_context
    ) VALUES (
        NEW.id, v_channel, 'assistant_terminal', NEW.delivery_context
    )
    ON CONFLICT (task_id, channel, delivery_kind) DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION create_web_user_wecom_delivery()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_target_context JSONB;
BEGIN
    IF NOT (
        NEW.delivery_context @> '{"actor":true,"channel":"web"}'::JSONB
    ) THEN
        RETURN NEW;
    END IF;

    SELECT t.delivery_context
               - 'stream_task_id' - 'stream_req_id'
               - 'stream_id' - 'stream_started_at'
      INTO v_target_context
      FROM conversations c
      JOIN conversation_channel_bindings b
        ON b.conversation_id = c.id
       AND b.org_id = c.org_id
       AND b.channel = 'wecom'
      JOIN tasks t
        ON t.conversation_id = c.id
       AND t.org_id IS NOT DISTINCT FROM c.org_id
     WHERE c.id = NEW.conversation_id
       AND c.source = 'wecom'
       AND t.delivery_context @> '{"actor":true,"channel":"wecom"}'::JSONB
       AND t.delivery_context->>'corp_id' = b.corp_id
       AND t.delivery_context->>'chatid' = b.external_chat_id
       AND t.delivery_context->>'chattype' = b.chat_type
       AND t.delivery_context->>'transport' IN ('smart_robot', 'app')
     ORDER BY t.created_at DESC, t.id DESC
     LIMIT 1;

    IF v_target_context IS NULL THEN
        RETURN NEW;
    END IF;

    INSERT INTO conversation_deliveries(
        task_id, channel, delivery_kind, target_context
    ) VALUES (
        NEW.id, 'wecom', 'web_user_message', v_target_context
    )
    ON CONFLICT (task_id, channel, delivery_kind) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tasks_web_user_wecom_delivery_trigger ON tasks;
CREATE TRIGGER tasks_web_user_wecom_delivery_trigger
AFTER INSERT ON tasks
FOR EACH ROW
EXECUTE FUNCTION create_web_user_wecom_delivery();

CREATE OR REPLACE FUNCTION claim_conversation_delivery(
    p_lease_seconds INTEGER DEFAULT 60,
    p_max_attempts INTEGER DEFAULT 8
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_delivery conversation_deliveries%ROWTYPE;
    v_token UUID;
BEGIN
    IF p_lease_seconds NOT BETWEEN 15 AND 300 OR p_max_attempts < 1 THEN
        RAISE EXCEPTION 'DELIVERY_CLAIM_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    UPDATE conversation_deliveries
       SET status = 'dead', lease_token = NULL, lease_expires_at = NULL,
           last_error = COALESCE(
               last_error, 'delivery lease expired after max attempts'
           ),
           updated_at = NOW()
     WHERE status = 'delivering'
       AND lease_expires_at IS NOT NULL
       AND lease_expires_at <= NOW()
       AND attempt_count >= p_max_attempts;

    SELECT * INTO v_delivery
      FROM conversation_deliveries
     WHERE (
            status = 'pending'
            OR (
                status = 'delivering'
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at <= NOW()
            )
       )
       AND next_attempt_at <= NOW()
       AND attempt_count < p_max_attempts
     ORDER BY next_attempt_at, created_at, id
     FOR UPDATE SKIP LOCKED
     LIMIT 1;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'empty');
    END IF;

    v_token := uuid_generate_v4();
    UPDATE conversation_deliveries
       SET status = 'delivering',
           attempt_count = attempt_count + 1,
           lease_token = v_token,
           lease_expires_at = NOW() + make_interval(secs => p_lease_seconds),
           updated_at = NOW()
     WHERE id = v_delivery.id
     RETURNING * INTO v_delivery;

    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'delivery_id', v_delivery.id,
        'task_id', v_delivery.task_id,
        'channel', v_delivery.channel,
        'delivery_kind', v_delivery.delivery_kind,
        'target_context', v_delivery.target_context,
        'attempt_count', v_delivery.attempt_count,
        'delivered_items', v_delivery.delivered_items,
        'lease_token', v_token
    );
END;
$$;

REVOKE ALL ON FUNCTION claim_conversation_delivery(INTEGER, INTEGER)
    FROM PUBLIC;

COMMENT ON COLUMN conversation_deliveries.delivery_kind
    IS 'assistant_terminal=AI 终态；web_user_message=Web 用户输入镜像';
COMMENT ON FUNCTION create_web_user_wecom_delivery()
    IS 'Web task 入队事务内，按真实企微会话上下文创建用户消息镜像 Outbox';
