#!/usr/bin/env bash
# Local convenience entry. deploy/ is synchronized by the controlled release.
set -euo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec "$repo_dir/deploy/setup-agent-reach.sh" "$@"
