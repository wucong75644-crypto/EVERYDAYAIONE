#!/bin/bash

set -euo pipefail

required_variables=(
    TENANT_DB_ADMIN_URL
    EVERYDAYAI_CONFIG_IMPORT_READER_PASSWORD
    EVERYDAYAI_MIGRATOR_PASSWORD
    EVERYDAYAI_RUNTIME_PASSWORD
    EVERYDAYAI_SANDBOX_WORKER_PASSWORD
    EVERYDAYAI_SYNC_PASSWORD
    EVERYDAYAI_WECOM_RUNTIME_PASSWORD
    EVERYDAYAI_WORKER_PASSWORD
)

for variable_name in "${required_variables[@]}"; do
    if [ -z "${!variable_name:-}" ]; then
        echo "❌ 缺少必需环境变量：${variable_name}" >&2
        exit 1
    fi
done

if ! command -v psql >/dev/null 2>&1; then
    echo "❌ 未找到 psql" >&2
    exit 1
fi

passwords=(
    "$EVERYDAYAI_CONFIG_IMPORT_READER_PASSWORD"
    "$EVERYDAYAI_MIGRATOR_PASSWORD"
    "$EVERYDAYAI_RUNTIME_PASSWORD"
    "$EVERYDAYAI_SANDBOX_WORKER_PASSWORD"
    "$EVERYDAYAI_SYNC_PASSWORD"
    "$EVERYDAYAI_WECOM_RUNTIME_PASSWORD"
    "$EVERYDAYAI_WORKER_PASSWORD"
)

for password in "${passwords[@]}"; do
    if [ "${#password}" -lt 24 ]; then
        echo "❌ 数据库角色密码长度不能少于 24 个字符" >&2
        exit 1
    fi
    if [[ "$password" == *$'\n'* || "$password" == *$'\r'* ]]; then
        echo "❌ 数据库角色密码不能包含换行符" >&2
        exit 1
    fi
done

for left_index in "${!passwords[@]}"; do
    for right_index in "${!passwords[@]}"; do
        if [ "$left_index" -lt "$right_index" ] \
            && [ "${passwords[$left_index]}" = "${passwords[$right_index]}" ]; then
            echo "❌ config-import-reader、migrator、runtime、sync、wecom-runtime、worker 必须使用不同密码" >&2
            exit 1
        fi
    done
done

sql_literal() {
    local value=$1
    printf "%s" "$value" | sed "s/'/''/g"
}

config_import_reader_password=$(
    sql_literal "$EVERYDAYAI_CONFIG_IMPORT_READER_PASSWORD"
)
migrator_password=$(sql_literal "$EVERYDAYAI_MIGRATOR_PASSWORD")
runtime_password=$(sql_literal "$EVERYDAYAI_RUNTIME_PASSWORD")
sandbox_worker_password=$(
    sql_literal "$EVERYDAYAI_SANDBOX_WORKER_PASSWORD"
)
sync_password=$(sql_literal "$EVERYDAYAI_SYNC_PASSWORD")
wecom_runtime_password=$(sql_literal "$EVERYDAYAI_WECOM_RUNTIME_PASSWORD")
worker_password=$(sql_literal "$EVERYDAYAI_WORKER_PASSWORD")

{
    cat <<'SQL'
\set ON_ERROR_STOP on
SET standard_conforming_strings = on;

SELECT 'CREATE ROLE everydayai_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'everydayai_owner'
)
\gexec

SELECT format(
    'CREATE ROLE everydayai_config_import_reader LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
SQL
    printf "    '%s'\n" "$config_import_reader_password"
    cat <<'SQL'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles
     WHERE rolname = 'everydayai_config_import_reader'
)
\gexec

SELECT format(
    'CREATE ROLE everydayai_migrator LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
SQL
    printf "    '%s'\n" "$migrator_password"
    cat <<'SQL'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'everydayai_migrator'
)
\gexec

SELECT format(
    'CREATE ROLE everydayai_runtime LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
SQL
    printf "    '%s'\n" "$runtime_password"
    cat <<'SQL'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'everydayai_runtime'
)
\gexec

SELECT format(
    'CREATE ROLE everydayai_sandbox_worker LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
SQL
    printf "    '%s'\n" "$sandbox_worker_password"
    cat <<'SQL'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles
     WHERE rolname = 'everydayai_sandbox_worker'
)
\gexec

SELECT format(
    'CREATE ROLE everydayai_sync LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
SQL
    printf "    '%s'\n" "$sync_password"
    cat <<'SQL'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'everydayai_sync'
)
\gexec

SELECT format(
    'CREATE ROLE everydayai_wecom_runtime LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
SQL
    printf "    '%s'\n" "$wecom_runtime_password"
    cat <<'SQL'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'everydayai_wecom_runtime'
)
\gexec

SELECT format(
    'CREATE ROLE everydayai_worker LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
SQL
    printf "    '%s'\n" "$worker_password"
    cat <<'SQL'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'everydayai_worker'
)
\gexec

ALTER ROLE everydayai_owner
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
SQL
    printf "ALTER ROLE everydayai_config_import_reader LOGIN PASSWORD '%s' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n" "$config_import_reader_password"
    printf "ALTER ROLE everydayai_migrator LOGIN PASSWORD '%s' INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n" "$migrator_password"
    printf "ALTER ROLE everydayai_runtime LOGIN PASSWORD '%s' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n" "$runtime_password"
    printf "ALTER ROLE everydayai_sandbox_worker LOGIN PASSWORD '%s' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n" "$sandbox_worker_password"
    printf "ALTER ROLE everydayai_sync LOGIN PASSWORD '%s' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n" "$sync_password"
    printf "ALTER ROLE everydayai_wecom_runtime LOGIN PASSWORD '%s' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n" "$wecom_runtime_password"
    printf "ALTER ROLE everydayai_worker LOGIN PASSWORD '%s' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n" "$worker_password"
    cat <<'SQL'

GRANT everydayai_owner TO everydayai_migrator;
REVOKE everydayai_owner FROM everydayai_config_import_reader;
REVOKE everydayai_owner FROM everydayai_runtime;
REVOKE everydayai_owner FROM everydayai_sandbox_worker;
REVOKE everydayai_worker FROM everydayai_sandbox_worker;
GRANT USAGE ON SCHEMA public TO everydayai_sandbox_worker;
REVOKE CREATE ON SCHEMA public FROM everydayai_sandbox_worker;
REVOKE everydayai_owner FROM everydayai_sync;
REVOKE everydayai_owner FROM everydayai_wecom_runtime;
REVOKE everydayai_owner FROM everydayai_worker;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ 租户数据库角色已创建或更新"
