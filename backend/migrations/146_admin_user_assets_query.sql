-- 管理员统一用户资产查询：按 canonical 资产分页，并从来源关联投影展示字段。

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

REVOKE ALL ON FUNCTION list_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) FROM PUBLIC;
DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION list_admin_user_assets(
            UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
        ) TO service_role;
    END IF;
END
$grant$;
