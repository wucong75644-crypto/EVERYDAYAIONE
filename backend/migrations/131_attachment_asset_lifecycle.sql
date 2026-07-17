-- 131: 统一附件资产身份、集合生命周期与任务不可变引用。

ALTER TABLE conversation_attachment_refs
    ADD COLUMN IF NOT EXISTS provider_name TEXT,
    ADD COLUMN IF NOT EXISTS canonical_name TEXT,
    ADD COLUMN IF NOT EXISTS detected_mime_type TEXT,
    ADD COLUMN IF NOT EXISTS detection_source TEXT,
    ADD COLUMN IF NOT EXISTS content_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS attachment_set_id UUID,
    ADD COLUMN IF NOT EXISTS last_referenced_at TIMESTAMPTZ;

UPDATE conversation_attachment_refs
   SET provider_name = COALESCE(provider_name, original_name),
       canonical_name = COALESCE(canonical_name, original_name),
       detected_mime_type = COALESCE(detected_mime_type, mime_type),
       detection_source = COALESCE(detection_source, 'legacy');

WITH conversation_sets AS (
    SELECT conversation_id, gen_random_uuid() AS set_id
      FROM conversation_attachment_refs
     GROUP BY conversation_id
)
UPDATE conversation_attachment_refs a
   SET attachment_set_id = s.set_id,
       reference_state = CASE
           WHEN a.status = 'ready' THEN 'active'
           ELSE a.reference_state
       END
  FROM conversation_sets s
 WHERE a.conversation_id = s.conversation_id
   AND a.attachment_set_id IS NULL;

ALTER TABLE conversation_attachment_refs
    ALTER COLUMN canonical_name SET NOT NULL,
    ALTER COLUMN detected_mime_type SET NOT NULL,
    ALTER COLUMN detection_source SET NOT NULL,
    ALTER COLUMN attachment_set_id SET NOT NULL;

CREATE TABLE IF NOT EXISTS task_attachment_refs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    turn_id UUID NOT NULL,
    input_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    attachment_id UUID NOT NULL
        REFERENCES conversation_attachment_refs(id) ON DELETE RESTRICT,
    attachment_set_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_id, attachment_id)
);

CREATE INDEX IF NOT EXISTS idx_task_attachment_input
    ON task_attachment_refs(input_message_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attachment_active_set
    ON conversation_attachment_refs(
        conversation_id, attachment_set_id, created_at
    )
    WHERE status = 'ready' AND reference_state = 'active';

INSERT INTO task_attachment_refs(
    org_id, task_id, turn_id, input_message_id, attachment_id,
    attachment_set_id
)
SELECT t.org_id, t.id, t.turn_id, t.input_message_id, a.id,
       a.attachment_set_id
  FROM tasks t
  JOIN messages m ON m.id = t.input_message_id
  CROSS JOIN LATERAL jsonb_array_elements(m.content::JSONB) part
  JOIN conversation_attachment_refs a ON a.id = CASE
       WHEN part->>'asset_id' ~
            '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
       THEN (part->>'asset_id')::UUID
       ELSE NULL
   END
 WHERE t.turn_id IS NOT NULL
   AND t.input_message_id IS NOT NULL
   AND part->>'type' = 'file'
ON CONFLICT (task_id, attachment_id) DO NOTHING;

CREATE OR REPLACE FUNCTION stage_wecom_attachment_v2(
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
    p_asset_identity JSONB,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_existing conversation_attachment_refs%ROWTYPE;
    v_attachment_id UUID := gen_random_uuid();
    v_set_id UUID;
    v_content JSONB;
    v_hash TEXT;
BEGIN
    v_hash := NULLIF(BTRIM(p_asset_identity->>'content_sha256'), '');
    IF p_org_id IS NULL OR p_conversation_id IS NULL
       OR p_source_message_id IS NULL OR p_sender_user_id IS NULL
       OR COALESCE(BTRIM(p_source_provider_id), '') = ''
       OR jsonb_typeof(p_content) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_content) <> 1
       OR jsonb_typeof(p_asset_identity) IS DISTINCT FROM 'object'
       OR COALESCE(p_asset_identity->>'canonical_name', '') = ''
       OR COALESCE(p_asset_identity->>'detected_mime_type', '') = ''
       OR COALESCE(p_asset_identity->>'detection_source', '') = ''
       OR v_hash !~ '^[0-9a-f]{64}$'
       OR COALESCE(BTRIM(p_url), '') = ''
       OR COALESCE(BTRIM(p_workspace_path), '') = ''
       OR p_storage_scope NOT IN ('user', 'channel')
       OR p_size < 0 THEN
        RAISE EXCEPTION 'WECOM_ATTACHMENT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_existing
      FROM conversation_attachment_refs
     WHERE org_id = p_org_id AND channel = 'wecom'
       AND source_provider_id = BTRIM(p_source_provider_id)
     FOR UPDATE;
    IF FOUND THEN
        IF v_existing.conversation_id IS DISTINCT FROM p_conversation_id
           OR v_existing.sender_user_id IS DISTINCT FROM p_sender_user_id THEN
            RAISE EXCEPTION 'WECOM_ATTACHMENT_REPLAY_SCOPE_MISMATCH'
                USING ERRCODE = '42501';
        END IF;
        IF v_existing.content_sha256 IS NOT NULL
           AND v_existing.content_sha256 IS DISTINCT FROM v_hash THEN
            RAISE EXCEPTION 'WECOM_ATTACHMENT_CONTENT_CONFLICT'
                USING ERRCODE = '23505';
        END IF;
        UPDATE conversation_attachment_refs
           SET provider_name = COALESCE(
                   NULLIF(p_asset_identity->>'provider_name', ''),
                   provider_name
               ),
               canonical_name = p_asset_identity->>'canonical_name',
               detected_mime_type = p_asset_identity->>'detected_mime_type',
               detection_source = p_asset_identity->>'detection_source',
               content_sha256 = v_hash,
               original_name = p_asset_identity->>'canonical_name',
               mime_type = p_asset_identity->>'detected_mime_type'
         WHERE id = v_existing.id;
        v_content := jsonb_set(
            p_content, '{0,asset_id}',
            to_jsonb(v_existing.id::TEXT), TRUE
        );
        UPDATE messages
           SET content = v_content
         WHERE id = v_existing.source_message_id
           AND conversation_id = p_conversation_id
           AND org_id = p_org_id;
        RETURN jsonb_build_object(
            'attachment_id', v_existing.id,
            'message_id', v_existing.source_message_id,
            'attachment_set_id', v_existing.attachment_set_id,
            'already_staged', TRUE
        );
    END IF;

    SELECT * INTO v_conversation FROM conversations
     WHERE id = p_conversation_id FOR UPDATE;
    IF NOT FOUND OR v_conversation.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'WECOM_ATTACHMENT_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF (v_conversation.scope_type = 'channel') IS DISTINCT FROM
       (p_storage_scope = 'channel') THEN
        RAISE EXCEPTION 'WECOM_ATTACHMENT_STORAGE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    SELECT attachment_set_id INTO v_set_id
      FROM conversation_attachment_refs a
     WHERE a.conversation_id = p_conversation_id
       AND a.org_id = p_org_id AND a.status = 'ready'
       AND a.reference_state = 'active'
     ORDER BY a.created_at DESC LIMIT 1;
    IF v_set_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM task_attachment_refs r
         WHERE r.attachment_set_id = v_set_id
    ) THEN
        UPDATE conversation_attachment_refs
           SET reference_state = 'replaced'
         WHERE conversation_id = p_conversation_id
           AND org_id = p_org_id
           AND attachment_set_id = v_set_id
           AND reference_state = 'active';
        v_set_id := NULL;
    END IF;
    v_set_id := COALESCE(v_set_id, gen_random_uuid());
    v_content := jsonb_set(
        p_content, '{0,asset_id}', to_jsonb(v_attachment_id::TEXT), TRUE
    );

    INSERT INTO messages(
        id, conversation_id, org_id, role, content, status,
        sender_user_id, sender_channel_identity
    ) VALUES (
        p_source_message_id, p_conversation_id, p_org_id, 'user',
        v_content, 'completed', p_sender_user_id,
        NULLIF(BTRIM(p_sender_channel_identity), '')
    );
    INSERT INTO conversation_attachment_refs(
        id, org_id, conversation_id, source_message_id, source_provider_id,
        channel, sender_user_id, original_name, url, workspace_path,
        storage_scope, mime_type, size, status, reference_state, ready_at,
        provider_name, canonical_name, detected_mime_type, detection_source,
        content_sha256, attachment_set_id
    ) VALUES (
        v_attachment_id, p_org_id, p_conversation_id, p_source_message_id,
        BTRIM(p_source_provider_id), 'wecom', p_sender_user_id,
        p_asset_identity->>'canonical_name', BTRIM(p_url),
        BTRIM(p_workspace_path), p_storage_scope,
        p_asset_identity->>'detected_mime_type', p_size, 'ready', 'active',
        NOW(), NULLIF(p_asset_identity->>'provider_name', ''),
        p_asset_identity->>'canonical_name',
        p_asset_identity->>'detected_mime_type',
        p_asset_identity->>'detection_source', v_hash, v_set_id
    );
    PERFORM increment_message_count(p_conversation_id, p_org_id);
    RETURN jsonb_build_object(
        'attachment_id', v_attachment_id,
        'message_id', p_source_message_id,
        'attachment_set_id', v_set_id,
        'already_staged', FALSE
    );
END;
$$;

CREATE OR REPLACE FUNCTION current_attachment_parts(
    p_conversation_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE sql
SECURITY INVOKER
SET search_path = public
AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'type', 'file', 'url', a.url,
        'workspace_path', a.workspace_path,
        'name', a.canonical_name,
        'mime_type', a.detected_mime_type,
        'size', a.size, 'asset_id', a.id
    ) ORDER BY a.created_at), '[]'::JSONB)
      FROM conversation_attachment_refs a
     WHERE a.conversation_id = p_conversation_id
       AND a.org_id = p_org_id
       AND a.status = 'ready'
       AND a.reference_state = 'active'
       AND a.attachment_set_id = (
           SELECT attachment_set_id
             FROM conversation_attachment_refs
            WHERE conversation_id = p_conversation_id
              AND org_id = p_org_id AND status = 'ready'
              AND reference_state = 'active'
            ORDER BY created_at DESC LIMIT 1
       );
$$;

CREATE OR REPLACE FUNCTION bind_task_attachments(
    p_task_id UUID,
    p_turn_id UUID,
    p_input_message_id UUID,
    p_conversation_id UUID,
    p_org_id UUID
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    INSERT INTO task_attachment_refs(
        org_id, task_id, turn_id, input_message_id, attachment_id,
        attachment_set_id
    )
    SELECT p_org_id, p_task_id, p_turn_id, p_input_message_id, a.id,
           a.attachment_set_id
      FROM conversation_attachment_refs a
     WHERE a.conversation_id = p_conversation_id
       AND a.org_id = p_org_id AND a.status = 'ready'
       AND a.reference_state = 'active'
       AND a.attachment_set_id = (
           SELECT attachment_set_id
             FROM conversation_attachment_refs
            WHERE conversation_id = p_conversation_id
              AND org_id = p_org_id AND status = 'ready'
              AND reference_state = 'active'
            ORDER BY created_at DESC LIMIT 1
       )
    ON CONFLICT (task_id, attachment_id) DO NOTHING;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    UPDATE conversation_attachment_refs a
       SET last_referenced_at = NOW()
     WHERE EXISTS (
         SELECT 1 FROM task_attachment_refs r
          WHERE r.task_id = p_task_id AND r.attachment_id = a.id
     );
    RETURN v_count;
END;
$$;

CREATE OR REPLACE FUNCTION enqueue_wecom_generation_turn_v2(
    p_task_data JSONB,
    p_input_message_id UUID,
    p_output_message_id UUID,
    p_turn_id UUID,
    p_input_content JSONB,
    p_delivery_context JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_input messages%ROWTYPE;
    v_conversation_id UUID;
    v_org_id UUID;
    v_user_id UUID;
    v_task_id UUID;
    v_content JSONB;
    v_result JSONB;
BEGIN
    BEGIN
        v_conversation_id := (p_task_data->>'conversation_id')::UUID;
        v_org_id := NULLIF(p_task_data->>'org_id', '')::UUID;
        v_user_id := (p_task_data->>'user_id')::UUID;
        v_task_id := (p_task_data->>'id')::UUID;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'WECOM_ACTOR_ENQUEUE_ID_INVALID'
            USING ERRCODE = '22023';
    END;
    IF jsonb_typeof(p_input_content) IS DISTINCT FROM 'array'
       OR p_input_message_id IS NULL OR p_output_message_id IS NULL
       OR p_turn_id IS NULL OR v_task_id IS NULL
       OR jsonb_typeof(p_delivery_context) IS DISTINCT FROM 'object'
       OR NOT (
           p_delivery_context @> '{"actor":true,"channel":"wecom"}'::JSONB
       )
       OR COALESCE(p_delivery_context->>'chatid', '') = ''
       OR COALESCE(p_delivery_context->>'transport', '')
          NOT IN ('smart_robot', 'app') THEN
        RAISE EXCEPTION 'WECOM_ACTOR_ENQUEUE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_conversation_id FOR UPDATE;
    IF NOT FOUND OR v_conversation.org_id IS DISTINCT FROM v_org_id
       OR v_conversation.source IS DISTINCT FROM 'wecom'
       OR (
           v_conversation.scope_type = 'user'
           AND v_conversation.user_id IS DISTINCT FROM v_user_id
       )
       OR (
           v_conversation.scope_type = 'channel'
           AND NOT EXISTS (
               SELECT 1 FROM conversation_channel_bindings b
                WHERE b.conversation_id = v_conversation_id
                  AND b.org_id = v_org_id
                  AND b.corp_id = p_delivery_context->>'corp_id'
                  AND b.external_chat_id = p_delivery_context->>'chatid'
                  AND b.chat_type = 'group'
           )
       ) THEN
        RAISE EXCEPTION 'WECOM_ACTOR_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_input FROM messages
     WHERE id = p_input_message_id FOR UPDATE;
    IF v_input.id IS NULL THEN
        v_content := p_input_content
            || current_attachment_parts(v_conversation_id, v_org_id);
        INSERT INTO messages(
            id, conversation_id, org_id, role, content, status, turn_id,
            sender_user_id, sender_channel_identity
        ) VALUES (
            p_input_message_id, v_conversation_id, v_org_id, 'user',
            v_content, 'completed', p_turn_id, v_user_id,
            NULLIF(p_delivery_context->>'wecom_userid', '')
        ) RETURNING * INTO v_input;
        INSERT INTO messages(
            id, conversation_id, org_id, role, content, status, turn_id,
            reply_to_message_id
        ) VALUES (
            p_output_message_id, v_conversation_id, v_org_id, 'assistant',
            '[{"type":"text","text":""}]', 'generating', p_turn_id,
            p_input_message_id
        ) ON CONFLICT (id) DO NOTHING;
        SELECT enqueue_wecom_task_record(
            p_task_data, p_input_message_id, p_turn_id, 'serial',
            p_delivery_context
        ) INTO v_result;
        PERFORM bind_task_attachments(
            v_task_id, p_turn_id, p_input_message_id,
            v_conversation_id, v_org_id
        );
        IF COALESCE((v_result->>'already_enqueued')::BOOLEAN, FALSE) = FALSE THEN
            PERFORM increment_message_count(v_conversation_id, v_org_id);
        END IF;
    ELSE
        v_content := v_input.content;
        IF v_input.conversation_id IS DISTINCT FROM v_conversation_id
           OR v_input.org_id IS DISTINCT FROM v_org_id
           OR v_input.role::TEXT <> 'user'
           OR v_input.turn_id IS DISTINCT FROM p_turn_id
           OR (
               SELECT COALESCE(
                   jsonb_agg(value ORDER BY ordinality), '[]'::JSONB
               )
                 FROM jsonb_array_elements(v_content)
                      WITH ORDINALITY AS item(value, ordinality)
                WHERE ordinality <= jsonb_array_length(p_input_content)
           ) IS DISTINCT FROM p_input_content THEN
            RAISE EXCEPTION 'WECOM_ACTOR_MESSAGE_CONFLICT'
                USING ERRCODE = '23505';
        END IF;
        SELECT enqueue_wecom_task_record(
            p_task_data, p_input_message_id, p_turn_id, 'serial',
            p_delivery_context
        ) INTO v_result;
    END IF;
    RETURN v_result || jsonb_build_object(
        'input_message_id', p_input_message_id,
        'output_message_id', p_output_message_id,
        'attachment_count', jsonb_array_length(v_content)
            - jsonb_array_length(p_input_content)
    );
END;
$$;

CREATE OR REPLACE FUNCTION enqueue_wecom_generation_turn_v2(
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
BEGIN
    IF NULLIF(p_task_data->>'org_id', '')::UUID IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'WECOM_ACTOR_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN enqueue_wecom_generation_turn_v2(
        p_task_data, p_input_message_id, p_output_message_id, p_turn_id,
        p_input_content, p_delivery_context
    );
END;
$$;

REVOKE ALL ON FUNCTION stage_wecom_attachment_v2(
    UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, JSONB, UUID
) FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB
) FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
) FROM PUBLIC;
