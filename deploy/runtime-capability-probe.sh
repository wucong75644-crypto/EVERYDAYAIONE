#!/bin/bash
set -euo pipefail
role=${1:?role required}
test -n "${WORKER_DATABASE_URL:-}"
repo_root=$(cd "$(dirname "$0")/.." && pwd)
"$repo_root/backend/venv/bin/python" \
  "$repo_root/deploy/runtime-worker-db-probe.py" "$role"
if [ "$role" != sandbox ]; then exit 0; fi
test "$(id -un)" = everydayai-sandbox
test "$(id -gn)" = everydayai-sandbox
test "$(id -u)" -ne 0
test "$(id -g)" -ne 0
groups=$(id -Gn | tr ' ' '\n' | sort -u)
test "$groups" = "$(printf '%s\n' everydayai-sandbox everydayai-sandbox-io | sort)"
workspace_owner=$(stat -c '%U:%G:%a' "$SANDBOX_JOB_ROOT")
test "$workspace_owner" = root:everydayai-sandbox-io:2770
test -w "$SANDBOX_JOB_ROOT"
if env | cut -d= -f1 | grep -Eq \
  '^(REDIS|OSS_|MODEL_|JWT|WECOM|DASHSCOPE|OPENROUTER|KIE_|GOOGLE_)'; then
  echo "forbidden credential namespace in Sandbox environment" >&2
  exit 1
fi
test ! -r "$repo_root/backend/.env"
test "$(uname -s)" = Linux
test "$(stat -fc %T /sys/fs/cgroup)" = cgroup2fs
controllers=$(cat /sys/fs/cgroup/cgroup.controllers)
for item in cpu memory pids; do grep -qw "$item" <<<"$controllers"; done
test "$SANDBOX_WORKER_CONCURRENCY" = 1
test "$SANDBOX_PARTIAL_RETENTION_SECONDS" = 86400
test -d "$SANDBOX_CGROUP_V2_MOUNT"
test -w "$SANDBOX_CGROUP_V2_MOUNT"
findmnt -T "$SANDBOX_ROOTFS" -n -o OPTIONS |
  tr ',' '\n' | grep -qx ro
echo "$SANDBOX_NSJAIL_SHA256  $SANDBOX_NSJAIL_PATH" | sha256sum -c -
echo "$SANDBOX_ROOTFS_SHA256  $SANDBOX_ROOTFS_MANIFEST" | sha256sum -c -
echo "$SANDBOX_SECCOMP_SHA256  $SANDBOX_SECCOMP_POLICY" | sha256sum -c -
"$repo_root/backend/venv/bin/python" -m \
  services.agent.runtime.sandbox.rootfs_manifest verify \
  "$SANDBOX_ROOTFS" "$SANDBOX_ROOTFS_MANIFEST"
"$SANDBOX_NSJAIL_PATH" --help 2>&1 | grep -q -- --use_cgroupv2
"$SANDBOX_NSJAIL_PATH" --help 2>&1 | grep -q -- --cgroup_mem_swap_max
test -r "$SANDBOX_ROOTFS/usr/bin/python3.12"
# The canary must have no host network and must leave no process/cgroup residue.
before=$(find /sys/fs/cgroup -maxdepth 4 -iname '*nsjail*' -print | sort)
set +e
"$SANDBOX_NSJAIL_PATH" --mode o --chroot "$SANDBOX_ROOTFS" \
  --user "65534:$(id -u):1" --group "65534:$(id -g):1" \
  --disable_proc --iface_no_lo \
  --use_cgroupv2 --cgroupv2_mount "$SANDBOX_CGROUP_V2_MOUNT" \
  --cgroup_mem_swap_max 0 \
  --time_limit 5 --seccomp_policy "$SANDBOX_SECCOMP_POLICY" \
  -- /usr/bin/python3.12 -I -c \
  'import socket; socket.create_connection(("1.1.1.1",53),1)' >/dev/null 2>&1
network_rc=$?
set -e
test "$network_rc" -ne 0
test -z "$(pgrep -f "$SANDBOX_NSJAIL_PATH" || true)"
after=$(find /sys/fs/cgroup -maxdepth 4 -iname '*nsjail*' -print | sort)
test "$before" = "$after"
