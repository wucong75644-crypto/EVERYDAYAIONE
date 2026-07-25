SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS runtime_list_kit_stock();
DROP FUNCTION IF EXISTS sync_refresh_kit_stock();

DO $rollback$
DECLARE
    table_record RECORD;
BEGIN
    FOR table_record IN
        SELECT relation.relname
          FROM pg_catalog.pg_class relation
         WHERE relation.relnamespace = 'public'::regnamespace
           AND relation.relkind IN ('r', 'p')
           AND (
               relation.relname LIKE 'erp\_%' ESCAPE '\'
               OR relation.relname LIKE 'kuaimai\_%' ESCAPE '\'
           )
    LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS sync_domain_owner ON public.%I',
            table_record.relname
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS sync_domain_legacy ON public.%I',
            table_record.relname
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS sync_domain_service ON public.%I',
            table_record.relname
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS sync_domain_runtime_read ON public.%I',
            table_record.relname
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS sync_domain_classification_sync ON public.%I',
            table_record.relname
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS sync_domain_classification_runtime ON public.%I',
            table_record.relname
        );
        EXECUTE format(
            'ALTER TABLE public.%I NO FORCE ROW LEVEL SECURITY',
            table_record.relname
        );
        EXECUTE format(
            'ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY',
            table_record.relname
        );
        EXECUTE format(
            'REVOKE ALL ON TABLE public.%I FROM everydayai_sync',
            table_record.relname
        );
    END LOOP;
END
$rollback$;

GRANT SELECT ON TABLE public.mv_kit_stock TO everydayai;

RESET ROLE;
