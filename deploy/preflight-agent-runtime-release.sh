#!/bin/bash
# Non-production-safe preflight. It never changes a runtime switch or role.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
test -n "${TENANT_DB_ADMIN_URL:-}"
with_sandbox=false
if [ "${1:-}" = "--with-sandbox" ]; then
  with_sandbox=true
elif [ "$#" -ne 0 ]; then
  echo "usage: $0 [--with-sandbox]" >&2
  exit 2
fi

cat <<'SQL' | python3 "$repo_root/deploy/run-psql-admin.py" \
  --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only
DO $preflight$
DECLARE role_name TEXT;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'everydayai_agent_runtime_worker',
    'everydayai_projection_worker',
    'everydayai_authorization_worker',
    'everydayai_runtime_admin'
  ] LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_roles
      WHERE rolname=role_name AND rolcanlogin AND NOT rolsuper
        AND NOT rolcreatedb AND NOT rolcreaterole
        AND NOT rolreplication AND NOT rolbypassrls
    ) THEN
      RAISE EXCEPTION 'AGENT_RUNTIME_ROLE_PREFLIGHT_FAILED:%', role_name;
    END IF;
  END LOOP;
  IF to_regclass('public.agent_runtime_control') IS NULL THEN
    RAISE EXCEPTION 'AGENT_RUNTIME_MIGRATION_223_REQUIRED';
  END IF;
  IF EXISTS (
    SELECT 1 FROM agent_runtime_control
    WHERE ingress_enabled OR command_claim_enabled
      OR action_dispatch_enabled OR safe_actions_enabled
      OR non_safe_actions_enabled OR code_execute_enabled
      OR projection_enabled OR authorization_recovery_enabled
      OR tool_confirmation_enabled
  ) THEN
    RAISE EXCEPTION 'AGENT_RUNTIME_SWITCHES_MUST_START_CLOSED';
  END IF;
END
$preflight$;
SELECT 'agent-runtime-database-preflight=ok';
SQL

"$repo_root/backend/venv/bin/python" \
  "$repo_root/deploy/runtime-redis-probe.py"

if "$with_sandbox"; then
  test "$(systemctl show everydayai-sandbox-worker -p User --value)" = \
    everydayai-sandbox
  test "$(systemctl show everydayai-sandbox-worker -p Group --value)" = \
    everydayai-sandbox
  supplementary=$(
    systemctl show everydayai-sandbox-worker \
      -p SupplementaryGroups --value | tr ' ' '\n' | sort -u
  )
  test "$supplementary" = everydayai-sandbox-io
  runuser --preserve-environment -u everydayai-sandbox -- \
    "$repo_root/deploy/runtime-capability-probe.sh" sandbox
fi

echo "agent-runtime-release-preflight=ok"
