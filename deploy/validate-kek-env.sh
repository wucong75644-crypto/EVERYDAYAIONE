#!/bin/bash

set -euo pipefail

kek_file=${1:-/var/www/everydayai/backend/.env.kek}

file_mode() {
    stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1"
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
keyring_json=$(printf '%s\n' "$values" |
    sed -n 's/^CONFIG_KEK_KEYRING_JSON=//p')

if [[ ! "$current_version" =~ ^[A-Za-z0-9._-]{1,64}$ ]]; then
    echo "❌ CONFIG_KEK_CURRENT_VERSION 无效" >&2
    exit 1
fi
if [ -z "$keyring_json" ] \
    || [[ "$keyring_json" == *"<"* ]] \
    || [[ "$keyring_json" == *">"* ]] \
    || [[ "$keyring_json" != \{*\} ]]; then
    echo "❌ CONFIG_KEK_KEYRING_JSON 为空、含占位符或格式无效" >&2
    exit 1
fi

echo "✅ KEK 环境文件权限与结构合同验证通过"
