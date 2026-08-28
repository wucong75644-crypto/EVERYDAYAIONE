-- 182: Sync 对错误日志、OSS、租户发现、企微匹配和告警的窄能力门面。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _record_service_error_log_core(
    p_fingerprint TEXT,
    p_level TEXT,
    p_module TEXT,
    p_function TEXT,
    p_line INTEGER,
    p_message TEXT,
    p_traceback TEXT,
    p_occurrence_count INTEGER,
    p_first_seen_at TIMESTAMPTZ,
    p_last_seen_at TIMESTAMPTZ,
    p_org_id UUID,
    p_is_critical BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_log public.error_logs%ROWTYPE;
BEGIN
    IF NULLIF(BTRIM(p_fingerprint), '') IS NULL
       OR NULLIF(BTRIM(p_level), '') IS NULL
       OR NULLIF(BTRIM(p_message), '') IS NULL
       OR p_occurrence_count IS NULL OR p_occurrence_count < 1
       OR p_first_seen_at IS NULL OR p_last_seen_at IS NULL THEN
        RAISE EXCEPTION 'SERVICE_ERROR_LOG_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.error_logs (
        fingerprint, level, module, function, line, message, traceback,
        occurrence_count, first_seen_at, last_seen_at, org_id, is_critical
    ) VALUES (
        p_fingerprint, p_level, p_module, p_function, p_line, p_message,
        p_traceback, p_occurrence_count, p_first_seen_at, p_last_seen_at,
        p_org_id, COALESCE(p_is_critical, FALSE)
    )
    ON CONFLICT (fingerprint) WHERE is_resolved = FALSE
    DO UPDATE SET
        occurrence_count = error_logs.occurrence_count
            + EXCLUDED.occurrence_count,
        last_seen_at = GREATEST(
            error_logs.last_seen_at, EXCLUDED.last_seen_at
        ),
        first_seen_at = LEAST(
            error_logs.first_seen_at, EXCLUDED.first_seen_at
        ),
        level = CASE
            WHEN EXCLUDED.level = 'CRITICAL' THEN 'CRITICAL'
            ELSE error_logs.level
        END,
        is_critical = error_logs.is_critical OR EXCLUDED.is_critical,
        message = EXCLUDED.message,
        traceback = COALESCE(EXCLUDED.traceback, error_logs.traceback)
    RETURNING * INTO v_log;
    RETURN jsonb_build_object(
        'outcome', 'recorded',
        'id', v_log.id,
        'occurrence_count', v_log.occurrence_count
    );
END;
$$;

CREATE OR REPLACE FUNCTION sync_record_error_log(
    p_fingerprint TEXT,
    p_level TEXT,
    p_module TEXT,
    p_function TEXT,
    p_line INTEGER,
    p_message TEXT,
    p_traceback TEXT,
    p_occurrence_count INTEGER,
    p_first_seen_at TIMESTAMPTZ,
    p_last_seen_at TIMESTAMPTZ,
    p_org_id UUID,
    p_is_critical BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_sync' THEN
        RAISE EXCEPTION 'SYNC_ERROR_LOG_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN public._record_service_error_log_core(
        p_fingerprint, p_level, p_module, p_function, p_line, p_message,
        p_traceback, p_occurrence_count, p_first_seen_at, p_last_seen_at,
        p_org_id, p_is_critical
    );
END;
$$;

CREATE OR REPLACE FUNCTION sync_cleanup_error_logs(p_retention_days INTEGER)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_deleted INTEGER;
BEGIN
    IF session_user <> 'everydayai_sync'
       OR p_retention_days < 1 OR p_retention_days > 3650 THEN
        RAISE EXCEPTION 'SYNC_ERROR_LOG_CLEANUP_DENIED'
            USING ERRCODE = '42501';
    END IF;
    DELETE FROM public.error_logs
     WHERE last_seen_at < NOW() - make_interval(days => p_retention_days);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;

CREATE OR REPLACE FUNCTION worker_cleanup_error_logs(p_retention_days INTEGER)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_deleted INTEGER;
BEGIN
    IF session_user <> 'everydayai_worker'
       OR p_retention_days < 1 OR p_retention_days > 3650 THEN
        RAISE EXCEPTION 'WORKER_ERROR_LOG_CLEANUP_DENIED'
            USING ERRCODE = '42501';
    END IF;
    DELETE FROM public.error_logs
     WHERE last_seen_at < NOW() - make_interval(days => p_retention_days);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;

CREATE OR REPLACE FUNCTION sync_list_oss_purge_candidates(p_limit INTEGER)
RETURNS TABLE (
    id BIGINT,
    oss_object_key TEXT,
    relative_path TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_sync'
       OR p_limit < 1 OR p_limit > 500 THEN
        RAISE EXCEPTION 'SYNC_OSS_PURGE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
    SELECT deleted.id, deleted.oss_object_key, deleted.relative_path
      FROM public.deleted_files deleted
     WHERE deleted.purge_after < NOW() AND NOT deleted.purged
     ORDER BY deleted.purge_after
     LIMIT p_limit;
END;
$$;

CREATE OR REPLACE FUNCTION sync_mark_oss_file_purged(
    p_id BIGINT,
    p_oss_object_key TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_sync'
       OR p_id IS NULL OR NULLIF(BTRIM(p_oss_object_key), '') IS NULL THEN
        RAISE EXCEPTION 'SYNC_OSS_PURGE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.deleted_files
       SET purged = TRUE
     WHERE id = p_id
       AND oss_object_key = p_oss_object_key
       AND purge_after < NOW()
       AND NOT purged;
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION sync_discover_erp_targets()
RETURNS TABLE (org_id UUID)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_sync' THEN
        RAISE EXCEPTION 'SYNC_DISCOVERY_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
    SELECT organization.id
      FROM public.organizations organization
     WHERE organization.status = 'active'
       AND COALESCE(
           (organization.features->>'erp')::BOOLEAN,
           FALSE
       )
       AND EXISTS (
           SELECT 1 FROM public.configuration_entries entry
            WHERE entry.scope_kind = 'organization'
              AND entry.org_id = organization.id
              AND entry.config_key = 'erp.app_credentials'
              AND entry.status = 'active'
       )
       AND EXISTS (
           SELECT 1 FROM public.configuration_entries entry
            WHERE entry.scope_kind = 'organization'
              AND entry.org_id = organization.id
              AND entry.config_key = 'erp.token_pair'
              AND entry.status = 'active'
       )
     ORDER BY organization.id;
END;
$$;

CREATE OR REPLACE FUNCTION sync_list_erp_token_versions()
RETURNS TABLE (
    org_id UUID,
    version BIGINT,
    updated_at TIMESTAMPTZ
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_sync' THEN
        RAISE EXCEPTION 'SYNC_DISCOVERY_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
    SELECT entry.org_id, entry.version, entry.updated_at
      FROM public.configuration_entries entry
      JOIN public.organizations organization
        ON organization.id = entry.org_id
       AND organization.status = 'active'
     WHERE entry.scope_kind = 'organization'
       AND entry.config_key = 'erp.token_pair'
       AND entry.status = 'active'
     ORDER BY entry.org_id;
END;
$$;

CREATE OR REPLACE FUNCTION sync_list_wecom_employees(p_org_id UUID)
RETURNS TABLE (
    wecom_userid VARCHAR,
    name VARCHAR,
    status INTEGER
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_sync'
       OR p_org_id IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM public.organizations organization
            WHERE organization.id = p_org_id
              AND organization.status = 'active'
       ) THEN
        RAISE EXCEPTION 'SYNC_WECOM_EMPLOYEE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
    SELECT employee.wecom_userid, employee.name, employee.status
      FROM public.wecom_employees employee
     WHERE employee.org_id = p_org_id
       AND employee.status = 1
     ORDER BY employee.wecom_userid;
END;
$$;

CREATE OR REPLACE FUNCTION sync_get_org_label(p_org_id UUID)
RETURNS TEXT
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_name TEXT;
BEGIN
    IF session_user <> 'everydayai_sync' OR p_org_id IS NULL THEN
        RAISE EXCEPTION 'SYNC_ORG_LABEL_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT organization.name INTO v_name
      FROM public.organizations organization
     WHERE organization.id = p_org_id
       AND organization.status = 'active';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'SYNC_ORG_NOT_ACTIVE'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_name;
END;
$$;

CREATE OR REPLACE FUNCTION service_create_org_alert(
    p_org_id UUID,
    p_text TEXT
)
RETURNS TABLE (
    target_org_id UUID,
    user_id UUID,
    wecom_userid VARCHAR,
    conversation_id UUID,
    message_id UUID
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_rec RECORD;
    v_conversation_id UUID;
    v_message_id UUID;
BEGIN
    IF session_user NOT IN ('everydayai_sync', 'everydayai_worker')
       OR p_org_id IS NULL
       OR NULLIF(BTRIM(p_text), '') IS NULL
       OR LENGTH(p_text) > 20000
       OR NOT EXISTS (
           SELECT 1 FROM public.organizations organization
            WHERE organization.id = p_org_id
              AND organization.status = 'active'
       ) THEN
        RAISE EXCEPTION 'SYNC_ORG_ALERT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    FOR v_rec IN
        SELECT member.user_id, mapping.wecom_userid
          FROM public.org_members member
          JOIN public.users app_user
            ON app_user.id = member.user_id
           AND app_user.status::TEXT = 'active'
          LEFT JOIN public.wecom_user_mappings mapping
            ON mapping.org_id = member.org_id
           AND mapping.user_id = member.user_id
         WHERE member.org_id = p_org_id
           AND member.role IN ('owner', 'admin')
           AND member.status = 'active'
         ORDER BY member.user_id
         LIMIT 10
    LOOP
        SELECT conversation.id INTO v_conversation_id
          FROM public.conversations conversation
         WHERE conversation.user_id = v_rec.user_id
           AND conversation.org_id = p_org_id
           AND conversation.source = 'wecom'
         ORDER BY conversation.updated_at DESC
         LIMIT 1;
        IF v_conversation_id IS NULL THEN
            INSERT INTO public.conversations (
                user_id, title, model_id, org_id, source,
                scope_type, scope_id
            ) VALUES (
                v_rec.user_id, '企微对话', 'auto', p_org_id, 'wecom',
                'user', v_rec.user_id::TEXT
            )
            RETURNING id INTO v_conversation_id;
        END IF;
        INSERT INTO public.messages (
            conversation_id, role, content, status, org_id,
            message_kind
        ) VALUES (
            v_conversation_id, 'assistant',
            jsonb_build_array(
                jsonb_build_object('type', 'text', 'text', p_text)
            )::TEXT,
            'completed', p_org_id, 'conversation'
        )
        RETURNING id INTO v_message_id;
        UPDATE public.conversations
           SET message_count = message_count + 1,
               last_message_preview = LEFT(p_text, 50),
               updated_at = NOW()
         WHERE id = v_conversation_id;
        target_org_id := p_org_id;
        user_id := v_rec.user_id;
        wecom_userid := v_rec.wecom_userid;
        conversation_id := v_conversation_id;
        message_id := v_message_id;
        RETURN NEXT;
        v_conversation_id := NULL;
        v_message_id := NULL;
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION service_create_platform_alert(p_text TEXT)
RETURNS TABLE (
    target_org_id UUID,
    user_id UUID,
    wecom_userid VARCHAR,
    conversation_id UUID,
    message_id UUID
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org_id UUID;
BEGIN
    IF session_user NOT IN ('everydayai_sync', 'everydayai_worker')
       OR NULLIF(BTRIM(p_text), '') IS NULL
       OR LENGTH(p_text) > 20000 THEN
        RAISE EXCEPTION 'SYNC_PLATFORM_ALERT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT member.org_id INTO v_org_id
      FROM public.users app_user
      JOIN public.org_members member
        ON member.user_id = app_user.id
       AND member.status = 'active'
       AND member.role IN ('owner', 'admin')
      JOIN public.organizations organization
        ON organization.id = member.org_id
       AND organization.status = 'active'
     WHERE app_user.role::TEXT = 'super_admin'
       AND app_user.status::TEXT = 'active'
     ORDER BY member.joined_at
     LIMIT 1;
    IF v_org_id IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT * FROM public.service_create_org_alert(v_org_id, p_text);
END;
$$;

REVOKE ALL ON FUNCTION _record_service_error_log_core(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, INTEGER,
    TIMESTAMPTZ, TIMESTAMPTZ, UUID, BOOLEAN
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
       everydayai_worker, everydayai_sync, everydayai;

REVOKE ALL ON FUNCTION sync_record_error_log(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, INTEGER,
    TIMESTAMPTZ, TIMESTAMPTZ, UUID, BOOLEAN
), sync_cleanup_error_logs(INTEGER),
   worker_cleanup_error_logs(INTEGER),
   sync_list_oss_purge_candidates(INTEGER),
   sync_mark_oss_file_purged(BIGINT, TEXT),
   sync_discover_erp_targets(),
   sync_list_erp_token_versions(),
   sync_list_wecom_employees(UUID),
   sync_get_org_label(UUID),
   service_create_org_alert(UUID, TEXT),
   service_create_platform_alert(TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

GRANT EXECUTE ON FUNCTION sync_record_error_log(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, INTEGER,
    TIMESTAMPTZ, TIMESTAMPTZ, UUID, BOOLEAN
), sync_cleanup_error_logs(INTEGER),
   sync_list_oss_purge_candidates(INTEGER),
   sync_mark_oss_file_purged(BIGINT, TEXT),
   sync_discover_erp_targets(),
   sync_list_erp_token_versions(),
   sync_list_wecom_employees(UUID),
   sync_get_org_label(UUID),
   service_create_org_alert(UUID, TEXT),
   service_create_platform_alert(TEXT)
TO everydayai_sync;

GRANT EXECUTE ON FUNCTION service_create_org_alert(UUID, TEXT),
    service_create_platform_alert(TEXT),
    worker_cleanup_error_logs(INTEGER)
TO everydayai_worker;

RESET ROLE;
