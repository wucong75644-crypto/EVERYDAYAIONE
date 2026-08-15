#!/bin/bash
set -euo pipefail

test "$(id -u)" -eq 0
action=${1:?prepare or cleanup required}
service_path=system.slice/everydayai-conversation-actor.service
controllers=(memory pids cpu,cpuacct)

case "$action" in
  prepare)
    for controller in "${controllers[@]}"; do
      parent="/sys/fs/cgroup/${controller}/${service_path}"
      test -d "$parent"
      install -d -o everydayai-actor -g everydayai-app -m 0700 \
        "${parent}/nsjail"
    done
    ;;
  cleanup)
    for controller in "${controllers[@]}"; do
      target="/sys/fs/cgroup/${controller}/${service_path}/nsjail"
      if [ -d "$target" ]; then
        find "$target" -mindepth 1 -maxdepth 1 -type d \
          -name 'NSJAIL.*' -exec rmdir -- {} +
        rmdir "$target"
      fi
    done
    ;;
  *)
    echo "unsupported action: $action" >&2
    exit 2
    ;;
esac
