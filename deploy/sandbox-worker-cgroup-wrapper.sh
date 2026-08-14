#!/bin/bash

set -euo pipefail

root=${SANDBOX_CGROUP_V2_MOUNT:?SANDBOX_CGROUP_V2_MOUNT required}
runner=${SANDBOX_CGROUP_V2_RUNNER:-${root}/runner}
if [ "$(id -u)" -eq 0 ]; then
  echo 'sandbox worker cgroup wrapper must not run as root' >&2
  exit 1
fi
case "$runner" in
  "$root"/*) ;;
  *) echo 'sandbox worker cgroup runner must be beneath mount' >&2; exit 1 ;;
esac
test -d "$root" -a -w "$root"
mkdir -p "$runner"
echo "$$" > "$runner/cgroup.procs"
test -z "$(cat "$root/cgroup.procs")"
export SANDBOX_CGROUP_V2_RUNNER="$runner"
test "$#" -gt 0
exec "$@"
