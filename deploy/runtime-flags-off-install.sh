#!/bin/bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

source deploy/deploy-helpers.sh

EXPECTED_SHA=
runtime_mode=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --runtime-flags-off-install)
            if [ "$runtime_mode" = true ]; then
                log_error "--runtime-flags-off-install 不能重复"
                exit 2
            fi
            runtime_mode=true
            shift
            ;;
        --expected-sha)
            if [ $# -lt 2 ] || [ -n "$EXPECTED_SHA" ]; then
                log_error "--expected-sha 缺少值或重复"
                exit 2
            fi
            EXPECTED_SHA=$2
            shift 2
            ;;
        *)
            log_error "--runtime-flags-off-install 不能与其他部署模式组合: $1"
            exit 2
            ;;
    esac
done
if [ "$runtime_mode" != true ]; then
    log_error "缺少 --runtime-flags-off-install"
    exit 2
fi

check_release_source
if [ ! -f deploy/config.env ]; then
    log_error "配置文件 deploy/config.env 不存在"
    exit 1
fi
source deploy/config.env
for required_name in SERVER_HOST SERVER_USER SERVER_PORT REMOTE_APP_DIR \
    REMOTE_BACKEND_DIR; do
    if [ -z "${!required_name:-}" ]; then
        log_error "deploy/config.env 缺少 ${required_name}"
        exit 1
    fi
done
if [ "$SERVER_HOST" = your_server_ip_or_domain ]; then
    log_error "请在 deploy/config.env 中配置 SERVER_HOST"
    exit 1
fi
for command_name in ssh rsync; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        log_error "缺少必要工具: ${command_name}"
        exit 1
    fi
done

remote_exec() {
    ssh -p "$SERVER_PORT" "$SERVER_USER@$SERVER_HOST" "$@"
}

log_info "确认 Agent Runtime 服务保持 inactive + disabled..."
remote_exec bash << 'ENDSSH'
    set -euo pipefail
    services=(
        everydayai-agent-runtime
        everydayai-agent-projection
        everydayai-agent-authorization
        everydayai-sandbox-worker
    )
    for service in "${services[@]}"; do
        active_state=$(systemctl is-active "$service" 2>/dev/null || true)
        enabled_state=$(systemctl is-enabled "$service" 2>/dev/null || true)
        if [ "$active_state" != inactive ] || [ "$enabled_state" != disabled ]; then
            echo "❌ ${service} 必须为 inactive + disabled，实际为 ${active_state:-unknown} + ${enabled_state:-unknown}" >&2
            exit 1
        fi
    done
    echo "✅ 四个 Agent Runtime 服务均为 inactive + disabled"
ENDSSH

install_files=(
    deploy/validate-tenant-db-env.sh
    deploy/install-service-units.sh
    deploy/everydayai-agent-runtime.service
    deploy/everydayai-agent-projection.service
    deploy/everydayai-agent-authorization.service
    deploy/everydayai-sandbox-worker.service
    deploy/sandbox-worker-cgroup-wrapper.sh
    deploy/env-templates/runtime.env.template
    deploy/env-templates/wecom-runtime.env.template
    deploy/env-templates/agent-runtime-worker.env.template
    deploy/env-templates/agent-projection-worker.env.template
    deploy/env-templates/agent-authorization-worker.env.template
    deploy/env-templates/sandbox-worker.env.template
)
log_info "同步 Agent Runtime flags-off 安装文件..."
rsync -avz --relative -e "ssh -p ${SERVER_PORT}" \
    "${install_files[@]}" \
    "${SERVER_USER}@${SERVER_HOST}:${REMOTE_APP_DIR}/"

log_info "安装 Agent Runtime flags-off Systemd 单元..."
remote_exec \
    "EXPECTED_RELEASE_SHA=${EXPECTED_SHA} bash ${REMOTE_APP_DIR}/deploy/install-service-units.sh ${REMOTE_BACKEND_DIR} agent-runtime-only"
log_success "Agent Runtime flags-off 安装完成；未迁移、启停、enable 或切换 Owner"
