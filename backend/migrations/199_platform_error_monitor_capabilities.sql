-- 199: Platform error-monitor capabilities and dormant audit-table isolation.

SET LOCAL ROLE everydayai_owner;

DROP POLICY IF EXISTS platform_admin_error_logs_select ON error_logs;
DROP POLICY IF EXISTS platform_admin_error_logs_update ON error_logs;
DROP POLICY IF EXISTS platform_admin_error_logs_delete ON error_logs;
CREATE POLICY error_logs_owner_all ON error_logs
FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE error_logs FORCE ROW LEVEL SECURITY;

ALTER TABLE permission_audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY permission_audit_log_owner_all ON permission_audit_log
FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE permission_audit_log FORCE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION list_platform_error_logs(
    p_page INTEGER,
    p_page_size INTEGER,
    p_level TEXT,
    p_is_critical BOOLEAN,
    p_is_resolved BOOLEAN,
    p_search TEXT,
    p_days INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_offset INTEGER;
    v_total BIGINT;
    v_items JSONB;
    v_search TEXT;
BEGIN
    IF NOT public.tenant_platform_admin() THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED' USING ERRCODE = '42501';
    END IF;
    IF p_page NOT BETWEEN 1 AND 1000000
       OR p_page_size NOT BETWEEN 1 AND 100
       OR p_days NOT BETWEEN 1 AND 30
       OR (p_level IS NOT NULL AND upper(p_level) NOT IN ('ERROR', 'CRITICAL'))
       OR length(COALESCE(p_search, '')) > 500 THEN
        RAISE EXCEPTION 'ERROR_MONITOR_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    v_offset := (p_page - 1) * p_page_size;
    v_search := replace(replace(replace(p_search, '\', '\\'), '%', '\%'), '_', '\_');

    SELECT count(*) INTO v_total
      FROM public.error_logs log
     WHERE log.last_seen_at >= now() - make_interval(days => p_days)
       AND (p_level IS NULL OR log.level = upper(p_level))
       AND (p_is_critical IS NULL OR log.is_critical = p_is_critical)
       AND (p_is_resolved IS NULL OR log.is_resolved = p_is_resolved)
       AND (p_search IS NULL OR log.message ILIKE '%' || v_search || '%' ESCAPE '\');

    SELECT COALESCE(jsonb_agg(to_jsonb(page_rows)), '[]'::JSONB)
      INTO v_items
      FROM (
          SELECT *
            FROM public.error_logs log
           WHERE log.last_seen_at >= now() - make_interval(days => p_days)
             AND (p_level IS NULL OR log.level = upper(p_level))
             AND (p_is_critical IS NULL OR log.is_critical = p_is_critical)
             AND (p_is_resolved IS NULL OR log.is_resolved = p_is_resolved)
             AND (p_search IS NULL OR log.message ILIKE '%' || v_search || '%' ESCAPE '\')
           ORDER BY log.last_seen_at DESC
           OFFSET v_offset LIMIT p_page_size
      ) page_rows;
    RETURN jsonb_build_object('items', v_items, 'total', v_total);
END;
$$;

CREATE OR REPLACE FUNCTION get_platform_error_stats()
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_today TIMESTAMPTZ;
    v_week TIMESTAMPTZ := now() - INTERVAL '7 days';
    v_result JSONB;
BEGIN
    IF NOT public.tenant_platform_admin() THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED' USING ERRCODE = '42501';
    END IF;
    v_today := date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')
               AT TIME ZONE 'Asia/Shanghai';
    SELECT jsonb_build_object(
        'today_total', count(*) FILTER (WHERE last_seen_at >= v_today),
        'today_critical', count(*) FILTER (
            WHERE last_seen_at >= v_today AND is_critical
        ),
        'week_total', count(*) FILTER (WHERE last_seen_at >= v_week),
        'unresolved', count(*) FILTER (WHERE NOT is_resolved),
        'top_modules', (
            SELECT COALESCE(jsonb_agg(to_jsonb(module_rows)), '[]'::JSONB)
              FROM (
                  SELECT COALESCE(module, 'unknown') AS module,
                         sum(occurrence_count)::BIGINT AS count
                    FROM public.error_logs
                   WHERE last_seen_at >= v_week
                   GROUP BY COALESCE(module, 'unknown')
                   ORDER BY count DESC
                   LIMIT 10
              ) module_rows
        )
    ) INTO v_result FROM public.error_logs;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION list_platform_error_summary(p_days INTEGER)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    IF NOT public.tenant_platform_admin() THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED' USING ERRCODE = '42501';
    END IF;
    IF p_days NOT BETWEEN 1 AND 30 THEN
        RAISE EXCEPTION 'ERROR_MONITOR_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(summary_rows)), '[]'::JSONB)
      INTO v_result
      FROM (
          SELECT level, module, function, message, occurrence_count,
                 is_critical, first_seen_at, last_seen_at
            FROM public.error_logs
           WHERE last_seen_at >= now() - make_interval(days => p_days)
           ORDER BY occurrence_count DESC
           LIMIT 100
      ) summary_rows;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION resolve_platform_error(p_error_id BIGINT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_updated BIGINT;
BEGIN
    IF NOT public.tenant_platform_admin() THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED' USING ERRCODE = '42501';
    END IF;
    UPDATE public.error_logs
       SET is_resolved = TRUE,
           resolved_at = now(),
           resolved_by = public.tenant_actor_user_id()
     WHERE id = p_error_id;
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN jsonb_build_object('updated', v_updated);
END;
$$;

CREATE OR REPLACE FUNCTION clear_platform_errors(
    p_before_date DATE,
    p_resolved_only BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_cutoff TIMESTAMPTZ;
    v_deleted BIGINT;
BEGIN
    IF NOT public.tenant_platform_admin() THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED' USING ERRCODE = '42501';
    END IF;
    v_cutoff := CASE
        WHEN p_before_date IS NULL
        THEN now() - INTERVAL '7 days'
        ELSE p_before_date::TIMESTAMP AT TIME ZONE 'Asia/Shanghai'
    END;
    DELETE FROM public.error_logs
     WHERE last_seen_at < v_cutoff
       AND (NOT p_resolved_only OR is_resolved);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN jsonb_build_object('deleted', v_deleted);
END;
$$;

REVOKE ALL ON FUNCTION
    list_platform_error_logs(INTEGER, INTEGER, TEXT, BOOLEAN, BOOLEAN, TEXT, INTEGER),
    get_platform_error_stats(),
    list_platform_error_summary(INTEGER),
    resolve_platform_error(BIGINT),
    clear_platform_errors(DATE, BOOLEAN)
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION
    list_platform_error_logs(INTEGER, INTEGER, TEXT, BOOLEAN, BOOLEAN, TEXT, INTEGER),
    get_platform_error_stats(),
    list_platform_error_summary(INTEGER),
    resolve_platform_error(BIGINT),
    clear_platform_errors(DATE, BOOLEAN)
TO everydayai_runtime;

REVOKE ALL ON error_logs
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
REVOKE ALL ON permission_audit_log
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;

RESET ROLE;
