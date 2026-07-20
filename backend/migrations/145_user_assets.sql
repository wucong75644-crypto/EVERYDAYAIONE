-- 145: canonical 用户资产与多来源关联。
CREATE TABLE IF NOT EXISTS user_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    storage_scope TEXT NOT NULL CHECK (storage_scope IN ('user', 'channel')),
    storage_owner_key TEXT NOT NULL CHECK (
        (
            storage_scope = 'user'
            AND storage_owner_key ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )
        OR (
            storage_scope = 'channel'
            AND storage_owner_key ~ '^channels/wecom/[0-9a-f]{24}$'
        )
    ),
    storage_provider TEXT NOT NULL CHECK (storage_provider IN ('workspace', 'oss')),
    storage_key TEXT NOT NULL CHECK (BTRIM(storage_key) <> ''),
    media_type TEXT NOT NULL CHECK (media_type IN ('image', 'video', 'file')),
    status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'deleted')),
    original_url TEXT NOT NULL CHECK (BTRIM(original_url) <> ''),
    thumbnail_url TEXT,
    download_url TEXT NOT NULL CHECK (BTRIM(download_url) <> ''),
    workspace_path TEXT,
    name TEXT NOT NULL CHECK (BTRIM(name) <> ''),
    mime_type TEXT,
    size BIGINT CHECK (size IS NULL OR size >= 0),
    content_sha256 TEXT CHECK (
        content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'
    ),
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_user_assets_storage_identity
    ON user_assets (
        COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::UUID),
        storage_scope, storage_owner_key, storage_provider, storage_key
    );
CREATE INDEX IF NOT EXISTS idx_user_assets_admin_cursor
    ON user_assets(created_at DESC, id DESC) WHERE status = 'ready';
CREATE INDEX IF NOT EXISTS idx_user_assets_admin_media_cursor
    ON user_assets(media_type, created_at DESC, id DESC)
    WHERE status = 'ready';

CREATE TABLE IF NOT EXISTS user_asset_refs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ref_key TEXT NOT NULL UNIQUE CHECK (BTRIM(ref_key) <> ''),
    asset_id UUID NOT NULL REFERENCES user_assets(id) ON DELETE CASCADE,
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (source_type IN ('upload', 'generated')),
    source_kind TEXT NOT NULL CHECK (source_kind IN (
        'web_upload', 'wecom_upload', 'image_task', 'video_task',
        'media_tool', 'ecom_image'
    )),
    ref_kind TEXT NOT NULL CHECK (ref_kind IN (
        'upload', 'task', 'message', 'image_generation', 'attachment'
    )),
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    source_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    source_task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    source_generation_id UUID REFERENCES image_generations(id) ON DELETE SET NULL,
    source_attachment_id UUID
        REFERENCES conversation_attachment_refs(id) ON DELETE SET NULL,
    content_index INTEGER CHECK (content_index IS NULL OR content_index >= 0),
    model_id TEXT,
    prompt TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_asset_refs_admin
    ON user_asset_refs(actor_user_id, source_type, asset_id);
CREATE INDEX IF NOT EXISTS idx_user_asset_refs_message
    ON user_asset_refs(source_message_id) WHERE source_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_user_asset_refs_task
    ON user_asset_refs(source_task_id) WHERE source_task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_user_asset_refs_generation
    ON user_asset_refs(source_generation_id)
    WHERE source_generation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_user_asset_refs_attachment
    ON user_asset_refs(source_attachment_id)
    WHERE source_attachment_id IS NOT NULL;

CREATE OR REPLACE FUNCTION _resolve_user_asset(
    p_org_id UUID,
    p_storage_scope TEXT,
    p_storage_owner_key TEXT,
    p_storage_provider TEXT,
    p_storage_key TEXT,
    p_media_type TEXT,
    p_original_url TEXT,
    p_thumbnail_url TEXT,
    p_download_url TEXT,
    p_workspace_path TEXT,
    p_name TEXT,
    p_mime_type TEXT,
    p_size BIGINT,
    p_content_sha256 TEXT,
    p_asset_metadata JSONB,
    p_created_at TIMESTAMPTZ
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $function$
DECLARE
    v_asset user_assets%ROWTYPE;
    v_created BOOLEAN := FALSE;
BEGIN
    SELECT * INTO v_asset
      FROM user_assets
     WHERE org_id IS NOT DISTINCT FROM p_org_id
       AND storage_scope = p_storage_scope
       AND storage_owner_key = p_storage_owner_key
       AND storage_provider = p_storage_provider
       AND storage_key = p_storage_key
     FOR UPDATE;

    IF v_asset.id IS NULL THEN
        BEGIN
            INSERT INTO user_assets (
                org_id, storage_scope, storage_owner_key, storage_provider,
                storage_key, media_type, original_url, thumbnail_url,
                download_url, workspace_path, name, mime_type, size,
                content_sha256, metadata, created_at, updated_at
            ) VALUES (
                p_org_id, p_storage_scope, p_storage_owner_key,
                p_storage_provider, p_storage_key, p_media_type,
                p_original_url, p_thumbnail_url, p_download_url,
                p_workspace_path, p_name, p_mime_type, p_size,
                p_content_sha256, COALESCE(p_asset_metadata, '{}'::JSONB),
                COALESCE(p_created_at, NOW()), NOW()
            ) RETURNING * INTO v_asset;
            v_created := TRUE;
        EXCEPTION WHEN unique_violation THEN
            SELECT * INTO v_asset
              FROM user_assets
             WHERE org_id IS NOT DISTINCT FROM p_org_id
               AND storage_scope = p_storage_scope
               AND storage_owner_key = p_storage_owner_key
               AND storage_provider = p_storage_provider
               AND storage_key = p_storage_key
             FOR UPDATE;
        END;
    END IF;

    IF v_asset.id IS NULL OR v_asset.media_type <> p_media_type THEN
        RAISE EXCEPTION 'USER_ASSET_IDENTITY_CONFLICT';
    END IF;

    UPDATE user_assets SET
        thumbnail_url = COALESCE(user_assets.thumbnail_url, p_thumbnail_url),
        workspace_path = COALESCE(user_assets.workspace_path, p_workspace_path),
        mime_type = COALESCE(user_assets.mime_type, p_mime_type),
        size = COALESCE(user_assets.size, p_size),
        content_sha256 = COALESCE(
            user_assets.content_sha256, p_content_sha256
        ),
        metadata = user_assets.metadata || COALESCE(
            p_asset_metadata, '{}'::JSONB
        ),
        updated_at = NOW()
    WHERE id = v_asset.id
    RETURNING * INTO v_asset;

    RETURN jsonb_build_object(
        'asset', to_jsonb(v_asset), 'created', v_created
    );
END
$function$;

CREATE OR REPLACE FUNCTION _bind_user_asset_ref(
    p_asset_id UUID, p_ref_key TEXT, p_actor_user_id UUID, p_org_id UUID,
    p_source_type TEXT, p_source_kind TEXT, p_ref_kind TEXT,
    p_conversation_id UUID, p_source_message_id UUID,
    p_source_task_id UUID, p_source_generation_id UUID,
    p_source_attachment_id UUID, p_content_index INTEGER,
    p_model_id TEXT, p_prompt TEXT, p_ref_metadata JSONB,
    p_created_at TIMESTAMPTZ
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $function$
DECLARE
    v_ref user_asset_refs%ROWTYPE;
    v_created BOOLEAN := FALSE;
BEGIN
    SELECT * INTO v_ref
      FROM user_asset_refs
     WHERE ref_key = p_ref_key
     FOR UPDATE;

    IF v_ref.id IS NULL THEN
        BEGIN
            INSERT INTO user_asset_refs (
                ref_key, asset_id, actor_user_id, org_id, source_type,
                source_kind, ref_kind, conversation_id, source_message_id,
                source_task_id, source_generation_id, source_attachment_id,
                content_index, model_id, prompt, metadata, created_at,
                updated_at
            ) VALUES (
                p_ref_key, p_asset_id, p_actor_user_id, p_org_id,
                p_source_type, p_source_kind, p_ref_kind, p_conversation_id,
                p_source_message_id, p_source_task_id,
                p_source_generation_id, p_source_attachment_id,
                p_content_index, p_model_id, p_prompt,
                COALESCE(p_ref_metadata, '{}'::JSONB),
                COALESCE(p_created_at, NOW()), NOW()
            ) RETURNING * INTO v_ref;
            v_created := TRUE;
        EXCEPTION WHEN unique_violation THEN
            SELECT * INTO v_ref
              FROM user_asset_refs
             WHERE ref_key = p_ref_key
             FOR UPDATE;
        END;
    END IF;

    IF v_ref.id IS NULL
       OR v_ref.asset_id <> p_asset_id
       OR v_ref.actor_user_id <> p_actor_user_id
       OR v_ref.org_id IS DISTINCT FROM p_org_id
       OR v_ref.source_type <> p_source_type
       OR v_ref.source_kind <> p_source_kind
       OR v_ref.ref_kind <> p_ref_kind
       OR (
           v_ref.conversation_id IS NOT NULL
           AND p_conversation_id IS NOT NULL
           AND v_ref.conversation_id <> p_conversation_id
       )
       OR (
           v_ref.source_message_id IS NOT NULL
           AND p_source_message_id IS NOT NULL
           AND v_ref.source_message_id <> p_source_message_id
       )
       OR (
           v_ref.source_task_id IS NOT NULL
           AND p_source_task_id IS NOT NULL
           AND v_ref.source_task_id <> p_source_task_id
       )
       OR (
           v_ref.source_generation_id IS NOT NULL
           AND p_source_generation_id IS NOT NULL
           AND v_ref.source_generation_id <> p_source_generation_id
       )
       OR (
           v_ref.source_attachment_id IS NOT NULL
           AND p_source_attachment_id IS NOT NULL
           AND v_ref.source_attachment_id <> p_source_attachment_id
       )
       OR (
           v_ref.content_index IS NOT NULL
           AND p_content_index IS NOT NULL
           AND v_ref.content_index <> p_content_index
       )
    THEN
        RAISE EXCEPTION 'USER_ASSET_REF_CONFLICT';
    END IF;

    UPDATE user_asset_refs SET
        conversation_id = COALESCE(
            user_asset_refs.conversation_id, p_conversation_id
        ),
        source_message_id = COALESCE(
            user_asset_refs.source_message_id, p_source_message_id
        ),
        source_task_id = COALESCE(
            user_asset_refs.source_task_id, p_source_task_id
        ),
        source_generation_id = COALESCE(
            user_asset_refs.source_generation_id, p_source_generation_id
        ),
        source_attachment_id = COALESCE(
            user_asset_refs.source_attachment_id, p_source_attachment_id
        ),
        model_id = COALESCE(user_asset_refs.model_id, p_model_id),
        prompt = COALESCE(user_asset_refs.prompt, p_prompt),
        metadata = user_asset_refs.metadata || COALESCE(
            p_ref_metadata, '{}'::JSONB
        ),
        updated_at = NOW()
    WHERE id = v_ref.id
    RETURNING * INTO v_ref;

    RETURN jsonb_build_object(
        'ref', to_jsonb(v_ref), 'created', v_created
    );
END
$function$;

CREATE OR REPLACE FUNCTION register_user_asset(
    p_org_id UUID,
    p_storage_scope TEXT,
    p_storage_owner_key TEXT,
    p_storage_provider TEXT,
    p_storage_key TEXT,
    p_media_type TEXT,
    p_original_url TEXT,
    p_thumbnail_url TEXT,
    p_download_url TEXT,
    p_workspace_path TEXT,
    p_name TEXT,
    p_mime_type TEXT,
    p_size BIGINT,
    p_content_sha256 TEXT,
    p_asset_metadata JSONB,
    p_ref_key TEXT,
    p_actor_user_id UUID,
    p_source_type TEXT,
    p_source_kind TEXT,
    p_ref_kind TEXT,
    p_conversation_id UUID,
    p_source_message_id UUID,
    p_source_task_id UUID,
    p_source_generation_id UUID,
    p_source_attachment_id UUID,
    p_content_index INTEGER,
    p_model_id TEXT,
    p_prompt TEXT,
    p_ref_metadata JSONB,
    p_created_at TIMESTAMPTZ DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $function$
DECLARE
    v_asset_result JSONB;
    v_ref_result JSONB;
    v_asset_id UUID;
BEGIN
    IF p_storage_scope IS NULL
       OR p_storage_scope NOT IN ('user', 'channel')
       OR p_storage_provider IS NULL
       OR p_storage_provider NOT IN ('workspace', 'oss')
       OR p_media_type IS NULL
       OR p_media_type NOT IN ('image', 'video', 'file')
       OR p_source_type IS NULL
       OR p_source_type NOT IN ('upload', 'generated')
       OR p_source_kind IS NULL
       OR p_source_kind NOT IN (
           'web_upload', 'wecom_upload', 'image_task', 'video_task',
           'media_tool', 'ecom_image'
       )
       OR p_ref_kind IS NULL
       OR p_ref_kind NOT IN (
           'upload', 'task', 'message', 'image_generation', 'attachment'
       )
       OR p_storage_owner_key IS NULL
       OR (
           p_storage_scope = 'user'
           AND p_storage_owner_key !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       )
       OR (
           p_storage_scope = 'channel'
           AND p_storage_owner_key !~ '^channels/wecom/[0-9a-f]{24}$'
       )
       OR p_actor_user_id IS NULL
       OR COALESCE(BTRIM(p_storage_key), '') = ''
       OR COALESCE(BTRIM(p_original_url), '') = ''
       OR COALESCE(BTRIM(p_download_url), '') = ''
       OR COALESCE(BTRIM(p_name), '') = ''
       OR COALESCE(BTRIM(p_ref_key), '') = ''
       OR (p_size IS NOT NULL AND p_size < 0)
       OR (
           p_content_sha256 IS NOT NULL
           AND p_content_sha256 !~ '^[0-9a-f]{64}$'
       )
       OR (p_content_index IS NOT NULL AND p_content_index < 0)
    THEN
        RAISE EXCEPTION 'USER_ASSET_INVALID_INPUT';
    END IF;

    v_asset_result := _resolve_user_asset(
        p_org_id, p_storage_scope, p_storage_owner_key, p_storage_provider,
        p_storage_key, p_media_type, p_original_url, p_thumbnail_url,
        p_download_url, p_workspace_path, p_name, p_mime_type, p_size,
        p_content_sha256, p_asset_metadata, p_created_at
    );
    v_asset_id := (v_asset_result->'asset'->>'id')::UUID;
    v_ref_result := _bind_user_asset_ref(
        v_asset_id, p_ref_key, p_actor_user_id, p_org_id, p_source_type,
        p_source_kind, p_ref_kind, p_conversation_id, p_source_message_id,
        p_source_task_id, p_source_generation_id, p_source_attachment_id,
        p_content_index, p_model_id, p_prompt, p_ref_metadata, p_created_at
    );

    RETURN jsonb_build_object(
        'asset', v_asset_result->'asset',
        'ref', v_ref_result->'ref',
        'asset_created', (v_asset_result->>'created')::BOOLEAN,
        'ref_created', (v_ref_result->>'created')::BOOLEAN
    );
END
$function$;

ALTER TABLE user_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_asset_refs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE user_assets, user_asset_refs FROM PUBLIC;
REVOKE ALL ON FUNCTION _resolve_user_asset(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, BIGINT, TEXT, JSONB, TIMESTAMPTZ
) FROM PUBLIC;
REVOKE ALL ON FUNCTION _bind_user_asset_ref(
    UUID, TEXT, UUID, UUID, TEXT, TEXT, TEXT, UUID, UUID, UUID, UUID,
    UUID, INTEGER, TEXT, TEXT, JSONB, TIMESTAMPTZ
) FROM PUBLIC;
REVOKE ALL ON FUNCTION register_user_asset(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, BIGINT, TEXT, JSONB, TEXT, UUID, TEXT, TEXT, TEXT, UUID,
    UUID, UUID, UUID, UUID, INTEGER, TEXT, TEXT, JSONB, TIMESTAMPTZ
) FROM PUBLIC;

DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON TABLE user_assets, user_asset_refs TO service_role;
        GRANT EXECUTE ON FUNCTION register_user_asset(
            UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
            TEXT, TEXT, BIGINT, TEXT, JSONB, TEXT, UUID, TEXT, TEXT,
            TEXT, UUID, UUID, UUID, UUID, UUID, INTEGER, TEXT, TEXT,
            JSONB, TIMESTAMPTZ
        ) TO service_role;
    END IF;
END
$grant$;

COMMENT ON TABLE user_assets IS
    '按稳定存储对象身份去重的 canonical 用户资产；不替代业务事实';
COMMENT ON TABLE user_asset_refs IS
    '用户资产与上传、任务、消息、生成记录、企微附件的多来源关联';
