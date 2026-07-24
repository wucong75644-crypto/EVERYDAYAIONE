#!/bin/bash

set -euo pipefail

env_directory=${1:-/var/www/everydayai/backend}

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

read_contract_value() {
    local path=$1
    local expected_key=$2
    local lines
    lines=$(grep -Ev '^[[:space:]]*(#|$)' "$path" || true)
    if [ "$(printf '%s\n' "$lines" | wc -l | tr -d ' ')" -ne 1 ]; then
        echo "❌ ${path} 必须且只能包含一个非注释配置项" >&2
        return 1
    fi
    case "$lines" in
        "${expected_key}="*) ;;
        *)
            echo "❌ ${path} 必须配置 ${expected_key}" >&2
            return 1
            ;;
    esac
    local value=${lines#*=}
    if [ -z "$value" ] || [[ "$value" == *"<"* || "$value" == *">"* ]]; then
        echo "❌ ${path} 仍为空或包含模板占位符" >&2
        return 1
    fi
    printf '%s' "$value"
}

validate_role_file() {
    local filename=$1
    local key=$2
    local role=$3
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
    value=$(read_contract_value "$path" "$key")
    case "$value" in
        "postgresql://${role}:"?*"@"?*|"postgres://${role}:"?*"@"?*) ;;
        *)
            echo "❌ ${path} 必须使用 ${role} 数据库角色" >&2
            return 1
            ;;
    esac
    printf '%s' "$value"
}

runtime_url=$(validate_role_file ".env.runtime" "DATABASE_URL" "everydayai_runtime")
wecom_runtime_url=$(
    validate_role_file \
        ".env.wecom-runtime" \
        "DATABASE_URL" \
        "everydayai_wecom_runtime"
)
worker_url=$(validate_role_file ".env.worker" "DATABASE_URL" "everydayai_worker")
worker_client_url=$(
    validate_role_file \
        ".env.worker-client" \
        "WORKER_DATABASE_URL" \
        "everydayai_worker"
)
migrator_url=$(
    validate_role_file \
        ".env.migrator" \
        "MIGRATION_DATABASE_URL" \
        "everydayai_migrator"
)
sync_url=$(validate_role_file ".env.sync" "DATABASE_URL" "everydayai")

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

echo "✅ 数据库角色环境文件合同验证通过"
