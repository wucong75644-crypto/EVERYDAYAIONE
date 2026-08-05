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

check_remote_unit_states() {
    local phase=$1
    remote_exec bash -s -- "$phase" \
        < deploy/check-agent-runtime-unit-states.sh
}

log_info "确认 Agent Runtime unit 安装前状态安全..."
check_remote_unit_states pre-install

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
log_info "确认 Agent Runtime unit 安装后仍为 inactive + disabled..."
check_remote_unit_states post-install
log_success "Agent Runtime flags-off 安装完成；未迁移、启停、enable 或切换 Owner"
