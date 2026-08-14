#!/bin/bash
set -euo pipefail

required=(
  TENANT_DB_ADMIN_URL
  EVERYDAYAI_AGENT_RUNTIME_WORKER_PASSWORD
  EVERYDAYAI_PROJECTION_WORKER_PASSWORD
  EVERYDAYAI_AUTHORIZATION_WORKER_PASSWORD
  EVERYDAYAI_RUNTIME_ADMIN_PASSWORD
)
for name in "${required[@]}"; do
  test -n "${!name:-}" || { echo "missing $name" >&2; exit 1; }
  if [[ "$name" == *_PASSWORD ]]; then
    value=${!name}
    test "${#value}" -ge 24 || { echo "$name too short" >&2; exit 1; }
  fi
done

sql_literal() { printf %s "$1" | sed "s/'/''/g"; }
agent=$(sql_literal "$EVERYDAYAI_AGENT_RUNTIME_WORKER_PASSWORD")
projection=$(sql_literal "$EVERYDAYAI_PROJECTION_WORKER_PASSWORD")
authorization=$(sql_literal "$EVERYDAYAI_AUTHORIZATION_WORKER_PASSWORD")
admin=$(sql_literal "$EVERYDAYAI_RUNTIME_ADMIN_PASSWORD")

{
  cat <<SQL
\\set ON_ERROR_STOP on
DO \$bootstrap\$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='everydayai_owner') THEN
    RAISE EXCEPTION 'EVERYDAYAI_OWNER_ROLE_REQUIRED';
  END IF;
END
\$bootstrap\$;
SELECT format('CREATE ROLE everydayai_agent_runtime_worker LOGIN PASSWORD %L NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS','$agent')
WHERE NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='everydayai_agent_runtime_worker') \\gexec
SELECT format('CREATE ROLE everydayai_projection_worker LOGIN PASSWORD %L NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS','$projection')
WHERE NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='everydayai_projection_worker') \\gexec
SELECT format('CREATE ROLE everydayai_authorization_worker LOGIN PASSWORD %L NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS','$authorization')
WHERE NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='everydayai_authorization_worker') \\gexec
SELECT format('CREATE ROLE everydayai_runtime_admin LOGIN PASSWORD %L NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS','$admin')
WHERE NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='everydayai_runtime_admin') \\gexec
ALTER ROLE everydayai_agent_runtime_worker LOGIN PASSWORD '$agent' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE everydayai_projection_worker LOGIN PASSWORD '$projection' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE everydayai_authorization_worker LOGIN PASSWORD '$authorization' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE everydayai_runtime_admin LOGIN PASSWORD '$admin' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
REVOKE everydayai_owner FROM everydayai_agent_runtime_worker;
REVOKE everydayai_owner FROM everydayai_projection_worker;
REVOKE everydayai_owner FROM everydayai_authorization_worker;
REVOKE everydayai_owner FROM everydayai_runtime_admin;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
  --no-psqlrc --set=ON_ERROR_STOP=1
