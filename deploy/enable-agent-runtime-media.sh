#!/bin/bash

set -Eeuo pipefail

deploy_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
backend_dir=${REMOTE_BACKEND_DIR:-/var/www/everydayai/backend}
env_dir=${AGENT_RUNTIME_ENV_DIR:-/etc/everydayai}
transaction_root=${CONTROL_PLANE_TRANSACTION_ROOT:-/var/backups/everydayai-media-activation}
release_sha=
remote_mode=false

usage() {
    echo "usage: $0 --expected-sha SHA [--remote]" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --expected-sha)
            [ $# -ge 2 ] && [ -z "$release_sha" ] || { usage; exit 2; }
            release_sha=$2
            shift 2
            ;;
        --remote)
            [ "$remote_mode" = false ] || { usage; exit 2; }
            remote_mode=true
            shift
            ;;
        *) usage; exit 2 ;;
    esac
done

[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || { usage; exit 2; }

if [ "$remote_mode" = false ]; then
    source "${deploy_dir}/config.env"
    : "${SERVER_HOST:?deploy/config.env 缺少 SERVER_HOST}"
    : "${SERVER_USER:?deploy/config.env 缺少 SERVER_USER}"
    : "${SERVER_PORT:?deploy/config.env 缺少 SERVER_PORT}"
    : "${REMOTE_APP_DIR:?deploy/config.env 缺少 REMOTE_APP_DIR}"
    ssh -p "$SERVER_PORT" "$SERVER_USER@$SERVER_HOST" \
        "sudo bash ${REMOTE_APP_DIR}/deploy/enable-agent-runtime-media.sh \
        --remote --expected-sha ${release_sha}"
    exit $?
fi

provisioner="${deploy_dir}/provision-control-plane-worker-envs.py"
python_bin="${backend_dir}/venv/bin/python"
published=false
rollback() {
    local result=$1
    trap - EXIT
    if [ "$result" -ne 0 ] && [ "$published" = true ]; then
        set +e
        "$python_bin" "$provisioner" rollback \
            --env-dir "$env_dir" --release-sha "$release_sha" \
            --transaction-root "$transaction_root"
        systemctl restart everydayai-agent-runtime everydayai-agent-projection
        exit "$result"
    fi
    exit "$result"
}
trap 'rollback $?' EXIT

"$python_bin" "$provisioner" prepare \
    --backend-dir "$backend_dir" --env-dir "$env_dir" \
    --release-sha "$release_sha" --transaction-root "$transaction_root" --media-on
"$python_bin" "$provisioner" preflight \
    --env-dir "$env_dir" --release-sha "$release_sha" \
    --transaction-root "$transaction_root"
"$python_bin" "$provisioner" publish \
    --env-dir "$env_dir" --release-sha "$release_sha" \
    --transaction-root "$transaction_root"
"$python_bin" "$provisioner" verify \
    --env-dir "$env_dir" --release-sha "$release_sha" \
    --transaction-root "$transaction_root"
published=true

systemctl restart everydayai-agent-runtime everydayai-agent-projection
for socket_path in /run/everydayai-agent-runtime/health.sock \
    /run/everydayai-agent-projection/health.sock; do
    for attempt in $(seq 1 30); do
        [ -S "$socket_path" ] && break
        [ "$attempt" -eq 30 ] && {
            systemctl --no-pager --full status \
                "$(basename "${socket_path%/health.sock}")" || true
            exit 1
        }
        sleep 1
    done
done

set -a
source /etc/everydayai/runtime-admin.env
set +a
"$python_bin" - <<'PY'
import json
import os
import socket
import time
from uuid import uuid4

import psycopg


def run() -> None:
    dsn = os.environ.get("RUNTIME_ADMIN_DATABASE_URL")
    if not dsn:
        raise RuntimeError("RUNTIME_ADMIN_DATABASE_URL_MISSING")
    with psycopg.connect(dsn) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.access_kind','runtime_admin',false)")
            context = None
            for _ in range(30):
                cursor.execute("SELECT get_agent_runtime_media_admin_context_v1()")
                context = cursor.fetchone()[0]
                readiness = context.get("readiness", {})
                if readiness.get("projection_heartbeat_fresh"):
                    break
                time.sleep(1)
            readiness = context.get("readiness", {}) if context else {}
            if not readiness.get("projection_heartbeat_fresh"):
                try:
                    health_socket = socket.socket(socket.AF_UNIX)
                    health_socket.settimeout(3)
                    health_socket.connect(
                        "/run/everydayai-agent-projection/health.sock",
                    )
                    health_socket.sendall(b"x")
                    health = health_socket.recv(4096).decode()
                    health_socket.close()
                except Exception as error:
                    health = type(error).__name__
                print(json.dumps({
                    "media_context": context,
                    "projection_health": health,
                }, sort_keys=True))
                raise RuntimeError("PROJECTION_MEDIA_READINESS_TIMEOUT")
            actor = context["actor_user_id"]
            org = context["org_id"]
            cursor.execute("SELECT set_config('app.actor_user_id',%s,false)", (str(actor),))
            cursor.execute("SELECT set_config('app.org_id',%s,false)", (str(org),))
            request_id = uuid4()
            cursor.execute(
                "SELECT set_agent_runtime_media_production_state_v1(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    request_id, actor, org,
                    readiness["state_version"], True, True, True, True,
                    "enable frozen image v13 production release",
                ),
            )
            result = cursor.fetchone()[0]
            if result.get("outcome") != "applied":
                raise RuntimeError(
                    "MEDIA_ACTIVATION_NOT_APPLIED:" + json.dumps(result, sort_keys=True)
                )
            final_readiness = result.get("readiness", {})
            if not final_readiness.get("ready"):
                raise RuntimeError("MEDIA_ACTIVATION_READINESS_NOT_READY")
            print(json.dumps({
                "outcome": result["outcome"],
                "image_ingress_enabled": result["image_ingress_enabled"],
                "ready": final_readiness["ready"],
                "state_version": final_readiness["state_version"],
            }, sort_keys=True))


run()
PY

published=false
trap - EXIT
echo "✅ Runtime image v13 production activation completed"
