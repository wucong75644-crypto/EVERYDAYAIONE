#!/bin/bash

set -euo pipefail

script_dir=$(cd "$(dirname "$0")" && pwd)

bash "$script_dir/preflight/tenant-role-capabilities.sh"
bash "$script_dir/preflight/tenant-core.sh"
bash "$script_dir/preflight/admin-user-assets-capability.sh"
bash "$script_dir/preflight/worker-control.sh"
bash "$script_dir/preflight/organization-lifecycle.sh"

echo "✅ 租户数据库核心域、Worker Control 与企业生命周期只读前置检查通过"
