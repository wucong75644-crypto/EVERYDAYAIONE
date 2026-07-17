-- 129: 会话附件事实源与企微 FILE 幂等暂存。

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS sender_user_id UUID REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS sender_channel_identity TEXT;

CREATE TABLE IF NOT EXISTS conversation_attachment_refs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    source_message_id UUID NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    source_provider_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    sender_user_id UUID NOT NULL REFERENCES users(id),
    original_name TEXT NOT NULL,
    url TEXT NOT NULL,
    workspace_path TEXT NOT NULL,
    storage_scope TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    reference_state TEXT NOT NULL DEFAULT 'active',
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ready_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    CONSTRAINT attachment_channel_check CHECK (channel = 'wecom'),
    CONSTRAINT attachment_storage_scope_check
        CHECK (storage_scope IN ('user', 'channel')),
    CONSTRAINT attachment_status_check
        CHECK (status IN ('receiving', 'stored', 'ready', 'failed', 'orphan')),
    CONSTRAINT attachment_reference_state_check
        CHECK (reference_state IN ('active', 'referenced', 'replaced', 'expired')),
    CONSTRAINT attachment_size_check CHECK (size >= 0),
    UNIQUE (org_id, channel, source_provider_id)
);

CREATE INDEX IF NOT EXISTS idx_attachment_active_conversation
    ON conversation_attachment_refs(conversation_id, created_at DESC)
    WHERE status = 'ready' AND reference_state = 'active';

CREATE OR REPLACE FUNCTION stage_wecom_attachment(
    p_conversation_id UUID,
    p_source_message_id UUID,
    p_source_provider_id TEXT,
    p_sender_user_id UUID,
    p_sender_channel_identity TEXT,
    p_content JSONB,
    p_original_name TEXT,
    p_url TEXT,
    p_workspace_path TEXT,
    p_storage_scope TEXT,
    p_mime_type TEXT,
    p_size BIGINT,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_attachment conversation_attachment_refs%ROWTYPE;
BEGIN
    IF p_org_id IS NULL
       OR p_conversation_id IS NULL
       OR p_source_message_id IS NULL
       OR p_sender_user_id IS NULL
       OR COALESCE(BTRIM(p_source_provider_id), '') = ''
       OR COALESCE(BTRIM(p_original_name), '') = ''
       OR COALESCE(BTRIM(p_url), '') = ''
       OR COALESCE(BTRIM(p_workspace_path), '') = ''
       OR p_storage_scope NOT IN ('user', 'channel')
       OR COALESCE(BTRIM(p_mime_type), '') = ''
       OR p_size < 0
       OR jsonb_typeof(p_content) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'WECOM_ATTACHMENT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_attachment
      FROM conversation_attachment_refs
     WHERE org_id = p_org_id
       AND channel = 'wecom'
       AND source_provider_id = BTRIM(p_source_provider_id)
     FOR UPDATE;
    IF FOUND THEN
        RETURN jsonb_build_object(
            'attachment_id', v_attachment.id,
            'message_id', v_attachment.source_message_id,
            'already_staged', TRUE
        );
    END IF;

    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = p_conversation_id
     FOR UPDATE;
    IF NOT FOUND OR v_conversation.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'WECOM_ATTACHMENT_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF (v_conversation.scope_type = 'channel') IS DISTINCT FROM
       (p_storage_scope = 'channel') THEN
        RAISE EXCEPTION 'WECOM_ATTACHMENT_STORAGE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    INSERT INTO messages(
        id, conversation_id, org_id, role, content, status,
        sender_user_id, sender_channel_identity
    ) VALUES (
        p_source_message_id, p_conversation_id, p_org_id, 'user',
        p_content, 'completed', p_sender_user_id,
        NULLIF(BTRIM(p_sender_channel_identity), '')
    );

    INSERT INTO conversation_attachment_refs(
        org_id, conversation_id, source_message_id, source_provider_id,
        channel, sender_user_id, original_name, url, workspace_path,
        storage_scope, mime_type, size, status, reference_state, ready_at
    ) VALUES (
        p_org_id, p_conversation_id, p_source_message_id,
        BTRIM(p_source_provider_id), 'wecom', p_sender_user_id,
        BTRIM(p_original_name), BTRIM(p_url), BTRIM(p_workspace_path), p_storage_scope,
        BTRIM(p_mime_type), p_size, 'ready', 'active', NOW()
    )
    RETURNING * INTO v_attachment;

    PERFORM increment_message_count(p_conversation_id, p_org_id);
    RETURN jsonb_build_object(
        'attachment_id', v_attachment.id,
        'message_id', p_source_message_id,
        'already_staged', FALSE
    );
END;
$$;

REVOKE ALL ON FUNCTION stage_wecom_attachment(
    UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, UUID
) FROM PUBLIC;
