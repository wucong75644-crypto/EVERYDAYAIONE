#!/bin/bash

set -euo pipefail

script_dir=$(cd "$(dirname "$0")" && pwd)

bash "$script_dir/preflight/tenant-core.sh"
bash "$script_dir/preflight/worker-control.sh"

echo "✅ 租户数据库核心域与 Worker Control 域只读前置检查通过"
