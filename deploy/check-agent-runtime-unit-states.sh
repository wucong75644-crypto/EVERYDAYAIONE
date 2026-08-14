#!/bin/bash

set -euo pipefail

phase=${1:-}
scope=${2:-all}
case "$phase" in
    pre-install|post-install) ;;
    *)
        echo "usage: $0 pre-install|post-install" >&2
        exit 2
        ;;
esac

case "$scope" in
    all)
        services=(
            everydayai-agent-runtime
            everydayai-agent-projection
            everydayai-agent-authorization
            everydayai-sandbox-worker
        )
        ;;
    control-plane)
        services=(
            everydayai-agent-runtime
            everydayai-agent-projection
            everydayai-agent-authorization
        )
        ;;
    *)
        echo "usage: $0 pre-install|post-install [all|control-plane]" >&2
        exit 2
        ;;
esac

legacy_service=everydayai-agent-model-gateway
legacy_load_state=$(systemctl show "$legacy_service" \
    -p LoadState --value 2>/dev/null || true)
if [ "${legacy_load_state:-unknown}" != not-found ]; then
    echo "❌ legacy Model Gateway 必须不存在，实际 LoadState 为 ${legacy_load_state:-unknown}" >&2
    exit 1
fi

for service in "${services[@]}"; do
    active_state=$(systemctl is-active "$service" 2>/dev/null || true)
    enabled_state=$(systemctl is-enabled "$service" 2>/dev/null || true)
    state_pair="${active_state:-unknown}:${enabled_state:-unknown}"
    if [ "$phase" = pre-install ]; then
        if [ "$scope" = control-plane ] && [ "$state_pair" != inactive:disabled ]; then
            echo "❌ ${service} 更新前必须为 inactive:disabled，实际为 ${state_pair}" >&2
            exit 1
        elif [ "$scope" = all ]; then
            case "$state_pair" in
                inactive:disabled|inactive:not-found) ;;
                *)
                    echo "❌ ${service} 安装前状态不安全: ${state_pair}" >&2
                    exit 1
                    ;;
            esac
        fi
    elif [ "$state_pair" != inactive:disabled ]; then
        echo "❌ ${service} 安装后必须为 inactive:disabled，实际为 ${state_pair}" >&2
        exit 1
    fi
done

echo "✅ Agent Runtime ${scope} unit ${phase} 状态合同通过"
