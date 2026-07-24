#!/bin/bash

set -euo pipefail

kek_file=${1:-/var/www/everydayai/backend/.env.kek}

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

if [ ! -f "$kek_file" ]; then
    echo "❌ 缺少 KEK 环境文件：${kek_file}" >&2
    exit 1
fi
if [ "$(file_mode "$kek_file")" != "600" ]; then
    echo "❌ ${kek_file} 权限必须为 0600" >&2
    exit 1
fi

values=$(grep -Ev '^[[:space:]]*(#|$)' "$kek_file" || true)
if [ "$(printf '%s\n' "$values" | wc -l | tr -d ' ')" -ne 2 ]; then
    echo "❌ ${kek_file} 必须且只能包含两项 KEK 配置" >&2
    exit 1
fi

current_version=$(printf '%s\n' "$values" |
    sed -n 's/^CONFIG_KEK_CURRENT_VERSION=//p')
keyring_json_raw=$(printf '%s\n' "$values" |
    sed -n 's/^CONFIG_KEK_KEYRING_JSON=//p')

if [[ ! "$current_version" =~ ^[A-Za-z0-9._-]{1,64}$ ]]; then
    echo "❌ CONFIG_KEK_CURRENT_VERSION 无效" >&2
    exit 1
fi
if [[ "$keyring_json_raw" != \'*\' ]]; then
    echo "❌ CONFIG_KEK_KEYRING_JSON 必须使用单引号包裹" >&2
    exit 1
fi
keyring_json=${keyring_json_raw:1:${#keyring_json_raw}-2}
if [ -z "$keyring_json" ] \
    || [[ "$keyring_json" == *"<"* ]] \
    || [[ "$keyring_json" == *">"* ]] \
    || [[ "$keyring_json" != \{*\} ]]; then
    echo "❌ CONFIG_KEK_KEYRING_JSON 为空、含占位符或格式无效" >&2
    exit 1
fi

echo "✅ KEK 环境文件权限与结构合同验证通过"
