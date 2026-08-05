#!/bin/bash

set -euo pipefail

deploy_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
backend_dir=${1:-/var/www/everydayai/backend}
install_mode=${2:-all}
expected_release_revision=${3:-${EXPECTED_RELEASE_SHA:-}}
expected_unit_manifest=${4:-${EXPECTED_UNIT_MANIFEST:-}}
systemd_dir=${SYSTEMD_UNIT_DIR:-/etc/systemd/system}
runtime_env_dir=${AGENT_RUNTIME_ENV_DIR:-/etc/everydayai}
libexec_dir=${LIBEXEC_DIR:-/usr/local/libexec}

runtime_services=(
    everydayai-agent-runtime
    everydayai-agent-projection
    everydayai-agent-authorization
    everydayai-sandbox-worker
)

manifest_temp_to_remove=
cleanup_manifest_temp() {
    if [ -n "$manifest_temp_to_remove" ]; then
        rm -f "$manifest_temp_to_remove"
    fi
}

file_mode() {
    local path=$1
    local mode
    mode=$(stat -c '%a' "$path" 2>/dev/null || true)
    if [[ "$mode" =~ ^[0-7]{3,4}$ ]]; then
        printf '%s' "$mode"
        return 0
    fi
    mode=$(stat -f '%Lp' "$path" 2>/dev/null || true)
    if [[ "$mode" =~ ^[0-7]{3,4}$ ]]; then
        printf '%s' "$mode"
        return 0
    fi
    echo "❌ 无法读取 ${path} 的文件权限" >&2
    return 1
}

contains_key() {
    local candidate=$1
    shift
    local item
    for item in "$@"; do
        if [ "$candidate" = "$item" ]; then
            return 0
        fi
    done
    return 1
}

read_env_value() {
    local path=$1
    local expected_key=$2
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ "$line" == "${expected_key}="* ]]; then
            printf '%s' "${line#*=}"
            return 0
        fi
    done < "$path"
    return 1
}

validate_exact_env_file() {
    local path=$1
    shift
    local allowed_keys=("$@")
    local seen_keys='|'
    local seen_count=0
    local line key value

    if [ ! -f "$path" ]; then
        echo "❌ 缺少 Agent Runtime 环境文件: ${path}" >&2
        return 1
    fi
    if [ "$(file_mode "$path")" != 640 ]; then
        echo "❌ ${path} 权限必须为 0640" >&2
        return 1
    fi
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ "$line" =~ ^[[:space:]]*(#|$) ]]; then
            continue
        fi
        if [[ ! "$line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]]; then
            echo "❌ ${path} 包含无效配置行" >&2
            return 1
        fi
        key=${BASH_REMATCH[1]}
        value=${BASH_REMATCH[2]}
        if ! contains_key "$key" "${allowed_keys[@]}"; then
            echo "❌ ${path} 包含未知配置键: ${key}" >&2
            return 1
        fi
        if [[ "$seen_keys" == *"|${key}|"* ]]; then
            echo "❌ ${path} 包含重复配置键: ${key}" >&2
            return 1
        fi
        seen_keys="${seen_keys}${key}|"
        seen_count=$((seen_count + 1))
        if [[ "$value" == *"<"* || "$value" == *">"* ]]; then
            echo "❌ ${path} 的 ${key} 包含模板占位符" >&2
            return 1
        fi
        if [ -z "$value" ] \
            && [ "$key" != REDIS_PASSWORD ] \
            && [ "$key" != SENTRY_DSN ]; then
            echo "❌ ${path} 的 ${key} 不能为空" >&2
            return 1
        fi
    done < "$path"
    if [ "$seen_count" -ne "${#allowed_keys[@]}" ]; then
        echo "❌ ${path} 缺少必需配置键" >&2
        return 1
    fi
}

require_env_value() {
    local path=$1
    local key=$2
    local expected=$3
    local actual
    actual=$(read_env_value "$path" "$key") || {
        echo "❌ ${path} 缺少 ${key}" >&2
        return 1
    }
    if [ "$actual" != "$expected" ]; then
        echo "❌ ${path} 的 ${key} 不符合 flags-off 安装合同" >&2
        return 1
    fi
}

require_database_role() {
    local path=$1
    local role=$2
    local value
    value=$(read_env_value "$path" WORKER_DATABASE_URL) || {
        echo "❌ ${path} 缺少 WORKER_DATABASE_URL" >&2
        return 1
    }
    case "$value" in
        "postgresql://${role}:"?*"@"?*|"postgres://${role}:"?*"@"?*) ;;
        *)
            echo "❌ ${path} 必须使用 ${role} 数据库角色" >&2
            return 1
            ;;
    esac
}

validate_runtime_worker_envs() {
    if [[ ! "$expected_release_revision" =~ ^[0-9a-f]{40}$ ]]; then
        echo "❌ agent-runtime-only 需要 40 位 EXPECTED_RELEASE_SHA" >&2
        return 1
    fi

    local runtime_path="${runtime_env_dir}/agent-runtime-worker.env"
    local projection_path="${runtime_env_dir}/agent-projection-worker.env"
    local authorization_path="${runtime_env_dir}/agent-authorization-worker.env"
    local sandbox_path="${runtime_env_dir}/sandbox-worker.env"

    validate_exact_env_file "$runtime_path" \
        WORKER_DATABASE_URL AGENT_RUNTIME_PROCESS_ROLE \
        AGENT_RUNTIME_WORKER_ID AGENT_RUNTIME_RELEASE_REVISION \
        AGENT_RUNTIME_HEALTH_SOCKET \
        AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED SANDBOX_JOB_ROOT \
        SANDBOX_RUNTIME_REVISION
    validate_exact_env_file "$projection_path" \
        WORKER_DATABASE_URL REDIS_HOST REDIS_PORT REDIS_PASSWORD REDIS_DB \
        REDIS_SSL AGENT_RUNTIME_PROCESS_ROLE AGENT_RUNTIME_WORKER_ID \
        AGENT_RUNTIME_RELEASE_REVISION AGENT_RUNTIME_HEALTH_SOCKET \
        AGENT_RUNTIME_POLL_INTERVAL_SECONDS AGENT_RUNTIME_HEARTBEAT_SECONDS \
        SENTRY_DSN ENVIRONMENT
    validate_exact_env_file "$authorization_path" \
        WORKER_DATABASE_URL AGENT_RUNTIME_PROCESS_ROLE \
        AGENT_RUNTIME_WORKER_ID AGENT_RUNTIME_RELEASE_REVISION \
        AGENT_RUNTIME_HEALTH_SOCKET AGENT_RUNTIME_POLL_INTERVAL_SECONDS \
        AGENT_RUNTIME_HEARTBEAT_SECONDS SENTRY_DSN ENVIRONMENT
    validate_exact_env_file "$sandbox_path" \
        WORKER_DATABASE_URL AGENT_RUNTIME_PROCESS_ROLE \
        AGENT_RUNTIME_WORKER_ID AGENT_RUNTIME_RELEASE_REVISION \
        AGENT_RUNTIME_HEALTH_SOCKET SANDBOX_JOB_ROOT SANDBOX_WORKER_ID \
        SANDBOX_RUNTIME_REVISION SANDBOX_ROOTFS SANDBOX_ROOTFS_MANIFEST \
        SANDBOX_ROOTFS_SHA256 SANDBOX_NSJAIL_PATH SANDBOX_NSJAIL_SHA256 \
        SANDBOX_PYTHON_PATH SANDBOX_SECCOMP_POLICY SANDBOX_SECCOMP_SHA256 \
        SANDBOX_CGROUP_V2_MOUNT SANDBOX_CGROUP_V2_RUNNER \
        SANDBOX_WORKER_CONCURRENCY SANDBOX_PARTIAL_RETENTION_SECONDS SENTRY_DSN

    require_database_role "$runtime_path" everydayai_agent_runtime_worker
    require_database_role "$projection_path" everydayai_projection_worker
    require_database_role "$authorization_path" everydayai_authorization_worker
    require_database_role "$sandbox_path" everydayai_sandbox_worker
    require_env_value "$runtime_path" AGENT_RUNTIME_PROCESS_ROLE agent_runtime
    require_env_value "$runtime_path" \
        AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED false
    require_env_value "$projection_path" AGENT_RUNTIME_PROCESS_ROLE projection
    require_env_value "$authorization_path" AGENT_RUNTIME_PROCESS_ROLE authorization
    require_env_value "$sandbox_path" AGENT_RUNTIME_PROCESS_ROLE sandbox

    local path
    for path in "$runtime_path" "$projection_path" "$authorization_path" \
        "$sandbox_path"; do
        require_env_value "$path" AGENT_RUNTIME_RELEASE_REVISION \
            "$expected_release_revision"
    done
}

validate_control_plane_worker_envs() {
    if [[ ! "$expected_release_revision" =~ ^[0-9a-f]{40}$ ]]; then
        echo "❌ control-plane-only 需要 40 位 EXPECTED_RELEASE_SHA" >&2
        return 1
    fi

    local runtime_path="${runtime_env_dir}/agent-runtime-worker.env"
    local projection_path="${runtime_env_dir}/agent-projection-worker.env"
    local authorization_path="${runtime_env_dir}/agent-authorization-worker.env"

    validate_exact_env_file "$runtime_path" \
        WORKER_DATABASE_URL AGENT_RUNTIME_PROCESS_ROLE \
        AGENT_RUNTIME_WORKER_ID AGENT_RUNTIME_RELEASE_REVISION \
        AGENT_RUNTIME_HEALTH_SOCKET \
        AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED SANDBOX_JOB_ROOT \
        SANDBOX_RUNTIME_REVISION
    validate_exact_env_file "$projection_path" \
        WORKER_DATABASE_URL REDIS_HOST REDIS_PORT REDIS_PASSWORD REDIS_DB \
        REDIS_SSL AGENT_RUNTIME_PROCESS_ROLE AGENT_RUNTIME_WORKER_ID \
        AGENT_RUNTIME_RELEASE_REVISION AGENT_RUNTIME_HEALTH_SOCKET \
        AGENT_RUNTIME_POLL_INTERVAL_SECONDS AGENT_RUNTIME_HEARTBEAT_SECONDS \
        SENTRY_DSN ENVIRONMENT
    validate_exact_env_file "$authorization_path" \
        WORKER_DATABASE_URL AGENT_RUNTIME_PROCESS_ROLE \
        AGENT_RUNTIME_WORKER_ID AGENT_RUNTIME_RELEASE_REVISION \
        AGENT_RUNTIME_HEALTH_SOCKET AGENT_RUNTIME_POLL_INTERVAL_SECONDS \
        AGENT_RUNTIME_HEARTBEAT_SECONDS SENTRY_DSN ENVIRONMENT

    require_database_role "$runtime_path" everydayai_agent_runtime_worker
    require_database_role "$projection_path" everydayai_projection_worker
    require_database_role "$authorization_path" everydayai_authorization_worker
    require_env_value "$runtime_path" AGENT_RUNTIME_PROCESS_ROLE agent_runtime
    require_env_value "$runtime_path" \
        AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED false
    require_env_value "$projection_path" AGENT_RUNTIME_PROCESS_ROLE projection
    require_env_value "$authorization_path" AGENT_RUNTIME_PROCESS_ROLE authorization

    local path
    for path in "$runtime_path" "$projection_path" "$authorization_path"; do
        require_env_value "$path" AGENT_RUNTIME_RELEASE_REVISION \
            "$expected_release_revision"
    done
}

fail_on_different_target() {
    local source=$1
    local target=$2
    if [ -e "$target" ] && ! cmp --silent "$source" "$target"; then
        echo "❌ 已有安装目标与发布内容不同，拒绝覆盖: ${target}" >&2
        return 1
    fi
}

install_runtime_units_only() {
    bash "${deploy_dir}/validate-tenant-db-env.sh" \
        "${backend_dir}" --runtime-flags-off-v3
    validate_runtime_worker_envs

    local service source_unit target_unit
    for service in "${runtime_services[@]}"; do
        source_unit="${deploy_dir}/${service}.service"
        target_unit="${systemd_dir}/${service}.service"
        test -f "$source_unit"
        fail_on_different_target "$source_unit" "$target_unit"
    done
    local wrapper="${deploy_dir}/sandbox-worker-cgroup-wrapper.sh"
    local wrapper_target="${libexec_dir}/everydayai-sandbox-worker-cgroup-wrapper"
    test -x "$wrapper"
    fail_on_different_target "$wrapper" "$wrapper_target"

    for service in "${runtime_services[@]}"; do
        source_unit="${deploy_dir}/${service}.service"
        target_unit="${systemd_dir}/${service}.service"
        if [ ! -e "$target_unit" ]; then
            sudo install -m 0644 "$source_unit" "$target_unit"
        fi
        cmp --silent "$source_unit" "$target_unit"
    done
    if [ ! -e "$wrapper_target" ]; then
        sudo install -d -m 0755 "$(dirname "$wrapper_target")"
        sudo install -m 0755 "$wrapper" "$wrapper_target"
    fi
    cmp --silent "$wrapper" "$wrapper_target"
    sudo systemctl daemon-reload
    echo "✅ Agent Runtime flags-off 单元已安装并验证；未启停或启用服务"
}

install_control_plane_units_only() {
    bash "${deploy_dir}/validate-tenant-db-env.sh" \
        "${backend_dir}" --runtime-flags-off-v3
    if [ -z "$expected_unit_manifest" ]; then
        echo "❌ control-plane-only 需要 reviewed unit SHA-256 manifest" >&2
        return 1
    fi

    local manifest_path=$expected_unit_manifest
    if [ "$manifest_path" = - ]; then
        manifest_temp_to_remove=$(mktemp)
        chmod 0600 "$manifest_temp_to_remove"
        cat > "$manifest_temp_to_remove"
        manifest_path=$manifest_temp_to_remove
        trap cleanup_manifest_temp EXIT
    fi

    local provisioner="${deploy_dir}/provision-control-plane-worker-envs.py"
    local updater="${deploy_dir}/update-control-plane-units.sh"
    test -f "$provisioner"
    test -f "$updater"

    sudo python3 "$provisioner" \
        --backend-dir "$backend_dir" \
        --env-dir "$runtime_env_dir" \
        --release-sha "$expected_release_revision" \
        --check-only
    SYSTEMD_UNIT_DIR="$systemd_dir" \
        CONTROL_PLANE_DEPLOY_DIR="$deploy_dir" \
        bash "$updater" preflight "$expected_release_revision" "$manifest_path"

    sudo python3 "$provisioner" \
        --backend-dir "$backend_dir" \
        --env-dir "$runtime_env_dir" \
        --release-sha "$expected_release_revision"
    validate_control_plane_worker_envs

    SYSTEMD_UNIT_DIR="$systemd_dir" \
        CONTROL_PLANE_DEPLOY_DIR="$deploy_dir" \
        bash "$updater" apply "$expected_release_revision" "$manifest_path"
    cleanup_manifest_temp
    manifest_temp_to_remove=
    echo "✅ control-plane flags-off env/unit 已 provisioning/update；未启停或启用服务"
}

install_all_units() {
    bash "${deploy_dir}/validate-tenant-db-env.sh" "${backend_dir}"
    bash "${deploy_dir}/validate-kek-env.sh" "${backend_dir}/.env.kek"

    local services=(
        everydayai-backend
        everydayai-sync
        everydayai-wecom
        everydayai-conversation-actor
        "${runtime_services[@]}"
    )
    local service source_unit target_unit
    for service in "${services[@]}"; do
        source_unit="${deploy_dir}/${service}.service"
        target_unit="${systemd_dir}/${service}.service"
        if [ ! -f "$source_unit" ]; then
            echo "❌ 缺少仓库服务单元: ${source_unit}" >&2
            exit 1
        fi
        sudo install -m 0644 "$source_unit" "$target_unit"
        cmp --silent "$source_unit" "$target_unit" || {
            echo "❌ Systemd 服务单元安装不一致: ${service}" >&2
            exit 1
        }
    done

    local wrapper="${deploy_dir}/sandbox-worker-cgroup-wrapper.sh"
    local wrapper_target="${libexec_dir}/everydayai-sandbox-worker-cgroup-wrapper"
    test -x "$wrapper"
    sudo install -d -m 0755 "$(dirname "$wrapper_target")"
    sudo install -m 0755 "$wrapper" "$wrapper_target"
    cmp --silent "$wrapper" "$wrapper_target" || {
        echo "❌ Sandbox cgroup wrapper installation mismatch" >&2
        exit 1
    }
    sudo systemctl daemon-reload
    echo "✅ Systemd 服务单元已安装并验证"
}

case "$install_mode" in
    all)
        install_all_units
        ;;
    agent-runtime-only)
        install_runtime_units_only
        ;;
    control-plane-only)
        install_control_plane_units_only
        ;;
    *)
        echo "usage: $0 [backend-dir] [all|agent-runtime-only|control-plane-only] [release-sha] [reviewed-manifest]" >&2
        exit 2
        ;;
esac
