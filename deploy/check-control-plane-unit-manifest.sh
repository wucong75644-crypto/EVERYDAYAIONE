#!/bin/bash

set -euo pipefail

systemd_dir=${SYSTEMD_UNIT_DIR:-/etc/systemd/system}
services=(
    everydayai-agent-runtime
    everydayai-agent-projection
    everydayai-agent-authorization
)

sha256_file() {
    local path=$1
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$path" | awk '{print $1}'
    else
        shasum -a 256 "$path" | awk '{print $1}'
    fi
}

[ "$#" -eq 6 ] || {
    echo "❌ reviewed unit manifest 参数数量无效" >&2
    exit 2
}

for service in "${services[@]}"; do
    expected_hash=$1
    expected_name=$2
    shift 2
    if [[ ! "$expected_hash" =~ ^[0-9a-f]{64}$ ]] \
        || [ "$expected_name" != "${service}.service" ]; then
        echo "❌ reviewed unit manifest 参数无效" >&2
        exit 2
    fi
    target="${systemd_dir}/${service}.service"
    if [ ! -f "$target" ] || [ -L "$target" ]; then
        echo "❌ reviewed update 要求已存在普通 target unit: ${service}" >&2
        exit 1
    fi
    if [ "$(sha256_file "$target")" != "$expected_hash" ]; then
        echo "❌ target unit SHA-256 与 reviewed manifest 不匹配: ${service}" >&2
        exit 1
    fi
done

echo "✅ 三个 control-plane target unit reviewed SHA-256 预检通过"
