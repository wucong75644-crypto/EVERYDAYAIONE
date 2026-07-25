#!/bin/bash

set -euo pipefail

deploy_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
backend_dir=${1:-/var/www/everydayai/backend}
systemd_dir=${SYSTEMD_UNIT_DIR:-/etc/systemd/system}

bash "${deploy_dir}/validate-tenant-db-env.sh" "${backend_dir}"
bash "${deploy_dir}/validate-kek-env.sh" "${backend_dir}/.env.kek"

services=(
    everydayai-backend
    everydayai-sync
    everydayai-wecom
    everydayai-conversation-actor
)
for service in "${services[@]}"; do
    source_unit="${deploy_dir}/${service}.service"
    target_unit="${systemd_dir}/${service}.service"
    if [ ! -f "${source_unit}" ]; then
        echo "❌ 缺少仓库服务单元: ${source_unit}" >&2
        exit 1
    fi
    sudo install -m 0644 "${source_unit}" "${target_unit}"
    cmp --silent "${source_unit}" "${target_unit}" || {
        echo "❌ Systemd 服务单元安装不一致: ${service}" >&2
        exit 1
    }
done

sudo systemctl daemon-reload
echo "✅ Systemd 服务单元已安装并验证"
