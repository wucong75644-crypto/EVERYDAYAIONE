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
runtime_mode=
EXPECTED_UNIT_MANIFEST=
reviewed_names=()
reviewed_hashes=()
reviewed_count=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --runtime-flags-off-install)
            if [ -n "$runtime_mode" ]; then
                log_error "flags-off 模式不能重复或组合"
                exit 2
            fi
            runtime_mode=all
            shift
            ;;
        --runtime-control-plane-flags-off-update)
            if [ -n "$runtime_mode" ]; then
                log_error "flags-off 模式不能重复或组合"
                exit 2
            fi
            runtime_mode=control-plane
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
        --expected-unit-manifest)
            if [ $# -lt 2 ] || [ -n "$EXPECTED_UNIT_MANIFEST" ]; then
                log_error "--expected-unit-manifest 缺少值或重复"
                exit 2
            fi
            EXPECTED_UNIT_MANIFEST=$2
            shift 2
            ;;
        *)
            if [ "$runtime_mode" = all ]; then
                log_error "--runtime-flags-off-install 不能与其他部署模式组合: $1"
            else
                log_error "control-plane flags-off update 不能与其他部署模式组合: $1"
            fi
            exit 2
            ;;
    esac
done
if [ -z "$runtime_mode" ]; then
    log_error "缺少 flags-off 安装或更新模式"
    exit 2
fi
if [ "$runtime_mode" = control-plane ]; then
    if [ -z "$EXPECTED_UNIT_MANIFEST" ] || [ ! -f "$EXPECTED_UNIT_MANIFEST" ] \
        || [ -L "$EXPECTED_UNIT_MANIFEST" ]; then
        log_error "control-plane update 需要普通文件形式的 reviewed unit manifest"
        exit 2
    fi
elif [ -n "$EXPECTED_UNIT_MANIFEST" ]; then
    log_error "--expected-unit-manifest 仅用于 control-plane update"
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
    local scope=${2:-all}
    remote_exec bash -s -- "$phase" "$scope" \
        < deploy/check-agent-runtime-unit-states.sh
}

load_reviewed_manifest() {
    local hash name extra seen='|'
    while read -r hash name extra || [ -n "${hash:-}${name:-}${extra:-}" ]; do
        if [[ ! "$hash" =~ ^[0-9a-f]{64}$ ]] || [ -z "${name:-}" ] \
            || [ -n "${extra:-}" ] || [[ "$seen" == *"|${name}|"* ]]; then
            log_error "reviewed unit manifest 格式或条目无效"
            return 1
        fi
        case "$name" in
            everydayai-agent-runtime.service|everydayai-agent-model-gateway.service|everydayai-agent-projection.service|everydayai-agent-authorization.service) ;;
            *)
                log_error "reviewed unit manifest 包含非控制面条目"
                return 1
                ;;
        esac
        reviewed_hashes+=("$hash")
        reviewed_names+=("$name")
        reviewed_count=$((reviewed_count + 1))
        seen="${seen}${name}|"
    done < "$EXPECTED_UNIT_MANIFEST"
    [ "$reviewed_count" -eq 4 ] || {
        log_error "reviewed unit manifest 必须精确包含四个 unit"
        return 1
    }
}

reviewed_manifest_args() {
    local service index found
    for service in everydayai-agent-runtime everydayai-agent-model-gateway \
        everydayai-agent-projection \
        everydayai-agent-authorization; do
        found=false
        for index in "${!reviewed_names[@]}"; do
            if [ "${reviewed_names[$index]}" = "${service}.service" ]; then
                printf '%s\n' "${reviewed_hashes[$index]}" "${reviewed_names[$index]}"
                found=true
                break
            fi
        done
        [ "$found" = true ] || return 1
    done
}

write_reviewed_manifest() {
    local index
    for ((index = 0; index < ${#manifest_args[@]}; index += 2)); do
        printf '%s  %s\n' "${manifest_args[$index]}" \
            "${manifest_args[$((index + 1))]}"
    done
}

if [ "$runtime_mode" = control-plane ]; then
    load_reviewed_manifest
    manifest_args=()
    while IFS= read -r manifest_arg; do
        manifest_args+=("$manifest_arg")
    done < <(reviewed_manifest_args)
    log_info "确认四个 control-plane unit 更新前严格 inactive + disabled..."
    check_remote_unit_states pre-install control-plane
    log_info "在任何远端文件同步前核对 reviewed target unit SHA-256..."
    remote_exec bash -s -- "${manifest_args[@]}" \
        < deploy/check-control-plane-unit-manifest.sh

    install_files=(
        deploy/validate-tenant-db-env.sh
        deploy/install-service-units.sh
        deploy/check-control-plane-unit-manifest.sh
        deploy/provision-control-plane-worker-envs.py
        deploy/control_plane_env_source.py
        deploy/update-control-plane-units.sh
        deploy/everydayai-agent-runtime.service
        deploy/everydayai-agent-model-gateway.service
        deploy/everydayai-agent-projection.service
        deploy/everydayai-agent-authorization.service
    )
    log_info "同步 control-plane flags-off provisioning/update 工具..."
    rsync -avz --relative -e "ssh -p ${SERVER_PORT}" \
        "${install_files[@]}" \
        "${SERVER_USER}@${SERVER_HOST}:${REMOTE_APP_DIR}/"

    log_info "provision 五份安全 env 并 reviewed update 四个 control-plane unit..."
    write_reviewed_manifest | remote_exec \
        "EXPECTED_RELEASE_SHA=${EXPECTED_SHA} bash ${REMOTE_APP_DIR}/deploy/install-service-units.sh ${REMOTE_BACKEND_DIR} control-plane-only ${EXPECTED_SHA} -"
    log_info "确认四个 control-plane unit 更新后仍严格 inactive + disabled..."
    if ! check_remote_unit_states post-install control-plane; then
        log_error "control-plane 外层 postcheck 失败，恢复 release 绑定 env/unit transaction"
        remote_exec \
            "sudo bash ${REMOTE_APP_DIR}/deploy/update-control-plane-units.sh rollback ${EXPECTED_SHA}" \
            || log_error "control-plane 外层 postcheck 后自动恢复失败"
        exit 1
    fi
    log_success "control-plane flags-off provisioning/update 完成；未触碰 Sandbox、迁移、服务启停、enable 或 Owner"
    exit 0
fi

log_info "确认 Agent Runtime unit 安装前状态安全..."
check_remote_unit_states pre-install all

install_files=(
    deploy/validate-tenant-db-env.sh
    deploy/install-service-units.sh
    deploy/everydayai-agent-runtime.service
    deploy/everydayai-agent-model-gateway.service
    deploy/everydayai-agent-projection.service
    deploy/everydayai-agent-authorization.service
    deploy/everydayai-sandbox-worker.service
    deploy/sandbox-worker-cgroup-wrapper.sh
    deploy/env-templates/runtime.env.template
    deploy/env-templates/wecom-runtime.env.template
    deploy/env-templates/agent-runtime-worker.env.template
    deploy/env-templates/agent-model-gateway.env.template
    deploy/env-templates/agent-model-gateway-kek.env.template
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
check_remote_unit_states post-install all
log_success "Agent Runtime flags-off 安装完成；未迁移、启停、enable 或切换 Owner"
