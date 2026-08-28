-- 236: 恢复旧 Web 管理后台的统一用户资产查询与下载授权。
--
-- 145/146 建立了 user_assets 及基础查询能力；209 后续把管理员门面
-- 绑定到了 Runtime 会话。当前分支明确删除 Runtime，因此这里恢复同一
-- 个管理员能力到旧 Web 后端 everydayai，不重新开放 Runtime 角色。

DROP FUNCTION IF EXISTS public.list_platform_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
);
DROP FUNCTION IF EXISTS public.resolve_platform_admin_user_assets_download(
    UUID, JSONB
);

CREATE FUNCTION public.list_platform_admin_user_assets(
    p_actor_user_id UUID,
    p_source_type TEXT,
    p_media_type TEXT DEFAULT NULL,
    p_limit INTEGER DEFAULT 24,
    p_cursor_created_at TIMESTAMPTZ DEFAULT NULL,
    p_cursor_id UUID DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_total BIGINT;
    v_items JSONB;
BEGIN
    IF p_actor_user_id IS NULL THEN
        RAISE EXCEPTION 'ADMIN_ASSET_USER_REQUIRED';
    END IF;
    IF p_source_type NOT IN ('upload', 'generated') THEN
        RAISE EXCEPTION 'ADMIN_ASSET_SOURCE_TYPE_INVALID';
    END IF;
    IF p_media_type IS NOT NULL
       AND p_media_type NOT IN ('image', 'video', 'file') THEN
        RAISE EXCEPTION 'ADMIN_ASSET_MEDIA_TYPE_INVALID';
    END IF;
    IF p_limit < 1 OR p_limit > 101 THEN
        RAISE EXCEPTION 'ADMIN_ASSET_LIMIT_INVALID';
    END IF;
    IF (p_cursor_created_at IS NULL) <> (p_cursor_id IS NULL) THEN
        RAISE EXCEPTION 'ADMIN_ASSET_CURSOR_INVALID';
    END IF;

    SELECT COUNT(*)
      INTO v_total
      FROM public.user_assets AS asset
     WHERE asset.status = 'ready'
       AND (p_media_type IS NULL OR asset.media_type = p_media_type)
       AND EXISTS (
           SELECT 1
             FROM public.user_asset_refs AS asset_ref
            WHERE asset_ref.asset_id = asset.id
              AND asset_ref.actor_user_id = p_actor_user_id
              AND asset_ref.source_type = p_source_type
       );

    SELECT COALESCE(jsonb_agg(to_jsonb(page_row)
                              ORDER BY page_row.created_at DESC, page_row.id DESC),
                    '[]'::JSONB)
      INTO v_items
      FROM (
          SELECT
              asset.id,
              representative_ref.source_type,
              representative_ref.ref_kind AS source_kind,
              asset.media_type,
              asset.status,
              asset.original_url,
              asset.thumbnail_url,
              asset.download_url,
              asset.workspace_path,
              asset.name,
              asset.mime_type,
              asset.size,
              representative_ref.conversation_id,
              representative_ref.source_message_id,
              representative_ref.source_task_id,
              representative_ref.model_id,
              representative_ref.prompt,
              asset.metadata,
              asset.created_at
            FROM public.user_assets AS asset
            JOIN LATERAL (
                SELECT
                    asset_ref.source_type,
                    asset_ref.ref_kind,
                    asset_ref.conversation_id,
                    asset_ref.source_message_id,
                    asset_ref.source_task_id,
                    asset_ref.model_id,
                    asset_ref.prompt
                  FROM public.user_asset_refs AS asset_ref
                 WHERE asset_ref.asset_id = asset.id
                   AND asset_ref.actor_user_id = p_actor_user_id
                   AND asset_ref.source_type = p_source_type
                 ORDER BY
                    CASE asset_ref.ref_kind
                        WHEN 'task' THEN 1
                        WHEN 'image_generation' THEN 2
                        WHEN 'attachment' THEN 3
                        WHEN 'upload' THEN 4
                        WHEN 'message' THEN 5
                        ELSE 6
                    END,
                    asset_ref.created_at,
                    asset_ref.id
                 LIMIT 1
            ) AS representative_ref ON TRUE
           WHERE asset.status = 'ready'
             AND (p_media_type IS NULL OR asset.media_type = p_media_type)
             AND (p_cursor_created_at IS NULL OR
                  (asset.created_at, asset.id) <
                  (p_cursor_created_at, p_cursor_id))
           ORDER BY asset.created_at DESC, asset.id DESC
           LIMIT p_limit
      ) AS page_row;

    RETURN jsonb_build_object('items', v_items, 'total', v_total);
END;
$$;


CREATE FUNCTION public.resolve_platform_admin_user_assets_download(
    p_actor_user_id UUID,
    p_asset_ids JSONB
) RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_items JSONB;
    v_resolved_count INTEGER;
    v_asset_ids UUID[] := ARRAY[]::UUID[];
    v_element JSONB;
    v_asset_id UUID;
BEGIN
    IF p_actor_user_id IS NULL
       OR p_asset_ids IS NULL
       OR jsonb_typeof(p_asset_ids) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'ADMIN_ASSET_DOWNLOAD_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(p_asset_ids) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'ADMIN_ASSET_DOWNLOAD_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    FOR v_element IN SELECT value FROM jsonb_array_elements(p_asset_ids)
    LOOP
        BEGIN
            v_asset_id := (v_element #>> '{}')::UUID;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'ADMIN_ASSET_DOWNLOAD_ARGUMENT_INVALID'
                USING ERRCODE = '22023';
        END;
        IF v_asset_id = ANY(v_asset_ids) THEN
            RAISE EXCEPTION 'ADMIN_ASSET_DOWNLOAD_ARGUMENT_INVALID'
                USING ERRCODE = '22023';
        END IF;
        v_asset_ids := array_append(v_asset_ids, v_asset_id);
    END LOOP;

    SELECT count(*)::INTEGER,
           COALESCE(jsonb_agg(
               jsonb_build_object(
                   'id', resolved.id,
                   'download_url', resolved.download_url,
                   'name', resolved.name
               ) ORDER BY resolved.ordinality
           ), '[]'::JSONB)
      INTO v_resolved_count, v_items
      FROM (
          SELECT requested.ordinality, asset.id, asset.download_url, asset.name
            FROM unnest(v_asset_ids) WITH ORDINALITY
                 AS requested(asset_id, ordinality)
            JOIN public.user_assets AS asset
              ON asset.id = requested.asset_id
             AND asset.status = 'ready'
           WHERE EXISTS (
               SELECT 1
                 FROM public.user_asset_refs AS asset_ref
                WHERE asset_ref.asset_id = asset.id
                  AND asset_ref.actor_user_id = p_actor_user_id
           )
      ) AS resolved;

    IF v_resolved_count <> cardinality(v_asset_ids) THEN
        RAISE EXCEPTION 'ADMIN_ASSET_DOWNLOAD_SCOPE_INVALID'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_items;
END;
$$;


DO $$
DECLARE
    v_role TEXT;
BEGIN
    FOREACH v_role IN ARRAY ARRAY[
        'everydayai_runtime',
        'everydayai_wecom_runtime',
        'everydayai_worker',
        'everydayai_sync',
        'service_role'
    ] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.list_platform_admin_user_assets(uuid,text,text,integer,timestamptz,uuid) FROM %I',
                v_role
            );
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.resolve_platform_admin_user_assets_download(uuid,jsonb) FROM %I',
                v_role
            );
        END IF;
    END LOOP;
END;
$$;

ALTER FUNCTION public.list_platform_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) OWNER TO everydayai;
ALTER FUNCTION public.resolve_platform_admin_user_assets_download(
    UUID, JSONB
) OWNER TO everydayai;

REVOKE ALL ON FUNCTION public.list_platform_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.resolve_platform_admin_user_assets_download(
    UUID, JSONB
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.list_platform_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) TO everydayai;
GRANT EXECUTE ON FUNCTION public.resolve_platform_admin_user_assets_download(
    UUID, JSONB
) TO everydayai;

COMMENT ON FUNCTION public.list_platform_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) IS '旧 Web 管理后台统一读取用户资产；不依赖 Runtime 会话';
COMMENT ON FUNCTION public.resolve_platform_admin_user_assets_download(
    UUID, JSONB
) IS '旧 Web 管理后台按用户资产归属解析下载地址；不依赖 Runtime 会话';
