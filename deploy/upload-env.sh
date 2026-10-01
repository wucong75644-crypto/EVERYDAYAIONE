#!/usr/bin/env bash
# Only explicitly named keys are patched into the live environment.
set -euo pipefail
umask 077

declare -a keys=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --key)
            [[ $# -ge 2 ]] || { echo '--key 缺少键名' >&2; exit 1; }
            keys+=(--key "$2"); shift 2 ;;
        -h|--help)
            echo '用法：deploy/upload-env.sh --key WEB_SEARCH_PROVIDER --key WEB_SEARCH_ARK_API_KEY'
            echo '只更新选中的键，保留生产其他配置；数据库连接须单独核验和恢复。'
            exit 0 ;;
        *) echo '必须用 --key 明确选择配置键；不再支持整份覆盖' >&2; exit 1 ;;
    esac
done
[[ ${#keys[@]} -gt 0 ]] || { echo '必须用 --key 明确选择配置键；不再支持整份覆盖' >&2; exit 1; }

primary=$(git worktree list --porcelain | awk '/^worktree / { sub(/^worktree /, ""); print; exit }')
python_bin=${EVERYDAYAI_ENV_PYTHON_BIN:-backend/venv/bin/python}
[[ -x "$python_bin" ]] || python_bin="$primary/backend/venv/bin/python"
[[ -x "$python_bin" ]] || { echo '缺少项目 Python 环境' >&2; exit 1; }
payload=$(mktemp)
chmod 600 "$payload"
cleanup() { rm -f -- "$payload"; }
trap cleanup EXIT
"$python_bin" deploy/env-patch.py --build-from deploy/.env.production "${keys[@]}" > "$payload"

source deploy/release-coordination.sh
acquire_release_lock deploy/config.env
cleanup() {
    rm -f -- "$payload"
    if [[ "${config_update_unconfirmed:-false}" == true || "${release_state_write_unconfirmed:-false}" == true ]]; then
        echo '生产配置操作结果不确定，保留发布锁，须核验远端状态' >&2
    else
        release_owned_lock
    fi
}
release_remote_state invalidate
config_update_unconfirmed=true
ssh -p "$SERVER_PORT" -o ConnectTimeout=10 -o BatchMode=yes "$SERVER_USER@$SERVER_HOST" \
    "/var/www/everydayai/backend/venv/bin/python /var/www/everydayai/deploy/env-patch.py --lock-token '$release_lock_token'" < "$payload"
config_update_unconfirmed=false
echo '已更新所选环境变量并备份。使用 deploy/release.sh 受控部署重启并复验。'
