#!/bin/bash

set -euo pipefail

if [ -z "${TENANT_DB_ADMIN_URL:-}" ]; then
    echo "❌ 缺少 TENANT_DB_ADMIN_URL" >&2
    exit 1
fi
if ! command -v psql >/dev/null 2>&1; then
    echo "❌ 未找到 psql" >&2
    exit 1
fi

cat <<'SQL' | python3 "$(dirname "$0")/../run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1
\set ON_ERROR_STOP on

DO $preflight$
DECLARE
    invalid_force_tables TEXT;
BEGIN
    IF EXISTS (
        SELECT 1
          FROM unnest(ARRAY['knowledge_nodes', 'knowledge_edges',
              'knowledge_metrics', 'scoring_audit_log', 'tool_audit_log'])
               AS table_name
          CROSS JOIN unnest(ARRAY['everydayai_wecom_runtime',
              'everydayai_worker', 'everydayai_sync']) AS role_name
         WHERE has_table_privilege(
             role_name, 'public.' || table_name,
             'SELECT, INSERT, UPDATE, DELETE'
         )
            OR has_any_column_privilege(
                role_name, 'public.' || table_name,
                'SELECT, INSERT, UPDATE, REFERENCES'
            )
    ) OR EXISTS (
        SELECT 1 FROM (VALUES
            ('knowledge_nodes', 'SELECT', TRUE),
            ('knowledge_nodes', 'INSERT', TRUE),
            ('knowledge_nodes', 'UPDATE', TRUE),
            ('knowledge_nodes', 'DELETE', FALSE),
            ('knowledge_edges', 'SELECT', TRUE),
            ('knowledge_edges', 'INSERT', TRUE),
            ('knowledge_edges', 'UPDATE', TRUE),
            ('knowledge_edges', 'DELETE', FALSE),
            ('knowledge_metrics', 'SELECT', FALSE),
            ('knowledge_metrics', 'INSERT', TRUE),
            ('knowledge_metrics', 'UPDATE', FALSE),
            ('knowledge_metrics', 'DELETE', FALSE),
            ('scoring_audit_log', 'SELECT', FALSE),
            ('scoring_audit_log', 'INSERT', FALSE),
            ('scoring_audit_log', 'UPDATE', FALSE),
            ('scoring_audit_log', 'DELETE', FALSE),
            ('tool_audit_log', 'SELECT', FALSE),
            ('tool_audit_log', 'INSERT', FALSE),
            ('tool_audit_log', 'UPDATE', FALSE),
            ('tool_audit_log', 'DELETE', FALSE)
        ) AS expected(table_name, privilege_name, allowed)
        WHERE has_table_privilege(
            'everydayai_runtime', 'public.' || table_name, privilege_name
        ) IS DISTINCT FROM allowed
    ) OR has_any_column_privilege(
        'everydayai_runtime', 'public.scoring_audit_log',
        'SELECT, INSERT, UPDATE, REFERENCES'
    ) OR has_any_column_privilege(
        'everydayai_runtime', 'public.tool_audit_log',
        'SELECT, INSERT, UPDATE, REFERENCES'
    ) THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_SERVICE_ACL_INVALID';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM (VALUES ('knowledge_nodes', 'knowledge_nodes_owner_all'),
              ('knowledge_edges', 'knowledge_edges_owner_all'),
              ('knowledge_metrics', 'knowledge_metrics_owner_all'),
              ('scoring_audit_log', 'scoring_audit_log_owner_all'),
              ('tool_audit_log', 'tool_audit_log_owner_all'))
               AS required(table_name, policy_name)
         WHERE NOT EXISTS (
             SELECT 1 FROM pg_catalog.pg_policies policy
              WHERE policy.schemaname = 'public'
                AND policy.tablename = required.table_name
                AND policy.policyname = required.policy_name
                AND policy.cmd = 'ALL'
                AND policy.roles = ARRAY['everydayai_owner'::name]
                AND policy.qual = 'true'
                AND policy.with_check = 'true'
         )
    ) THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_OWNER_POLICY_INVALID';
    END IF;

    SELECT string_agg(relation.relname, ', ' ORDER BY relation.relname)
      INTO invalid_force_tables
      FROM pg_catalog.pg_class relation
     WHERE relation.oid = ANY(ARRAY[
         'public.knowledge_nodes'::regclass,
         'public.knowledge_edges'::regclass,
         'public.knowledge_metrics'::regclass,
         'public.scoring_audit_log'::regclass,
         'public.tool_audit_log'::regclass
     ])
       AND NOT relation.relforcerowsecurity;
    IF invalid_force_tables IS NOT NULL THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_FORCE_RLS_INVALID: %',
            invalid_force_tables;
    END IF;
END
$preflight$;
SQL

echo "✅ Knowledge/Audit owner、FORCE RLS 与服务角色 ACL 检查通过"
