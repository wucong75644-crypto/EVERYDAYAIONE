#!/bin/bash

set -euo pipefail

phase=${1:-}
case "$phase" in
    pre-install|post-install) ;;
    *)
        echo "usage: $0 pre-install|post-install" >&2
        exit 2
        ;;
esac

services=(
    everydayai-agent-runtime
    everydayai-agent-projection
    everydayai-agent-authorization
    everydayai-sandbox-worker
)
for service in "${services[@]}"; do
    active_state=$(systemctl is-active "$service" 2>/dev/null || true)
    enabled_state=$(systemctl is-enabled "$service" 2>/dev/null || true)
    state_pair="${active_state:-unknown}:${enabled_state:-unknown}"
    if [ "$phase" = pre-install ]; then
        case "$state_pair" in
            inactive:disabled|inactive:not-found) ;;
            *)
                echo "❌ ${service} 安装前状态不安全: ${state_pair}" >&2
                exit 1
                ;;
        esac
    elif [ "$state_pair" != inactive:disabled ]; then
        echo "❌ ${service} 安装后必须为 inactive:disabled，实际为 ${state_pair}" >&2
        exit 1
    fi
done

echo "✅ Agent Runtime unit ${phase} 状态合同通过"
