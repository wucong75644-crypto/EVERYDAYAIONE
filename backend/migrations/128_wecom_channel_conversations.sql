-- 128: 企业微信外部会话与内部 conversation 的稳定绑定。

ALTER TABLE conversations
    ALTER COLUMN user_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS scope_type TEXT NOT NULL DEFAULT 'user',
    ADD COLUMN IF NOT EXISTS scope_id TEXT;

UPDATE conversations
   SET scope_id = user_id::TEXT
 WHERE scope_type = 'user'
   AND scope_id IS NULL
   AND user_id IS NOT NULL;

ALTER TABLE conversations
    DROP CONSTRAINT IF EXISTS conversations_scope_type_check,
    ADD CONSTRAINT conversations_scope_type_check
        CHECK (scope_type IN ('user', 'channel')),
    DROP CONSTRAINT IF EXISTS conversations_scope_owner_check,
    ADD CONSTRAINT conversations_scope_owner_check CHECK (
        (scope_type = 'user' AND user_id IS NOT NULL)
        OR (scope_type = 'channel' AND user_id IS NULL)
    );

CREATE TABLE IF NOT EXISTS conversation_channel_bindings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL UNIQUE
        REFERENCES conversations(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    corp_id TEXT NOT NULL,
    external_chat_id TEXT NOT NULL,
    chat_type TEXT NOT NULL,
    owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT channel_binding_channel_check CHECK (channel = 'wecom'),
    CONSTRAINT channel_binding_chat_type_check
        CHECK (chat_type IN ('single', 'group')),
    CONSTRAINT channel_binding_owner_check CHECK (
        (chat_type = 'single' AND owner_user_id IS NOT NULL)
        OR (chat_type = 'group' AND owner_user_id IS NULL)
    ),
    UNIQUE (org_id, channel, corp_id, external_chat_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_bindings_conversation
    ON conversation_channel_bindings(conversation_id);

CREATE OR REPLACE FUNCTION claim_legacy_wecom_conversation(
    p_user_id UUID,
    p_corp_id TEXT,
    p_external_chat_id TEXT,
    p_org_id UUID
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation_id UUID;
BEGIN
    SELECT c.id INTO v_conversation_id
      FROM conversations c
     WHERE c.org_id = p_org_id
       AND c.user_id = p_user_id
       AND c.source = 'wecom'
       AND c.scope_type = 'user'
       AND NOT EXISTS (
           SELECT 1 FROM conversation_channel_bindings b
            WHERE b.conversation_id = c.id
       )
     ORDER BY c.updated_at DESC
     LIMIT 1
     FOR UPDATE SKIP LOCKED;
    IF v_conversation_id IS NULL THEN
        RETURN NULL;
    END IF;
    UPDATE conversations
       SET scope_id = BTRIM(p_external_chat_id)
     WHERE id = v_conversation_id;
    BEGIN
        INSERT INTO conversation_channel_bindings(
            org_id, conversation_id, channel, corp_id,
            external_chat_id, chat_type, owner_user_id
        ) VALUES (
            p_org_id, v_conversation_id, 'wecom', BTRIM(p_corp_id),
            BTRIM(p_external_chat_id), 'single', p_user_id
        );
        RETURN v_conversation_id;
    EXCEPTION WHEN unique_violation THEN
        RETURN NULL;
    END;
END;
$$;

CREATE OR REPLACE FUNCTION resolve_wecom_conversation(
    p_user_id UUID, p_corp_id TEXT,
    p_external_chat_id TEXT,
    p_chat_type TEXT,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_binding conversation_channel_bindings%ROWTYPE;
    v_conversation_id UUID;
    v_owner_user_id UUID;
    v_scope_type TEXT;
BEGIN
    IF p_org_id IS NULL
       OR p_user_id IS NULL
       OR COALESCE(BTRIM(p_corp_id), '') = ''
       OR COALESCE(BTRIM(p_external_chat_id), '') = ''
       OR p_chat_type NOT IN ('single', 'group') THEN
        RAISE EXCEPTION 'WECOM_CONVERSATION_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM users WHERE id = p_user_id
    ) THEN
        RAISE EXCEPTION 'WECOM_CONVERSATION_USER_MISSING'
            USING ERRCODE = '23503';
    END IF;

    SELECT * INTO v_binding
      FROM conversation_channel_bindings
     WHERE org_id = p_org_id
       AND channel = 'wecom'
       AND corp_id = BTRIM(p_corp_id)
       AND external_chat_id = BTRIM(p_external_chat_id)
     FOR UPDATE;

    IF FOUND THEN
        IF v_binding.chat_type IS DISTINCT FROM p_chat_type
           OR (
               p_chat_type = 'single'
               AND v_binding.owner_user_id IS DISTINCT FROM p_user_id
           ) THEN
            RAISE EXCEPTION 'WECOM_CONVERSATION_BINDING_CONFLICT'
                USING ERRCODE = '23505';
        END IF;
        UPDATE conversation_channel_bindings
           SET last_seen_at = NOW()
         WHERE id = v_binding.id;
        RETURN jsonb_build_object(
            'conversation_id', v_binding.conversation_id,
            'scope_type', CASE p_chat_type
                WHEN 'group' THEN 'channel' ELSE 'user' END,
            'already_exists', TRUE
        );
    END IF;

    IF p_chat_type = 'single' THEN
        v_conversation_id := claim_legacy_wecom_conversation(
            p_user_id, p_corp_id, p_external_chat_id, p_org_id
        );
        IF v_conversation_id IS NOT NULL THEN
            RETURN jsonb_build_object(
                'conversation_id', v_conversation_id,
                'scope_type', 'user',
                'already_exists', TRUE
            );
        END IF;
    END IF;

    v_owner_user_id := CASE WHEN p_chat_type = 'single'
        THEN p_user_id ELSE NULL END;
    v_scope_type := CASE WHEN p_chat_type = 'group'
        THEN 'channel' ELSE 'user' END;

    INSERT INTO conversations(
        user_id, org_id, title, source, message_count, credits_consumed,
        scope_type, scope_id
    ) VALUES (
        v_owner_user_id, p_org_id,
        CASE WHEN p_chat_type = 'group' THEN '企微群聊' ELSE '企微对话' END,
        'wecom', 0, 0, v_scope_type, BTRIM(p_external_chat_id)
    )
    RETURNING id INTO v_conversation_id;

    BEGIN
        INSERT INTO conversation_channel_bindings(
            org_id, conversation_id, channel, corp_id,
            external_chat_id, chat_type, owner_user_id
        ) VALUES (
            p_org_id, v_conversation_id, 'wecom', BTRIM(p_corp_id),
            BTRIM(p_external_chat_id), p_chat_type, v_owner_user_id
        );
    EXCEPTION WHEN unique_violation THEN
        DELETE FROM conversations WHERE id = v_conversation_id;
        SELECT * INTO v_binding
          FROM conversation_channel_bindings
         WHERE org_id = p_org_id
           AND channel = 'wecom'
           AND corp_id = BTRIM(p_corp_id)
           AND external_chat_id = BTRIM(p_external_chat_id);
        IF NOT FOUND THEN
            RAISE;
        END IF;
        v_conversation_id := v_binding.conversation_id;
        v_scope_type := CASE v_binding.chat_type
            WHEN 'group' THEN 'channel' ELSE 'user' END;
    END;

    RETURN jsonb_build_object(
        'conversation_id', v_conversation_id,
        'scope_type', v_scope_type,
        'already_exists', FALSE
    );
END;
$$;

REVOKE ALL ON FUNCTION resolve_wecom_conversation(
    UUID, TEXT, TEXT, TEXT, UUID
) FROM PUBLIC;
REVOKE ALL ON FUNCTION claim_legacy_wecom_conversation(
    UUID, TEXT, TEXT, UUID
) FROM PUBLIC;
