-- 回滚 134：停止 Web 用户消息企微镜像，并恢复单 task/channel Outbox。

DROP TRIGGER IF EXISTS tasks_web_user_wecom_delivery_trigger ON tasks;
DROP FUNCTION IF EXISTS create_web_user_wecom_delivery();

DELETE FROM conversation_deliveries
 WHERE delivery_kind = 'web_user_message';

DROP FUNCTION IF EXISTS claim_conversation_delivery(INTEGER, INTEGER);

ALTER TABLE conversation_deliveries
    DROP CONSTRAINT IF EXISTS conversation_deliveries_task_channel_kind_unique,
    DROP CONSTRAINT IF EXISTS conversation_deliveries_kind_check,
    DROP COLUMN IF EXISTS delivery_kind,
    ADD CONSTRAINT conversation_deliveries_task_channel_unique
        UNIQUE (task_id, channel);

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
        task_id, channel, target_context
    ) VALUES (
        NEW.id, v_channel, NEW.delivery_context
    )
    ON CONFLICT (task_id, channel) DO NOTHING;
    RETURN NEW;
END;
$$;

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
        'target_context', v_delivery.target_context,
        'attempt_count', v_delivery.attempt_count,
        'delivered_items', v_delivery.delivered_items,
        'lease_token', v_token
    );
END;
$$;

REVOKE ALL ON FUNCTION claim_conversation_delivery(INTEGER, INTEGER)
    FROM PUBLIC;
