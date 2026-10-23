-- Private knowledge assets retain canonical registration, but must not leak
-- filenames, counts or locators through the platform-wide upload gallery.
-- Preserve the existing function signatures, owners and EXECUTE ACLs.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION list_admin_user_assets(
    p_actor_user_id UUID,
    p_source_type TEXT,
    p_media_type TEXT DEFAULT NULL,
    p_limit INTEGER DEFAULT 24,
    p_cursor_created_at TIMESTAMPTZ DEFAULT NULL,
    p_cursor_id UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $function$
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
    IF p_media_type IS NOT NULL AND
       p_media_type NOT IN ('image', 'video', 'file') THEN
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
    FROM user_assets AS asset
    WHERE asset.status = 'ready'
       AND (asset.metadata->>'storage_class') IS DISTINCT FROM 'knowledge_private'
      AND (p_media_type IS NULL OR asset.media_type = p_media_type)
      AND EXISTS (
          SELECT 1
          FROM user_asset_refs AS asset_ref
          WHERE asset_ref.asset_id = asset.id
            AND asset_ref.actor_user_id = p_actor_user_id
            AND asset_ref.source_type = p_source_type
      );

    SELECT COALESCE(jsonb_agg(
        to_jsonb(page_row)
        ORDER BY page_row.created_at DESC, page_row.id DESC
    ), '[]'::JSONB)
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
            asset.created_at,
            (
                SELECT COUNT(*)
                FROM user_asset_refs AS counted_ref
                WHERE counted_ref.asset_id = asset.id
                  AND counted_ref.actor_user_id = p_actor_user_id
                  AND counted_ref.source_type = p_source_type
            ) AS source_ref_count
        FROM user_assets AS asset
        JOIN LATERAL (
            SELECT
                asset_ref.source_type,
                asset_ref.ref_kind,
                asset_ref.conversation_id,
                asset_ref.source_message_id,
                asset_ref.source_task_id,
                asset_ref.model_id,
                asset_ref.prompt
            FROM user_asset_refs AS asset_ref
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
       AND (asset.metadata->>'storage_class') IS DISTINCT FROM 'knowledge_private'
          AND (p_media_type IS NULL OR asset.media_type = p_media_type)
          AND (p_cursor_created_at IS NULL OR
               (asset.created_at, asset.id) <
               (p_cursor_created_at, p_cursor_id))
        ORDER BY asset.created_at DESC, asset.id DESC
        LIMIT p_limit
    ) AS page_row;

    RETURN jsonb_build_object('items', v_items, 'total', v_total);
END;
$function$;

CREATE OR REPLACE FUNCTION public.list_platform_admin_user_assets(
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
       AND (asset.metadata->>'storage_class') IS DISTINCT FROM 'knowledge_private'
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
       AND (asset.metadata->>'storage_class') IS DISTINCT FROM 'knowledge_private'
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

CREATE OR REPLACE FUNCTION public.resolve_platform_admin_user_assets_download(
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
       AND (asset.metadata->>'storage_class') IS DISTINCT FROM 'knowledge_private'
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
RESET ROLE;
