-- 209: Expose admin asset reads only through a database-validated facade.

SET LOCAL ROLE everydayai_owner;

ALTER FUNCTION list_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) RENAME TO _list_admin_user_assets_owner;
ALTER FUNCTION _list_admin_user_assets_owner(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) SET search_path = pg_catalog, public;

REVOKE ALL ON FUNCTION _list_admin_user_assets_owner(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;

DO $$
BEGIN
    IF to_regrole('service_role') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION _list_admin_user_assets_owner(
            UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
        ) FROM service_role;
    END IF;
END;
$$;

CREATE FUNCTION list_platform_admin_user_assets(
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
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR NOT public.tenant_platform_admin() THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED'
            USING ERRCODE = '42501';
    END IF;

    RETURN public._list_admin_user_assets_owner(
        p_actor_user_id,
        p_source_type,
        p_media_type,
        p_limit,
        p_cursor_created_at,
        p_cursor_id
    );
END;
$$;

REVOKE ALL ON FUNCTION list_platform_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;
DO $$
BEGIN
    IF to_regrole('service_role') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION list_platform_admin_user_assets(
            UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
        ) FROM service_role;
    END IF;
END;
$$;
GRANT EXECUTE ON FUNCTION list_platform_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) TO everydayai_runtime;

CREATE FUNCTION resolve_platform_admin_user_assets_download(
    p_actor_user_id UUID,
    p_asset_ids JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_items JSONB;
    v_resolved_count INTEGER;
    v_asset_ids UUID[] := ARRAY[]::UUID[];
    v_element JSONB;
    v_text TEXT;
    v_asset_id UUID;
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR NOT public.tenant_platform_admin() THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED'
            USING ERRCODE = '42501';
    END IF;
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

    FOR v_element IN
        SELECT element.value
          FROM jsonb_array_elements(p_asset_ids)
               WITH ORDINALITY AS element(value, ordinality)
         ORDER BY element.ordinality
    LOOP
        IF jsonb_typeof(v_element) IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION 'ADMIN_ASSET_DOWNLOAD_ARGUMENT_INVALID'
                USING ERRCODE = '22023';
        END IF;
        v_text := v_element #>> '{}';
        IF btrim(v_text) = '' THEN
            RAISE EXCEPTION 'ADMIN_ASSET_DOWNLOAD_ARGUMENT_INVALID'
                USING ERRCODE = '22023';
        END IF;
        BEGIN
            v_asset_id := v_text::UUID;
        EXCEPTION
            WHEN invalid_text_representation THEN
                RAISE EXCEPTION 'ADMIN_ASSET_DOWNLOAD_ARGUMENT_INVALID'
                    USING ERRCODE = '22023';
        END;
        IF v_asset_id = ANY(v_asset_ids) THEN
            RAISE EXCEPTION 'ADMIN_ASSET_DOWNLOAD_ARGUMENT_INVALID'
                USING ERRCODE = '22023';
        END IF;
        v_asset_ids := array_append(v_asset_ids, v_asset_id);
    END LOOP;

    SELECT
        count(*)::INTEGER,
        COALESCE(
            jsonb_agg(
                jsonb_build_object(
                    'id', resolved.id,
                    'download_url', resolved.download_url,
                    'name', resolved.name
                )
                ORDER BY resolved.ordinality
            ),
            '[]'::JSONB
        )
      INTO v_resolved_count, v_items
      FROM (
          SELECT
              requested.ordinality,
              asset.id,
              asset.download_url,
              asset.name
            FROM unnest(v_asset_ids) WITH ORDINALITY
                 AS requested(asset_id, ordinality)
            JOIN public.user_assets asset
              ON asset.id = requested.asset_id
             AND asset.status = 'ready'
           WHERE EXISTS (
               SELECT 1
                 FROM public.user_asset_refs asset_ref
                WHERE asset_ref.asset_id = asset.id
                  AND asset_ref.actor_user_id = p_actor_user_id
           )
      ) resolved;

    IF v_resolved_count <> cardinality(v_asset_ids) THEN
        RAISE EXCEPTION 'ADMIN_ASSET_DOWNLOAD_SCOPE_INVALID'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_items;
END;
$$;

REVOKE ALL ON FUNCTION resolve_platform_admin_user_assets_download(
    UUID, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;
DO $$
BEGIN
    IF to_regrole('service_role') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION resolve_platform_admin_user_assets_download(
            UUID, JSONB
        ) FROM service_role;
    END IF;
END;
$$;
GRANT EXECUTE ON FUNCTION resolve_platform_admin_user_assets_download(
    UUID, JSONB
) TO everydayai_runtime;

REVOKE ALL ON TABLE user_assets, user_asset_refs
FROM everydayai_runtime;

RESET ROLE;
