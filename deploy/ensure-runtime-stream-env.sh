#!/usr/bin/env bash
set -euo pipefail

backend_dir=${1:?backend directory is required}
runtime_env=${2:?runtime env path is required}
source "${backend_dir}/.env"

: "${REDIS_HOST:?REDIS_HOST is required}"
: "${REDIS_PORT:?REDIS_PORT is required}"
: "${REDIS_DB:?REDIS_DB is required}"
: "${REDIS_SSL:?REDIS_SSL is required}"
[[ "$REDIS_PORT" =~ ^[0-9]+$ && "$REDIS_DB" =~ ^[0-9]+$ ]]
[[ "$REDIS_SSL" =~ ^(true|false|1|0|yes|no|on|off)$ ]]
[[ "$REDIS_HOST" != *$'\n'* && "${REDIS_PASSWORD:-}" != *$'\n'* ]]

runtime_dir=$(dirname "$runtime_env")
tmp_path=$(mktemp "${runtime_dir}/.agent-runtime-worker.env.XXXXXX")
cleanup() { rm -f "$tmp_path"; }
trap cleanup EXIT

awk -F= '
    $1 == "AGENT_RUNTIME_STREAM_ENABLED" ||
    $1 == "REDIS_HOST" || $1 == "REDIS_PORT" ||
    $1 == "REDIS_PASSWORD" || $1 == "REDIS_DB" || $1 == "REDIS_SSL" { next }
    { print }
' "$runtime_env" > "$tmp_path"
printf '%s\n' \
    'AGENT_RUNTIME_STREAM_ENABLED=true' \
    "REDIS_HOST=${REDIS_HOST}" \
    "REDIS_PORT=${REDIS_PORT}" \
    "REDIS_PASSWORD=${REDIS_PASSWORD:-}" \
    "REDIS_DB=${REDIS_DB}" \
    "REDIS_SSL=${REDIS_SSL}" >> "$tmp_path"

chown --reference="$runtime_env" "$tmp_path"
chmod --reference="$runtime_env" "$tmp_path"
if ! cmp --silent "$tmp_path" "$runtime_env"; then
    mv -f "$tmp_path" "$runtime_env"
fi
