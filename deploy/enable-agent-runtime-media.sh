#!/bin/bash

set -Eeuo pipefail

deploy_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
backend_dir=${REMOTE_BACKEND_DIR:-/var/www/everydayai/backend}
env_dir=${AGENT_RUNTIME_ENV_DIR:-/etc/everydayai}
transaction_root=${CONTROL_PLANE_TRANSACTION_ROOT:-/var/backups/everydayai-media-activation}
release_sha=
remote_mode=false
runtime_env_path=
runtime_env_backup=
runtime_env_changed=false

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
runtime_env_path="${backend_dir}/.env.runtime"
runtime_env_backup="${transaction_root}/${release_sha}/backend.env.runtime.before"

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
        if [ "$runtime_env_changed" = true ]; then
            "$python_bin" - "$runtime_env_path" "$runtime_env_backup" <<'PY'
import os
import sys
from pathlib import Path

target = Path(sys.argv[1])
backup = Path(sys.argv[2])
if not backup.is_file() or backup.is_symlink():
    raise SystemExit("TOOL_CONFIRMATION_ENV_BACKUP_MISSING")
temporary = target.with_name(f".{target.name}.rollback")
try:
    temporary.write_bytes(backup.read_bytes())
    stat_result = target.stat()
    os.chown(temporary, stat_result.st_uid, stat_result.st_gid)
    os.chmod(temporary, stat_result.st_mode & 0o7777)
    os.replace(temporary, target)
finally:
    temporary.unlink(missing_ok=True)
backup.unlink()
PY
            systemctl restart everydayai-backend
        fi
        systemctl restart everydayai-agent-runtime everydayai-agent-projection
        exit "$result"
    fi
    exit "$result"
}
trap 'rollback $?' EXIT

"$python_bin" "$provisioner" prepare \
    --backend-dir "$backend_dir" --env-dir "$env_dir" \
    --release-sha "$release_sha" --transaction-root "$transaction_root" \
    --media-on --runtime-on
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

# Confirm-level tools (including image generation) need the Redis-backed
# confirmation capability. This is deliberately independent from the
# code_execute/Sandbox gate.
set +e
"$python_bin" - "$runtime_env_path" "$runtime_env_backup" <<'PY'
import os
import stat
import sys
import tempfile
from pathlib import Path

target = Path(sys.argv[1])
backup = Path(sys.argv[2])
key = "TOOL_CONFIRMATION_V3_ENABLED"
if target.is_symlink() or not target.is_file():
    raise SystemExit("RUNTIME_ENV_INVALID")
raw = target.read_bytes()
text = raw.decode("utf-8")
lines = text.splitlines(keepends=True)
matches = [index for index, line in enumerate(lines)
           if line.partition("=")[0] == key]
if len(matches) > 1:
    raise SystemExit("RUNTIME_ENV_DUPLICATE_TOOL_CONFIRMATION_FLAG")
if matches and lines[matches[0]].partition("=")[2].strip().lower() == "true":
    raise SystemExit(0)

backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
if backup.exists() or backup.is_symlink():
    raise SystemExit("RUNTIME_ENV_BACKUP_ALREADY_EXISTS")
backup.write_bytes(raw)
os.chmod(backup, 0o600)

replacement = f"{key}=true\n"
if matches:
    lines[matches[0]] = replacement
else:
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n"
    lines.append(replacement)
updated = "".join(lines).encode("utf-8")
original_stat = target.stat()
mode = stat.S_IMODE(original_stat.st_mode)
descriptor, temporary_name = tempfile.mkstemp(
    prefix=f".{target.name}.", dir=target.parent,
)
temporary = Path(temporary_name)
try:
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(updated)
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
    os.chmod(temporary, mode)
    os.replace(temporary, target)
finally:
    temporary.unlink(missing_ok=True)
raise SystemExit(10)
PY
runtime_env_result=$?
set -e
case "$runtime_env_result" in
    0) ;;
    10) runtime_env_changed=true ;;
    *) exit "$runtime_env_result" ;;
esac

systemctl restart everydayai-backend
systemctl is-active --quiet everydayai-backend

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
                if context.get("readiness", {}).get("projection_heartbeat_fresh"):
                    break
                time.sleep(1)
            readiness = context.get("readiness", {})
            if not (
                readiness.get("projection_owner_ready")
                and readiness.get("projection_heartbeat_fresh")
            ):
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
                process_media_env = {}
                try:
                    import subprocess
                    pid = subprocess.check_output(
                        ["systemctl", "show", "everydayai-agent-projection",
                         "-p", "MainPID", "--value"], text=True,
                    ).strip()
                    raw_env = open(f"/proc/{pid}/environ", "rb").read()
                    for item in raw_env.split(b"\0"):
                        key, separator, value = item.partition(b"=")
                        if separator and key.decode() in {
                            "AGENT_RUNTIME_MEDIA_ENABLED",
                            "AGENT_RUNTIME_MEDIA_PROVIDER_PROBE_PASSED",
                            "MEDIA_CDN_DOMAIN",
                            "MEDIA_RESULT_ALLOWED_HOSTS",
                        }:
                            process_media_env[key.decode()] = value.decode()
                except Exception as error:
                    process_media_env = {"read_error": type(error).__name__}
                print(json.dumps({
                    "media_context": context,
                    "projection_health": health,
                    "projection_process_media_env": process_media_env,
                }, sort_keys=True))
                raise RuntimeError("PROJECTION_MEDIA_READINESS_TIMEOUT")
            actor = context["actor_user_id"]
            org = context["org_id"]
            cursor.execute("SELECT set_config('app.actor_user_id',%s,false)", (str(actor),))
            cursor.execute("SELECT set_config('app.org_id',%s,false)", (str(org),))
            capability_ready = False
            for _ in range(30):
                cursor.execute("SELECT get_agent_runtime_admin_status()")
                admin_status = cursor.fetchone()[0]
                capability_ready = any(
                    item.get("capability_name") == "tool_confirmation_v3_redis"
                    and item.get("ready") is True
                    for item in admin_status.get("capabilities", [])
                )
                if capability_ready:
                    break
                time.sleep(1)
            if not capability_ready:
                raise RuntimeError("TOOL_CONFIRMATION_CAPABILITY_NOT_READY")
            result = None
            for _ in range(5):
                cursor.execute("SELECT get_agent_runtime_media_admin_context_v1()")
                context = cursor.fetchone()[0]
                readiness = context.get("readiness", {})
                if not (
                    readiness.get("projection_owner_ready")
                    and readiness.get("projection_heartbeat_fresh")
                ):
                    raise RuntimeError("PROJECTION_MEDIA_READINESS_LOST")
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
                if result.get("outcome") != "stale_version":
                    break
                time.sleep(0.2)
            if result is None or result.get("outcome") != "applied":
                raise RuntimeError(
                    "MEDIA_ACTIVATION_NOT_APPLIED:" + json.dumps(result, sort_keys=True)
                )
            control_result = None
            for _ in range(5):
                cursor.execute("SELECT get_agent_runtime_admin_status()")
                admin_status = cursor.fetchone()[0]
                control_version = admin_status.get("control", {}).get(
                    "state_version"
                )
                if control_version is None:
                    raise RuntimeError("RUNTIME_CONTROL_STATE_VERSION_MISSING")
                cursor.execute(
                    "SELECT set_agent_runtime_control(%s,%s,%s,%s)",
                    (
                        uuid4(), control_version,
                        json.dumps({
                            "safe_actions_enabled": True,
                            "non_safe_actions_enabled": True,
                            "tool_confirmation_enabled": True,
                        }),
                        "enable Runtime v3 image control flags",
                    ),
                )
                control_result = cursor.fetchone()[0]
                if control_result.get("outcome") != "stale_version":
                    break
                time.sleep(0.2)
            if control_result is None or control_result.get("outcome") not in {
                "applied", "already_applied"
            }:
                raise RuntimeError(
                    "MEDIA_CONTROL_ACTIVATION_NOT_APPLIED:"
                    + json.dumps(control_result, sort_keys=True)
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

if [ "$runtime_env_changed" = true ]; then
    rm -f "$runtime_env_backup"
    runtime_env_changed=false
fi

published=false
trap - EXIT
echo "✅ Runtime image v13 production activation completed"
