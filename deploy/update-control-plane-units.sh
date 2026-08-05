#!/bin/bash

set -Eeuo pipefail

operation=${1:-}
release_sha=${2:-}
manifest_path=${3:-}
deploy_dir=${CONTROL_PLANE_DEPLOY_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
systemd_dir=${SYSTEMD_UNIT_DIR:-/etc/systemd/system}
backup_root=${CONTROL_PLANE_UNIT_BACKUP_ROOT:-/var/backups/everydayai/control-plane-units}
backup_dir="${backup_root}/${release_sha}"
update_started=false

services=(
    everydayai-agent-runtime
    everydayai-agent-projection
    everydayai-agent-authorization
)
manifest_names=()
manifest_hashes=()

sha256_file() {
    local path=$1
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$path" | awk '{print $1}'
    else
        shasum -a 256 "$path" | awk '{print $1}'
    fi
}

contains_name() {
    local candidate=$1
    local item
    for item in "${manifest_names[@]-}"; do
        [ "$candidate" != "$item" ] || return 0
    done
    return 1
}

expected_hash() {
    local expected_name=$1
    local index
    for index in "${!manifest_names[@]}"; do
        if [ "${manifest_names[$index]}" = "$expected_name" ]; then
            printf '%s' "${manifest_hashes[$index]}"
            return 0
        fi
    done
    return 1
}

read_manifest() {
    local hash name extra
    [ -f "$manifest_path" ] && [ ! -L "$manifest_path" ] || {
        echo "❌ 缺少 reviewed unit SHA-256 manifest" >&2
        return 1
    }
    while read -r hash name extra || [ -n "${hash:-}${name:-}${extra:-}" ]; do
        if [[ ! "$hash" =~ ^[0-9a-f]{64}$ ]] || [ -z "${name:-}" ] \
            || [ -n "${extra:-}" ] || contains_name "$name"; then
            echo "❌ reviewed unit manifest 格式或条目无效" >&2
            return 1
        fi
        case "$name" in
            everydayai-agent-runtime.service|everydayai-agent-projection.service|everydayai-agent-authorization.service) ;;
            *)
                echo "❌ reviewed unit manifest 包含非控制面条目" >&2
                return 1
                ;;
        esac
        manifest_hashes+=("$hash")
        manifest_names+=("$name")
    done < "$manifest_path"
    [ "${#manifest_names[@]}" -eq 3 ] || {
        echo "❌ reviewed unit manifest 必须精确包含三个 unit" >&2
        return 1
    }
}

check_unit_states() {
    local service active_state enabled_state
    for service in "${services[@]}"; do
        active_state=$(systemctl is-active "$service" 2>/dev/null || true)
        enabled_state=$(systemctl is-enabled "$service" 2>/dev/null || true)
        if [ "$active_state" != inactive ] || [ "$enabled_state" != disabled ]; then
            echo "❌ ${service} 必须为 inactive:disabled，实际为 ${active_state:-unknown}:${enabled_state:-unknown}" >&2
            return 1
        fi
    done
}

preflight_update() {
    [[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || {
        echo "❌ control-plane update 需要 40 位 release SHA" >&2
        return 1
    }
    read_manifest
    check_unit_states
    local service source target expected actual
    for service in "${services[@]}"; do
        source="${deploy_dir}/${service}.service"
        target="${systemd_dir}/${service}.service"
        [ -f "$source" ] && [ ! -L "$source" ] || {
            echo "❌ 缺少候选 unit: ${source}" >&2
            return 1
        }
        [ -f "$target" ] && [ ! -L "$target" ] || {
            echo "❌ reviewed update 要求已存在普通 target unit: ${target}" >&2
            return 1
        }
        [ ! -e "${target}.c7-${release_sha}.tmp" ] \
            && [ ! -L "${target}.c7-${release_sha}.tmp" ] || {
            echo "❌ unit staging 目标已存在" >&2
            return 1
        }
        expected=$(expected_hash "${service}.service")
        actual=$(sha256_file "$target")
        if [ "$actual" != "$expected" ]; then
            echo "❌ target unit SHA-256 与 reviewed manifest 不匹配: ${service}" >&2
            return 1
        fi
    done
}

atomic_install() {
    local source=$1
    local target=$2
    local staged="${target}.c7-${release_sha}.tmp"
    sudo install -m 0644 "$source" "$staged"
    sudo mv -f "$staged" "$target"
}

cleanup_staged() {
    local service
    for service in "${services[@]}"; do
        sudo rm -f "${systemd_dir}/${service}.service.c7-${release_sha}.tmp"
    done
}

restore_backups() {
    local service backup target restore_failed=0
    set +e
    for service in "${services[@]}"; do
        backup="${backup_dir}/${service}.service"
        target="${systemd_dir}/${service}.service"
        if [ ! -f "$backup" ] || ! atomic_install "$backup" "$target"; then
            restore_failed=1
        fi
    done
    sudo systemctl daemon-reload || restore_failed=1
    for service in "${services[@]}"; do
        cmp --silent "${backup_dir}/${service}.service" \
            "${systemd_dir}/${service}.service" || restore_failed=1
    done
    cleanup_staged
    set -e
    return "$restore_failed"
}

rollback_on_exit() {
    local result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then
        set +e
        if [ "$update_started" = true ]; then
            if restore_backups; then
                echo "✅ control-plane unit 更新失败，已恢复 reviewed 旧 unit 并 daemon-reload" >&2
            else
                echo "❌ control-plane unit 自动恢复失败，必须人工处置" >&2
                result=1
            fi
        fi
    fi
    exit "$result"
}

backup_all_units() {
    if { [ -e "$backup_root" ] || [ -L "$backup_root" ]; } \
        && { [ ! -d "$backup_root" ] || [ -L "$backup_root" ]; }; then
        echo "❌ control-plane unit backup root 必须是普通目录" >&2
        return 1
    fi
    if { [ -e "$backup_dir" ] || [ -L "$backup_dir" ]; } \
        && { [ ! -d "$backup_dir" ] || [ -L "$backup_dir" ]; }; then
        echo "❌ release unit backup 必须是普通目录" >&2
        return 1
    fi
    sudo install -d -m 0750 "$backup_dir"
    local service target backup expected actual
    for service in "${services[@]}"; do
        target="${systemd_dir}/${service}.service"
        backup="${backup_dir}/${service}.service"
        if [ -e "$backup" ] || [ -L "$backup" ]; then
            [ -f "$backup" ] && [ ! -L "$backup" ] || {
                echo "❌ release unit backup 必须是普通文件: ${backup}" >&2
                return 1
            }
            cmp --silent "$target" "$backup" || {
                echo "❌ release 备份目录已有不同内容: ${backup}" >&2
                return 1
            }
        else
            sudo install -m 0644 "$target" "$backup"
        fi
    done
    for service in "${services[@]}"; do
        target="${systemd_dir}/${service}.service"
        backup="${backup_dir}/${service}.service"
        cmp --silent "$target" "$backup" || return 1
        expected=$(expected_hash "${service}.service")
        actual=$(sha256_file "$backup")
        [ "$actual" = "$expected" ] || {
            echo "❌ 备份 unit SHA-256 与 reviewed manifest 不匹配: ${service}" >&2
            return 1
        }
    done
}

apply_update() {
    preflight_update
    backup_all_units
    update_started=true
    local service source target
    for service in "${services[@]}"; do
        source="${deploy_dir}/${service}.service"
        target="${systemd_dir}/${service}.service"
        atomic_install "$source" "$target"
        cmp --silent "$source" "$target"
    done
    sudo systemctl daemon-reload
    check_unit_states
    cleanup_staged
    update_started=false
    echo "✅ 三个 control-plane unit 已完成 reviewed 原子更新"
}

case "$operation" in
    preflight)
        preflight_update
        echo "✅ control-plane unit reviewed update 预检通过"
        ;;
    apply)
        trap rollback_on_exit EXIT
        apply_update
        ;;
    rollback)
        [[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
        restore_backups
        echo "✅ 三个 control-plane unit 已从 release 备份恢复"
        ;;
    *)
        echo "usage: $0 preflight|apply|rollback <release-sha> [manifest]" >&2
        exit 2
        ;;
esac
