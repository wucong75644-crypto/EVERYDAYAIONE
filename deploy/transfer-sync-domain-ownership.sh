#!/bin/bash

set -euo pipefail

if [ -z "${TENANT_DB_ADMIN_URL:-}" ]; then
    echo "❌ 缺少 TENANT_DB_ADMIN_URL" >&2
    exit 1
fi

legacy_owner=${LEGACY_DATABASE_OWNER:-everydayai}
if [[ ! "$legacy_owner" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; then
    echo "❌ LEGACY_DATABASE_OWNER 不是合法 PostgreSQL 角色名" >&2
    exit 1
fi

{
    cat <<SQL
\set ON_ERROR_STOP on
BEGIN;

DO \$transfer\$
DECLARE
    target_relations CONSTANT TEXT[] := ARRAY[
        'deleted_files',
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
        'kuaimai_sync_logs',
        'mv_kit_stock'
    ];
    missing_roles TEXT;
    missing_relations TEXT;
    unexpected_owners TEXT;
    relation_name TEXT;
    relation_kind "char";
    sequence_record RECORD;
    function_record RECORD;
BEGIN
    SELECT string_agg(required_role, ', ' ORDER BY required_role)
      INTO missing_roles
      FROM unnest(ARRAY[
          'everydayai_owner',
          'everydayai_runtime',
          'everydayai_sync',
          '${legacy_owner}'
      ]) AS required_role
     WHERE to_regrole(required_role) IS NULL;
    IF missing_roles IS NOT NULL THEN
        RAISE EXCEPTION 'SYNC_DOMAIN_ROLE_MISSING: %', missing_roles;
    END IF;

    SELECT string_agg(required_relation, ', ' ORDER BY required_relation)
      INTO missing_relations
      FROM unnest(target_relations) AS required_relation
     WHERE to_regclass('public.' || required_relation) IS NULL;
    IF missing_relations IS NOT NULL THEN
        RAISE EXCEPTION 'SYNC_DOMAIN_RELATION_MISSING: %', missing_relations;
    END IF;

    SELECT string_agg(
               relation.relname || '=' || owner_role.rolname,
               ', ' ORDER BY relation.relname
           )
      INTO unexpected_owners
      FROM pg_catalog.pg_class relation
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = relation.relowner
     WHERE relation.relnamespace = 'public'::regnamespace
       AND relation.relname = ANY(target_relations)
       AND owner_role.rolname NOT IN ('${legacy_owner}', 'everydayai_owner');
    IF unexpected_owners IS NOT NULL THEN
        RAISE EXCEPTION 'SYNC_DOMAIN_OWNER_UNEXPECTED: %', unexpected_owners;
    END IF;

    FOREACH relation_name IN ARRAY target_relations LOOP
        SELECT relkind INTO STRICT relation_kind
          FROM pg_catalog.pg_class
         WHERE oid = ('public.' || relation_name)::regclass;
        IF relation_kind = 'm' THEN
            EXECUTE format(
                'ALTER MATERIALIZED VIEW public.%I OWNER TO everydayai_owner',
                relation_name
            );
            EXECUTE format(
                'REVOKE ALL ON TABLE public.%I FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker, everydayai_sync',
                relation_name
            );
            EXECUTE format(
                'GRANT SELECT ON TABLE public.%I TO %I',
                relation_name, '${legacy_owner}'
            );
        ELSE
            EXECUTE format(
                'ALTER TABLE public.%I OWNER TO everydayai_owner',
                relation_name
            );
            EXECUTE format(
                'REVOKE ALL ON TABLE public.%I FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker, everydayai_sync',
                relation_name
            );
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO %I',
                relation_name, '${legacy_owner}'
            );
        END IF;
    END LOOP;

    FOR sequence_record IN
        SELECT DISTINCT sequence.relname
          FROM pg_catalog.pg_class sequence
          JOIN pg_catalog.pg_depend dependency ON dependency.objid = sequence.oid
          JOIN pg_catalog.pg_class target ON target.oid = dependency.refobjid
         WHERE sequence.relkind = 'S'
           AND target.relnamespace = 'public'::regnamespace
           AND target.relname = ANY(target_relations)
           AND dependency.deptype IN ('a', 'i')
    LOOP
        EXECUTE format(
            'ALTER SEQUENCE public.%I OWNER TO everydayai_owner',
            sequence_record.relname
        );
        EXECUTE format(
            'REVOKE ALL ON SEQUENCE public.%I FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker, everydayai_sync',
            sequence_record.relname
        );
        EXECUTE format(
            'GRANT USAGE, SELECT, UPDATE ON SEQUENCE public.%I TO %I',
            sequence_record.relname, '${legacy_owner}'
        );
    END LOOP;

    FOR function_record IN
        SELECT procedure.oid::regprocedure AS signature
          FROM pg_catalog.pg_proc procedure
         WHERE procedure.pronamespace = 'public'::regnamespace
           AND (
               procedure.proname LIKE 'erp\_%' ESCAPE '\'
               OR procedure.proname LIKE 'kuaimai\_%' ESCAPE '\'
           )
    LOOP
        EXECUTE format(
            'ALTER FUNCTION %s OWNER TO everydayai_owner',
            function_record.signature
        );
        EXECUTE format(
            'REVOKE ALL ON FUNCTION %s FROM everydayai_runtime, everydayai_sync',
            function_record.signature
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO %I',
            function_record.signature, '${legacy_owner}'
        );
    END LOOP;
END
\$transfer\$;

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Sync 数据域对象已转移给 everydayai_owner"
