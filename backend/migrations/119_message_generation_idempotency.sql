-- 119: 消息生成请求幂等记录
-- 在消息、任务和积分副作用前原子抢占一次请求的执行权。

CREATE TABLE IF NOT EXISTS message_generation_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    idempotency_key VARCHAR(100) NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 100),
    request_fingerprint CHAR(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    status VARCHAR(20) NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'completed', 'failed')),
    client_task_id VARCHAR(100) NOT NULL,
    user_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    assistant_message_id UUID NOT NULL,
    response_status SMALLINT CHECK (response_status BETWEEN 100 AND 599),
    response_body JSONB,
    error_code VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '24 hours'
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_message_generation_requests_org_key
    ON message_generation_requests(org_id, user_id, idempotency_key)
    WHERE org_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_message_generation_requests_personal_key
    ON message_generation_requests(user_id, idempotency_key)
    WHERE org_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_message_generation_requests_expiry
    ON message_generation_requests(expires_at);

CREATE INDEX IF NOT EXISTS idx_message_generation_requests_assistant
    ON message_generation_requests(assistant_message_id);

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
SET search_path = public
AS $$
DECLARE
    v_request message_generation_requests%ROWTYPE;
    v_outcome TEXT;
BEGIN
    IF p_idempotency_key IS NULL OR length(p_idempotency_key) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'IDEMPOTENCY_KEY_INVALID' USING ERRCODE = '22023';
    END IF;
    IF p_request_fingerprint IS NULL OR p_request_fingerprint !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'IDEMPOTENCY_FINGERPRINT_INVALID' USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM conversations conversation
         WHERE conversation.id = p_conversation_id
           AND conversation.user_id = p_user_id
           AND conversation.org_id IS NOT DISTINCT FROM p_org_id
    ) THEN
        RAISE EXCEPTION 'IDEMPOTENCY_CONVERSATION_ACCESS_DENIED' USING ERRCODE = '42501';
    END IF;

    BEGIN
        INSERT INTO message_generation_requests(
            org_id,
            user_id,
            conversation_id,
            idempotency_key,
            request_fingerprint,
            client_task_id,
            assistant_message_id
        ) VALUES (
            p_org_id,
            p_user_id,
            p_conversation_id,
            p_idempotency_key,
            p_request_fingerprint,
            p_client_task_id,
            p_assistant_message_id
        ) RETURNING * INTO v_request;

        v_outcome := 'claimed';
    EXCEPTION WHEN unique_violation THEN
        SELECT *
          INTO v_request
          FROM message_generation_requests request
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

REVOKE ALL ON FUNCTION claim_message_generation_request(
    UUID, UUID, UUID, VARCHAR, CHAR, VARCHAR, UUID
) FROM PUBLIC;

CREATE OR REPLACE FUNCTION cleanup_expired_message_generation_requests()
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_deleted BIGINT;
BEGIN
    DELETE FROM message_generation_requests
     WHERE expires_at < NOW();
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;

REVOKE ALL ON FUNCTION cleanup_expired_message_generation_requests() FROM PUBLIC;

COMMENT ON TABLE message_generation_requests
    IS '消息生成 POST 的幂等执行权、请求指纹和可重放终态';
COMMENT ON FUNCTION claim_message_generation_request(
    UUID, UUID, UUID, VARCHAR, CHAR, VARCHAR, UUID
) IS '按用户和企业空间原子抢占消息生成请求；重复请求返回现有状态';
COMMENT ON FUNCTION cleanup_expired_message_generation_requests()
IS '删除超过 24 小时保留期的消息生成幂等记录';
