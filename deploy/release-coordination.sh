#!/usr/bin/env bash

# Shared by the controlled entry point and its current deployment executor.
# The remote directory lock never expires automatically: an uncertain owner
# must be investigated before recovery, rather than allowing overlapping writes.

release_lock_token=''
release_lock_owned=false
release_state_write_unconfirmed=false

load_release_coordination_config() {
    local config_file=$1
    [[ -f "$config_file" ]] || { echo "缺少发布协调配置：$config_file" >&2; return 1; }
    # shellcheck disable=SC1090
    source "$config_file"
    [[ -n "${SERVER_HOST:-}" && -n "${SERVER_USER:-}" && -n "${SERVER_PORT:-}" && -n "${REMOTE_APP_DIR:-}" ]] \
        || { echo '发布配置缺少服务器或应用目录' >&2; return 1; }
    REMOTE_APP_DIR=${REMOTE_APP_DIR%/}
    [[ "$REMOTE_APP_DIR" == /* && "$REMOTE_APP_DIR" != / && -n "$REMOTE_APP_DIR" ]] \
        || { echo 'REMOTE_APP_DIR 必须是独立应用的绝对目录' >&2; return 1; }
}

release_remote_state() {
    local action=$1
    shift
    local command='bash -s --' value quoted
    for value in "$REMOTE_APP_DIR" "$action" "$release_lock_token" "$@"; do
        printf -v quoted '%q' "$value"
        command="$command $quoted"
    done
    case "$action" in invalidate|record) release_state_write_unconfirmed=true ;; esac
    local command_status=0
    ssh -p "$SERVER_PORT" -o ConnectTimeout=10 -o BatchMode=yes \
        -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
        "$SERVER_USER@$SERVER_HOST" "$command" <<'REMOTE_STATE' || command_status=$?
set -euo pipefail
app_dir=$1
action=$2
owner=$3
lock_dir="${app_dir}.release-lock"
marker="$app_dir/.release-provenance"
[[ "$owner" =~ ^[a-zA-Z0-9-]+$ ]] || { echo '无效的发布锁所有者' >&2; exit 1; }

if [[ "$action" == acquire ]]; then
    if ! mkdir -- "$lock_dir" 2>/dev/null; then
        echo "生产发布/验收锁忙或无法建立：${lock_dir}；保留现状，不自动抢锁" >&2
        exit 1
    fi
    if ! printf '%s\n' "$owner" > "$lock_dir/owner"; then
        # Only this process successfully created this directory.
        rm -f -- "$lock_dir/owner" && rmdir -- "$lock_dir" || true
        echo '无法记录发布锁所有者，停止操作' >&2
        exit 1
    fi
    exit 0
fi

[[ -r "$lock_dir/owner" && "$(cat "$lock_dir/owner")" == "$owner" ]] \
    || { echo "发布锁所有权无法确认：${lock_dir}；停止操作" >&2; exit 1; }
case "$action" in
    assert) ;;
    release)
        rm -- "$lock_dir/owner"
        rmdir -- "$lock_dir"
        ;;
    invalidate)
        rm -f -- "$marker"
        ;;
    read)
        [[ -r "$marker" ]] || { echo '生产当前没有可验收的完整发布候选' >&2; exit 1; }
        sed -n 's/^commit=//p' "$marker" | head -n 1
        ;;
    record)
        commit=$4
        mode=$5
        [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || exit 1
        case "$mode" in preview|main|rollback) ;; *) exit 1 ;; esac
        temporary="${marker}.tmp.${owner}"
        umask 022
        {
            printf 'commit=%s\n' "$commit"
            printf 'mode=%s\n' "$mode"
            printf 'recorded_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        } > "$temporary"
        mv -- "$temporary" "$marker"
        ;;
    *) echo '未知的发布协调操作' >&2; exit 1 ;;
esac
REMOTE_STATE
    if [[ "$command_status" -eq 0 ]]; then
        case "$action" in invalidate|record) release_state_write_unconfirmed=false ;; esac
    fi
    return "$command_status"
}

acquire_release_lock() {
    load_release_coordination_config "$1" || return 1
    release_lock_token="$(date -u +%Y%m%d%H%M%S)-$$-$RANDOM-$RANDOM"
    release_remote_state acquire || return 1
    release_lock_owned=true
}

release_owned_lock() {
    [[ "$release_lock_owned" == true ]] || return 0
    release_remote_state release || return 1
    release_lock_owned=false
}
