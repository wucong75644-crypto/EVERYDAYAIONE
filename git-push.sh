#!/usr/bin/env bash
# Task-scoped commit/push only; never deploys or merges main.
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
exec python3 "$root/scripts/task-git-push.py" "$@"
