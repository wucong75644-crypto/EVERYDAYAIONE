-- 124: Conversation Actor 终态持久投递 Outbox
-- 依赖 121/122。数据库保存投递事实；Redis 仅用于 best-effort 唤醒。

CREATE TABLE IF NOT EXISTS conversation_deliveries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    target_context JSONB NOT NULL DEFAULT '{}'::JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    delivered_items JSONB NOT NULL DEFAULT '[]'::JSONB,
    last_error TEXT,
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversation_deliveries_task_channel_unique
        UNIQUE (task_id, channel),
    CONSTRAINT conversation_deliveries_channel_check
        CHECK (channel IN ('wecom')),
    CONSTRAINT conversation_deliveries_status_check
        CHECK (status IN ('pending', 'delivering', 'delivered', 'dead')),
    CONSTRAINT conversation_deliveries_attempt_check
        CHECK (attempt_count >= 0),
    CONSTRAINT conversation_deliveries_target_object_check
        CHECK (jsonb_typeof(target_context) = 'object'),
    CONSTRAINT conversation_deliveries_items_array_check
        CHECK (jsonb_typeof(delivered_items) = 'array')
);

CREATE INDEX IF NOT EXISTS idx_conversation_deliveries_claim
    ON conversation_deliveries(status, next_attempt_at, created_at)
    WHERE status IN ('pending', 'delivering');
CREATE INDEX IF NOT EXISTS idx_conversation_deliveries_expired_lease
    ON conversation_deliveries(lease_expires_at)
    WHERE status = 'delivering';

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

DROP TRIGGER IF EXISTS tasks_actor_terminal_delivery_trigger ON tasks;
CREATE TRIGGER tasks_actor_terminal_delivery_trigger
AFTER UPDATE OF status ON tasks
FOR EACH ROW
EXECUTE FUNCTION create_actor_terminal_delivery();

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
       SET status = 'dead',
           lease_token = NULL,
           lease_expires_at = NULL,
           last_error = COALESCE(last_error, 'delivery lease expired after max attempts'),
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

CREATE OR REPLACE FUNCTION renew_conversation_delivery(
    p_delivery_id UUID,
    p_lease_token UUID,
    p_lease_seconds INTEGER DEFAULT 60,
    p_delivered_items JSONB DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_delivery conversation_deliveries%ROWTYPE;
BEGIN
    IF p_lease_seconds NOT BETWEEN 15 AND 300
       OR (
           p_delivered_items IS NOT NULL
           AND jsonb_typeof(p_delivered_items) <> 'array'
       ) THEN
        RAISE EXCEPTION 'DELIVERY_RENEW_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_delivery
      FROM conversation_deliveries
     WHERE id = p_delivery_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'DELIVERY_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_delivery.status <> 'delivering'
       OR v_delivery.lease_token IS DISTINCT FROM p_lease_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_delivery.lease_expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;

    UPDATE conversation_deliveries
       SET lease_expires_at = NOW() + make_interval(secs => p_lease_seconds),
           delivered_items = COALESCE(p_delivered_items, delivered_items),
           updated_at = NOW()
     WHERE id = p_delivery_id;
    RETURN jsonb_build_object('outcome', 'renewed');
END;
$$;

CREATE OR REPLACE FUNCTION complete_conversation_delivery(
    p_delivery_id UUID,
    p_lease_token UUID,
    p_delivered_items JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_delivery conversation_deliveries%ROWTYPE;
BEGIN
    IF p_delivered_items IS NULL
       OR jsonb_typeof(p_delivered_items) <> 'array' THEN
        RAISE EXCEPTION 'DELIVERY_COMPLETE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_delivery
      FROM conversation_deliveries
     WHERE id = p_delivery_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'DELIVERY_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_delivery.status = 'delivered' THEN
        RETURN jsonb_build_object('outcome', 'already_delivered');
    END IF;
    IF v_delivery.status <> 'delivering'
       OR v_delivery.lease_token IS DISTINCT FROM p_lease_token
       OR v_delivery.lease_expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    UPDATE conversation_deliveries
       SET status = 'delivered',
           delivered_items = p_delivered_items,
           delivered_at = NOW(),
           lease_token = NULL,
           lease_expires_at = NULL,
           last_error = NULL,
           updated_at = NOW()
     WHERE id = p_delivery_id;
    RETURN jsonb_build_object('outcome', 'delivered');
END;
$$;

CREATE OR REPLACE FUNCTION fail_conversation_delivery(
    p_delivery_id UUID,
    p_lease_token UUID,
    p_error TEXT,
    p_delivered_items JSONB,
    p_max_attempts INTEGER DEFAULT 8
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_delivery conversation_deliveries%ROWTYPE;
    v_dead BOOLEAN;
    v_delay_seconds INTEGER;
BEGIN
    IF p_max_attempts < 1
       OR p_error IS NULL
       OR p_delivered_items IS NULL
       OR jsonb_typeof(p_delivered_items) <> 'array' THEN
        RAISE EXCEPTION 'DELIVERY_FAIL_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_delivery
      FROM conversation_deliveries
     WHERE id = p_delivery_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'DELIVERY_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_delivery.status <> 'delivering'
       OR v_delivery.lease_token IS DISTINCT FROM p_lease_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    v_dead := v_delivery.attempt_count >= p_max_attempts;
    v_delay_seconds := LEAST(
        900,
        5 * CAST(POWER(2, GREATEST(v_delivery.attempt_count - 1, 0)) AS INTEGER)
    );
    UPDATE conversation_deliveries
       SET status = CASE WHEN v_dead THEN 'dead' ELSE 'pending' END,
           delivered_items = p_delivered_items,
           next_attempt_at = CASE
               WHEN v_dead THEN next_attempt_at
               ELSE NOW() + make_interval(secs => v_delay_seconds)
           END,
           lease_token = NULL,
           lease_expires_at = NULL,
           last_error = LEFT(p_error, 2000),
           updated_at = NOW()
     WHERE id = p_delivery_id;
    RETURN jsonb_build_object(
        'outcome', CASE WHEN v_dead THEN 'dead' ELSE 'retry_scheduled' END,
        'retry_seconds', CASE WHEN v_dead THEN NULL ELSE v_delay_seconds END
    );
END;
$$;

REVOKE ALL ON FUNCTION claim_conversation_delivery(INTEGER, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION renew_conversation_delivery(UUID, UUID, INTEGER, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION complete_conversation_delivery(UUID, UUID, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION fail_conversation_delivery(UUID, UUID, TEXT, JSONB, INTEGER) FROM PUBLIC;

COMMENT ON TABLE conversation_deliveries
    IS 'Conversation Actor 终态的事务 Outbox；投递采用租约、fencing 和 at-least-once';
