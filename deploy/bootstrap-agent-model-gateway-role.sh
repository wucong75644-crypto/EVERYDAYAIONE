#!/bin/bash

set -euo pipefail

for name in TENANT_DB_ADMIN_URL EVERYDAYAI_AGENT_MODEL_GATEWAY_PASSWORD; do
  test -n "${!name:-}" || { echo "missing $name" >&2; exit 1; }
done
test "${#EVERYDAYAI_AGENT_MODEL_GATEWAY_PASSWORD}" -ge 24 || {
  echo "EVERYDAYAI_AGENT_MODEL_GATEWAY_PASSWORD too short" >&2
  exit 1
}

sql_literal() { printf %s "$1" | sed "s/'/''/g"; }
gateway=$(sql_literal "$EVERYDAYAI_AGENT_MODEL_GATEWAY_PASSWORD")

{
  cat <<SQL
\set ON_ERROR_STOP on
DO \$bootstrap\$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='everydayai_owner') THEN
    RAISE EXCEPTION 'EVERYDAYAI_OWNER_ROLE_REQUIRED';
  END IF;
END
\$bootstrap\$;
SELECT format('CREATE ROLE everydayai_agent_model_gateway LOGIN PASSWORD %L NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS','$gateway')
WHERE NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='everydayai_agent_model_gateway') \gexec
ALTER ROLE everydayai_agent_model_gateway LOGIN PASSWORD '$gateway' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
REVOKE everydayai_owner FROM everydayai_agent_model_gateway;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
  --no-psqlrc --set=ON_ERROR_STOP=1
