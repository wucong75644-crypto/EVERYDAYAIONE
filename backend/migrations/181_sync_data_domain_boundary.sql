-- 181: ERP/Sync 数据域角色边界、FORCE RLS 与物化视图能力门面。
-- 前置：setup-tenant-db-roles.sh 与 transfer-sync-domain-ownership.sh。

SET LOCAL ROLE everydayai_owner;

DO $rls$
DECLARE
    tenant_tables CONSTANT TEXT[] := ARRAY[
        'erp_aftersale_logs',
        'erp_batch_stock',
        'erp_categories',
        'erp_classification_rules',
        'erp_document_items',
        'erp_document_items_archive',
        'erp_logistics_companies',
        'erp_operators',
        'erp_order_logs',
        'erp_order_packages',
        'erp_product_daily_stats',
        'erp_product_platform_map',
        'erp_product_skus',
        'erp_products',
        'erp_shop_operators',
        'erp_shops',
        'erp_stock_status',
        'erp_suppliers',
        'erp_sync_dead_letter',
        'erp_sync_state',
        'erp_tags',
        'erp_thinktank_profit_shop',
        'erp_viperp_sale_finance',
        'erp_warehouses',
        'kuaimai_external_credentials',
        'kuaimai_field_audit',
        'kuaimai_sync_logs'
    ];
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY tenant_tables LOOP
        IF to_regclass('public.' || table_name) IS NULL THEN
            RAISE EXCEPTION 'SYNC_DOMAIN_TABLE_MISSING: %', table_name;
        END IF;
        EXECUTE format(
            'ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',
            table_name
        );
        EXECUTE format(
            'ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',
            table_name
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS sync_domain_owner ON public.%I',
            table_name
        );
        EXECUTE format(
            'CREATE POLICY sync_domain_owner ON public.%I'
            ' FOR ALL TO everydayai_owner'
            ' USING (TRUE) WITH CHECK (TRUE)',
            table_name
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS sync_domain_legacy ON public.%I',
            table_name
        );
        EXECUTE format(
            'CREATE POLICY sync_domain_legacy ON public.%I'
            ' FOR ALL TO everydayai'
            ' USING (session_user = ''everydayai'')'
            ' WITH CHECK (session_user = ''everydayai'')',
            table_name
        );
    END LOOP;
END
$rls$;

DO $policies$
DECLARE
    sync_write_tables CONSTANT TEXT[] := ARRAY[
        'erp_aftersale_logs',
        'erp_batch_stock',
        'erp_categories',
        'erp_document_items',
        'erp_document_items_archive',
        'erp_logistics_companies',
        'erp_operators',
        'erp_order_logs',
        'erp_order_packages',
        'erp_product_daily_stats',
        'erp_product_platform_map',
        'erp_product_skus',
        'erp_products',
        'erp_shop_operators',
        'erp_shops',
        'erp_stock_status',
        'erp_suppliers',
        'erp_sync_dead_letter',
        'erp_sync_state',
        'erp_tags',
        'erp_thinktank_profit_shop',
        'erp_viperp_sale_finance',
        'erp_warehouses',
        'kuaimai_field_audit',
        'kuaimai_sync_logs'
    ];
    runtime_read_tables CONSTANT TEXT[] := ARRAY[
        'erp_aftersale_logs',
        'erp_batch_stock',
        'erp_categories',
        'erp_document_items',
        'erp_document_items_archive',
        'erp_logistics_companies',
        'erp_operators',
        'erp_order_logs',
        'erp_order_packages',
        'erp_product_daily_stats',
        'erp_product_platform_map',
        'erp_product_skus',
        'erp_products',
        'erp_shop_operators',
        'erp_shops',
        'erp_stock_status',
        'erp_suppliers',
        'erp_tags',
        'erp_thinktank_profit_shop',
        'erp_viperp_sale_finance',
        'erp_warehouses',
        'kuaimai_field_audit',
        'kuaimai_sync_logs'
    ];
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY sync_write_tables LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS sync_domain_service ON public.%I',
            table_name
        );
        EXECUTE format(
            'CREATE POLICY sync_domain_service ON public.%I'
            ' FOR ALL TO everydayai_sync'
            ' USING (session_user = ''everydayai_sync'')'
            ' WITH CHECK (session_user = ''everydayai_sync'')',
            table_name
        );
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE'
            ' ON TABLE public.%I TO everydayai_sync',
            table_name
        );
    END LOOP;

    FOREACH table_name IN ARRAY runtime_read_tables LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS sync_domain_runtime_read ON public.%I',
            table_name
        );
        EXECUTE format(
            'CREATE POLICY sync_domain_runtime_read ON public.%I'
            ' FOR SELECT TO everydayai_runtime'
            ' USING ('
            '   session_user = ''everydayai_runtime'''
            '   AND current_setting(''app.access_kind'', TRUE) = ''runtime'''
            '   AND org_id = public.tenant_org_id()'
            '   AND public.tenant_actor_is_active_member(org_id)'
            ' )',
            table_name
        );
        EXECUTE format(
            'GRANT SELECT ON TABLE public.%I TO everydayai_runtime',
            table_name
        );
    END LOOP;
END
$policies$;

DROP POLICY IF EXISTS sync_domain_service
    ON public.erp_classification_rules;
CREATE POLICY sync_domain_classification_sync
ON public.erp_classification_rules
FOR SELECT TO everydayai_sync
USING (session_user = 'everydayai_sync');
CREATE POLICY sync_domain_classification_runtime
ON public.erp_classification_rules
FOR ALL TO everydayai_runtime
USING (
    session_user = 'everydayai_runtime'
    AND current_setting('app.access_kind', TRUE) = 'runtime'
    AND org_id = public.tenant_org_id()
    AND public.tenant_actor_is_active_member(org_id)
)
WITH CHECK (
    session_user = 'everydayai_runtime'
    AND current_setting('app.access_kind', TRUE) = 'runtime'
    AND org_id = public.tenant_org_id()
    AND public.tenant_actor_is_active_member(org_id)
);
GRANT SELECT ON TABLE public.erp_classification_rules TO everydayai_sync;
GRANT SELECT, INSERT, UPDATE, DELETE
ON TABLE public.erp_classification_rules TO everydayai_runtime;

DO $sequences$
DECLARE
    sequence_record RECORD;
BEGIN
    FOR sequence_record IN
        SELECT DISTINCT sequence.relname
          FROM pg_catalog.pg_class sequence
          JOIN pg_catalog.pg_depend dependency
            ON dependency.objid = sequence.oid
          JOIN pg_catalog.pg_class target
            ON target.oid = dependency.refobjid
         WHERE sequence.relkind = 'S'
           AND sequence.relnamespace = 'public'::regnamespace
           AND target.relname LIKE 'erp\_%' ESCAPE '\'
           AND dependency.deptype IN ('a', 'i')
    LOOP
        EXECUTE format(
            'GRANT USAGE, SELECT, UPDATE ON SEQUENCE public.%I'
            ' TO everydayai_sync',
            sequence_record.relname
        );
    END LOOP;
END
$sequences$;

CREATE OR REPLACE FUNCTION sync_refresh_kit_stock()
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_sync' THEN
        RAISE EXCEPTION 'SYNC_KIT_STOCK_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF NOT pg_catalog.pg_try_advisory_xact_lock(
        pg_catalog.hashtext('mv_kit_stock')
    ) THEN
        RETURN FALSE;
    END IF;
    REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_kit_stock;
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION runtime_list_kit_stock()
RETURNS TABLE (
    org_id UUID,
    outer_id TEXT,
    sku_outer_id TEXT,
    item_name TEXT,
    properties_name TEXT,
    warehouse_id TEXT,
    sellable_num INTEGER,
    total_stock INTEGER,
    lock_stock INTEGER,
    purchase_num INTEGER,
    stock_status INTEGER
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org_id UUID := public.tenant_org_id();
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR v_org_id IS NULL
       OR NOT public.tenant_actor_is_active_member(v_org_id) THEN
        RAISE EXCEPTION 'RUNTIME_KIT_STOCK_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
    SELECT stock.org_id, stock.outer_id::TEXT, stock.sku_outer_id::TEXT,
           stock.item_name::TEXT, stock.properties_name::TEXT,
           stock.warehouse_id::TEXT, stock.sellable_num, stock.total_stock,
           stock.lock_stock, stock.purchase_num, stock.stock_status
      FROM public.mv_kit_stock stock
     WHERE stock.org_id = v_org_id;
END;
$$;

ALTER FUNCTION erp_distribution_query(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, NUMERIC[], TEXT
) SECURITY INVOKER;

REVOKE ALL ON TABLE public.mv_kit_stock
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
REVOKE ALL ON FUNCTION sync_refresh_kit_stock()
FROM PUBLIC, everydayai, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
REVOKE ALL ON FUNCTION runtime_list_kit_stock()
FROM PUBLIC, everydayai, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION sync_refresh_kit_stock()
TO everydayai_sync;
GRANT EXECUTE ON FUNCTION runtime_list_kit_stock()
TO everydayai_runtime;
GRANT SELECT ON TABLE public.mv_kit_stock TO everydayai;

DO $function_acl$
DECLARE
    runtime_functions CONSTANT TEXT[] := ARRAY[
        'erp_cross_metric_query',
        'erp_distinct_shops',
        'erp_distribution_query',
        'erp_global_stats_query',
        'erp_order_stats_grouped',
        'erp_repurchase_rate_query',
        'erp_ship_time_query',
        'erp_trend_query'
    ];
    sync_functions CONSTANT TEXT[] := ARRAY[
        'erp_aggregate_daily_stats',
        'erp_aggregate_daily_stats_batch',
        'erp_try_acquire_sync_lock'
    ];
    function_record RECORD;
BEGIN
    FOR function_record IN
        SELECT procedure.oid::regprocedure AS signature
          FROM pg_catalog.pg_proc procedure
         WHERE procedure.pronamespace = 'public'::regnamespace
           AND procedure.proname = ANY(runtime_functions)
    LOOP
        EXECUTE format(
            'REVOKE ALL ON FUNCTION %s FROM PUBLIC, everydayai_runtime, everydayai_sync',
            function_record.signature
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO everydayai_runtime, everydayai',
            function_record.signature
        );
    END LOOP;
    FOR function_record IN
        SELECT procedure.oid::regprocedure AS signature
          FROM pg_catalog.pg_proc procedure
         WHERE procedure.pronamespace = 'public'::regnamespace
           AND procedure.proname = ANY(sync_functions)
    LOOP
        EXECUTE format(
            'REVOKE ALL ON FUNCTION %s FROM PUBLIC, everydayai_runtime, everydayai_sync',
            function_record.signature
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO everydayai_sync, everydayai',
            function_record.signature
        );
    END LOOP;
END
$function_acl$;

RESET ROLE;
