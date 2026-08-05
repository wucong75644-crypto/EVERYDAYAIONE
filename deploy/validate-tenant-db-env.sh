#!/bin/bash

set -euo pipefail

env_directory=/var/www/everydayai/backend
env_directory_set=false
validation_mode=standard
for argument in "$@"; do
    case "$argument" in
        --runtime-flags-off-v3)
            validation_mode=runtime-flags-off-v3
            ;;
        -*)
            echo "usage: $0 [env-directory] [--runtime-flags-off-v3]" >&2
            exit 2
            ;;
        *)
            if [ "$env_directory_set" = true ]; then
                echo "usage: $0 [env-directory] [--runtime-flags-off-v3]" >&2
                exit 2
            fi
            env_directory=$argument
            env_directory_set=true
            ;;
    esac
done

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

key_is_allowed() {
    local candidate=$1
    shift
    local allowed
    for allowed in "$@"; do
        if [ "$candidate" = "$allowed" ]; then
            return 0
        fi
    done
    return 1
}

read_contract_value() {
    local path=$1
    local expected_key=$2
    shift 2
    local allowed_keys=("$@")
    local line key value
    local expected_count=0
    local expected_value=
    local seen_keys='|'

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
        if ! key_is_allowed "$key" "${allowed_keys[@]}"; then
            echo "❌ ${path} 包含未知配置键: ${key}" >&2
            return 1
        fi
        if [[ "$seen_keys" == *"|${key}|"* ]]; then
            echo "❌ ${path} 包含重复配置键: ${key}" >&2
            return 1
        fi
        seen_keys="${seen_keys}${key}|"
        if [ -z "$value" ] || [[ "$value" == *"<"* || "$value" == *">"* ]]; then
            echo "❌ ${path} 的 ${key} 为空或包含模板占位符" >&2
            return 1
        fi
        if [ "$key" = "$expected_key" ]; then
            expected_count=$((expected_count + 1))
            expected_value=$value
        fi
    done < "$path"

    if [ "$expected_count" -ne 1 ]; then
        echo "❌ ${path} 必须恰好配置一次 ${expected_key}" >&2
        return 1
    fi
    printf '%s' "$expected_value"
}

read_required_value() {
    local path=$1
    local expected_key=$2
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ "$line" == "${expected_key}="* ]]; then
            printf '%s' "${line#*=}"
            return 0
        fi
    done < "$path"
    echo "❌ ${path} 缺少 ${expected_key}" >&2
    return 1
}

require_exact_value() {
    local path=$1
    local key=$2
    local expected=$3
    local value
    value=$(read_required_value "$path" "$key")
    if [ "$value" != "$expected" ]; then
        echo "❌ ${path} 的 ${key} 必须为 ${expected}" >&2
        return 1
    fi
}

validate_role_file() {
    local filename=$1
    local key=$2
    local role=$3
    shift 3
    local allowed_keys=("$@")
    local path="${env_directory}/${filename}"
    if [ ! -f "$path" ]; then
        echo "❌ 缺少角色环境文件：${path}" >&2
        return 1
    fi
    if [ "$(file_mode "$path")" != "600" ]; then
        echo "❌ ${path} 权限必须为 0600" >&2
        return 1
    fi
    local value
    value=$(read_contract_value "$path" "$key" "${allowed_keys[@]}")
    case "$value" in
        "postgresql://${role}:"?*"@"?*|"postgres://${role}:"?*"@"?*) ;;
        *)
            echo "❌ ${path} 必须使用 ${role} 数据库角色" >&2
            return 1
            ;;
    esac
    printf '%s' "$value"
}

runtime_keys=(
    DATABASE_URL
    AGENT_RUNTIME_INGRESS_ENABLED
    TOOL_CONFIRMATION_V3_ENABLED
    AGENT_RUNTIME_AGENT_DEFINITION_ID
    AGENT_RUNTIME_AGENT_DEFINITION_REVISION
)
wecom_runtime_keys=(
    DATABASE_URL
    AGENT_RUNTIME_INGRESS_ENABLED
    AGENT_RUNTIME_AGENT_DEFINITION_ID
    AGENT_RUNTIME_AGENT_DEFINITION_REVISION
)

runtime_url=$(validate_role_file \
    ".env.runtime" "DATABASE_URL" "everydayai_runtime" "${runtime_keys[@]}")
wecom_runtime_url=$(validate_role_file \
    ".env.wecom-runtime" "DATABASE_URL" "everydayai_wecom_runtime" \
    "${wecom_runtime_keys[@]}")
worker_url=$(validate_role_file \
    ".env.worker" "DATABASE_URL" "everydayai_worker" "DATABASE_URL")
worker_client_url=$(validate_role_file \
    ".env.worker-client" "WORKER_DATABASE_URL" "everydayai_worker" \
    "WORKER_DATABASE_URL")
migrator_url=$(validate_role_file \
    ".env.migrator" "MIGRATION_DATABASE_URL" "everydayai_migrator" \
    "MIGRATION_DATABASE_URL")
sync_url=$(validate_role_file \
    ".env.sync" "DATABASE_URL" "everydayai_sync" "DATABASE_URL")

isolated_urls=(
    "$runtime_url"
    "$wecom_runtime_url"
    "$worker_url"
    "$migrator_url"
    "$sync_url"
)
for left_index in "${!isolated_urls[@]}"; do
    for right_index in "${!isolated_urls[@]}"; do
        if [ "$left_index" -lt "$right_index" ] \
            && [ "${isolated_urls[$left_index]}" = "${isolated_urls[$right_index]}" ]; then
            echo "❌ runtime、wecom-runtime、worker、migrator、sync 连接串必须相互独立" >&2
            exit 1
        fi
    done
done
if [ "$worker_client_url" != "$worker_url" ]; then
    echo "❌ worker 与 worker-client 必须指向同一 Worker 连接" >&2
    exit 1
fi

if [ "$validation_mode" = runtime-flags-off-v3 ]; then
    runtime_path="${env_directory}/.env.runtime"
    wecom_runtime_path="${env_directory}/.env.wecom-runtime"
    require_exact_value "$runtime_path" AGENT_RUNTIME_INGRESS_ENABLED false
    require_exact_value "$runtime_path" TOOL_CONFIRMATION_V3_ENABLED false
    require_exact_value "$runtime_path" \
        AGENT_RUNTIME_AGENT_DEFINITION_ID everydayai-default
    require_exact_value "$runtime_path" AGENT_RUNTIME_AGENT_DEFINITION_REVISION v3
    require_exact_value "$wecom_runtime_path" AGENT_RUNTIME_INGRESS_ENABLED false
    require_exact_value "$wecom_runtime_path" \
        AGENT_RUNTIME_AGENT_DEFINITION_ID everydayai-default
    require_exact_value "$wecom_runtime_path" \
        AGENT_RUNTIME_AGENT_DEFINITION_REVISION v3
    echo "✅ Runtime flags-off v3 环境合同验证通过"
else
    echo "✅ 数据库角色环境文件合同验证通过"
fi
