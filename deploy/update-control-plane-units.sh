#!/bin/bash

set -Eeuo pipefail

operation=${1:-}
release_sha=${2:-}
manifest_path=${3:-}
deploy_dir=${CONTROL_PLANE_DEPLOY_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
systemd_dir=${SYSTEMD_UNIT_DIR:-/etc/systemd/system}
env_dir=${CONTROL_PLANE_ENV_DIR:-/etc/everydayai}
env_tool=${CONTROL_PLANE_ENV_TOOL:-${deploy_dir}/provision-control-plane-worker-envs.py}
transaction_root=${CONTROL_PLANE_TRANSACTION_ROOT:-/var/backups/everydayai/control-plane-updates}
release_dir="${transaction_root}/${release_sha}"
unit_backup_dir="${release_dir}/units"
unit_journal="${release_dir}/unit-journal.sha256"
unit_status_file="${release_dir}/unit-status"
update_started=false

services=(
    everydayai-agent-runtime
    everydayai-agent-projection
    everydayai-agent-authorization
)
manifest_names=()
manifest_hashes=()
journal_names=()
journal_old_hashes=()
journal_new_hashes=()

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

contains() {
    local candidate=$1 item
    shift
    for item in "$@"; do
        [ "$candidate" != "$item" ] || return 0
    done
    return 1
}

manifest_hash() {
    local name=$1 index
    for index in "${!manifest_names[@]}"; do
        if [ "${manifest_names[$index]}" = "$name" ]; then
            printf '%s' "${manifest_hashes[$index]}"
            return
        fi
    done
    return 1
}

read_manifest() {
    local hash name extra
    manifest_names=()
    manifest_hashes=()
    [ -f "$manifest_path" ] && [ ! -L "$manifest_path" ] || {
        echo "❌ 缺少 reviewed unit SHA-256 manifest" >&2
        return 1
    }
    while read -r hash name extra || [ -n "${hash:-}${name:-}${extra:-}" ]; do
        if [[ ! "$hash" =~ ^[0-9a-f]{64}$ ]] || [ -z "${name:-}" ] \
            || [ -n "${extra:-}" ] || contains "$name" "${manifest_names[@]-}"; then
            echo "❌ reviewed unit manifest 格式或条目无效" >&2
            return 1
        fi
        case "$name" in
            everydayai-agent-runtime.service|everydayai-agent-projection.service|everydayai-agent-authorization.service) ;;
            *) echo "❌ reviewed unit manifest 包含非控制面条目" >&2; return 1 ;;
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
    local service active enabled load_state
    load_state=$(systemctl show everydayai-agent-model-gateway \
        -p LoadState --value 2>/dev/null || true)
    if [ "${load_state:-unknown}" != not-found ]; then
        echo "❌ legacy Model Gateway 必须先完成受审查退役" >&2
        return 1
    fi
    for legacy_env in agent-model-gateway.env agent-model-gateway-kek.env; do
        if [ -e "${env_dir}/${legacy_env}" ] || [ -L "${env_dir}/${legacy_env}" ]; then
            echo "❌ legacy Model Gateway env 必须先完成受审查退役" >&2
            return 1
        fi
    done
    for service in "${services[@]}"; do
        active=$(systemctl is-active "$service" 2>/dev/null || true)
        enabled=$(systemctl is-enabled "$service" 2>/dev/null || true)
        if [ "$active" != inactive ] || [ "$enabled" != disabled ]; then
            echo "❌ ${service} 必须为 inactive:disabled，实际为 ${active:-unknown}:${enabled:-unknown}" >&2
            return 1
        fi
    done
}

run_env_transaction() {
    python3 "$env_tool" "$1" --env-dir "$env_dir" \
        --release-sha "$release_sha" --transaction-root "$transaction_root"
}

preflight_units() {
    check_unit_states
    local service source target expected
    for service in "${services[@]}"; do
        source="${deploy_dir}/${service}.service"
        target="${systemd_dir}/${service}.service"
        [ -f "$source" ] && [ ! -L "$source" ] || {
            echo "❌ 缺少候选 unit: ${source}" >&2; return 1
        }
        [ -f "$target" ] && [ ! -L "$target" ] || {
            echo "❌ reviewed update 要求已存在普通 target unit: ${target}" >&2; return 1
        }
        [ ! -e "${target}.c7-${release_sha}.tmp" ] \
            && [ ! -L "${target}.c7-${release_sha}.tmp" ] || {
            echo "❌ unit staging 目标已存在" >&2; return 1
        }
        expected=$(manifest_hash "${service}.service")
        [ "$(sha256_file "$target")" = "$expected" ] || {
            echo "❌ target unit SHA-256 与 reviewed manifest 不匹配: ${service}" >&2
            return 1
        }
    done
}

preflight_update() {
    [[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || {
        echo "❌ control-plane update 需要 40 位 release SHA" >&2; return 1
    }
    read_manifest
    run_env_transaction preflight
    preflight_units
}

secure_transaction_dir() {
    [ -d "$release_dir" ] && [ ! -L "$release_dir" ] \
        && [ "$(stat -c '%a' "$release_dir" 2>/dev/null || stat -f '%Lp' "$release_dir")" = 700 ] || {
        echo "❌ release transaction 目录不安全" >&2; return 1
    }
}

write_unit_journal() {
    local temporary
    temporary=$(mktemp "${release_dir}/.unit-journal.XXXXXX")
    umask 077
    if ! {
        printf 'release_sha %s\n' "$release_sha"
        local service target source
        for service in "${services[@]}"; do
            target="${systemd_dir}/${service}.service"
            source="${deploy_dir}/${service}.service"
            printf '%s %s %s\n' "$(sha256_file "$target")" \
                "$(sha256_file "$source")" "${service}.service"
        done
    } > "$temporary" || ! chmod 0600 "$temporary" || ! mv "$temporary" "$unit_journal"; then
        rm -f "$temporary"
        return 1
    fi
    set_unit_status prepared
}

unit_status() {
    [ -f "$unit_status_file" ] && [ ! -L "$unit_status_file" ] \
        && [ "$(stat -c '%a' "$unit_status_file" 2>/dev/null || stat -f '%Lp' "$unit_status_file")" = 600 ] \
        || { echo "❌ unit transaction status 无效" >&2; return 1; }
    local value
    value=$(<"$unit_status_file")
    case "$value" in prepared|published|restored) printf '%s' "$value" ;; *) return 1 ;; esac
}

set_unit_status() {
    local temporary
    temporary=$(mktemp "${release_dir}/.unit-status.XXXXXX")
    if ! printf '%s\n' "$1" > "$temporary" \
        || ! chmod 0600 "$temporary" || ! mv "$temporary" "$unit_status_file"; then
        rm -f "$temporary"
        return 1
    fi
}

cleanup_unit_backup_dir() {
    local directory=$1 service
    for service in "${services[@]}"; do
        rm -f "${directory}/${service}.service"
    done
    rmdir "$directory" 2>/dev/null || true
}

read_unit_journal() {
    local first_key first_sha old_hash new_hash name extra
    journal_names=()
    journal_old_hashes=()
    journal_new_hashes=()
    [ -f "$unit_journal" ] && [ ! -L "$unit_journal" ] \
        && [ "$(stat -c '%a' "$unit_journal" 2>/dev/null || stat -f '%Lp' "$unit_journal")" = 600 ] || {
        echo "❌ unit transaction journal 无效" >&2; return 1
    }
    read -r first_key first_sha extra < "$unit_journal"
    [ "$first_key" = release_sha ] && [ "$first_sha" = "$release_sha" ] \
        && [ -z "${extra:-}" ] || { echo "❌ unit release fence 不匹配" >&2; return 1; }
    while read -r old_hash new_hash name extra; do
        [[ "$old_hash" =~ ^[0-9a-f]{64}$ && "$new_hash" =~ ^[0-9a-f]{64}$ ]] \
            && [ -z "${extra:-}" ] || { echo "❌ unit journal 条目无效" >&2; return 1; }
        case "$name" in
            everydayai-agent-runtime.service|everydayai-agent-projection.service|everydayai-agent-authorization.service) ;;
            *) echo "❌ unit journal 文件集合无效" >&2; return 1 ;;
        esac
        contains "$name" "${journal_names[@]-}" && {
            echo "❌ unit journal 包含重复条目" >&2; return 1
        }
        journal_old_hashes+=("$old_hash")
        journal_new_hashes+=("$new_hash")
        journal_names+=("$name")
    done < <(tail -n +2 "$unit_journal")
    [ "${#journal_names[@]}" -eq 3 ] || {
        echo "❌ unit journal 必须精确包含三个 unit" >&2; return 1
    }
}

prepare_unit_backups() {
    secure_transaction_dir
    if [ -e "$unit_backup_dir" ] || [ -L "$unit_backup_dir" ]; then
        read_unit_journal
        [ "$(unit_status)" = restored ] || {
            echo "❌ release unit backup 已存在且事务未恢复" >&2; return 1
        }
        local index
        for index in "${!journal_names[@]}"; do
            [ "${journal_old_hashes[$index]}" = "$(manifest_hash "${journal_names[$index]}")" ] \
                && [ "${journal_new_hashes[$index]}" = "$(sha256_file "${deploy_dir}/${journal_names[$index]}")" ] \
                && [ "${journal_old_hashes[$index]}" = "$(sha256_file "${unit_backup_dir}/${journal_names[$index]}")" ] \
                || { echo "❌ 既有 unit transaction fence 不匹配" >&2; return 1; }
        done
        set_unit_status prepared
        return
    fi
    local preparing_dir="${release_dir}/.units.prepare.$$"
    [ ! -e "$preparing_dir" ] && [ ! -L "$preparing_dir" ] || return 1
    install -d -m 0700 "$preparing_dir"
    local service target backup
    for service in "${services[@]}"; do
        target="${systemd_dir}/${service}.service"
        backup="${preparing_dir}/${service}.service"
        if ! install -m 0600 "$target" "$backup"; then
            cleanup_unit_backup_dir "$preparing_dir"
            return 1
        fi
    done
    if ! mv "$preparing_dir" "$unit_backup_dir"; then
        cleanup_unit_backup_dir "$preparing_dir"
        return 1
    fi
    if ! write_unit_journal; then
        cleanup_unit_backup_dir "$unit_backup_dir"
        return 1
    fi
    read_unit_journal
    for service in "${services[@]}"; do
        backup="${unit_backup_dir}/${service}.service"
        [ -f "$backup" ] && [ ! -L "$backup" ] || return 1
        [ "$(sha256_file "$backup")" = "$(manifest_hash "${service}.service")" ] || return 1
    done
}

atomic_install() {
    local source=$1 target=$2 staged="${2}.c7-${release_sha}.tmp"
    install -m 0644 "$source" "$staged"
    mv -f "$staged" "$target"
}

cleanup_staged() {
    local service
    for service in "${services[@]}"; do
        rm -f "${systemd_dir}/${service}.service.c7-${release_sha}.tmp"
    done
}

unit_rollback_preflight() {
    secure_transaction_dir
    read_unit_journal
    local index name backup target current status
    status=$(unit_status)
    for index in "${!journal_names[@]}"; do
        name=${journal_names[$index]}
        backup="${unit_backup_dir}/${name}"
        target="${systemd_dir}/${name}"
        [ -f "$backup" ] && [ ! -L "$backup" ] \
            && [ "$(sha256_file "$backup")" = "${journal_old_hashes[$index]}" ] || {
            echo "❌ unit backup hash fence 不匹配" >&2; return 1
        }
        [ -f "$target" ] && [ ! -L "$target" ] || return 1
        current=$(sha256_file "$target")
        if [ "$current" != "${journal_old_hashes[$index]}" ] \
            && { [ "$status" = restored ] || [ "$current" != "${journal_new_hashes[$index]}" ]; }; then
            echo "❌ unit rollback hash fence 不匹配" >&2; return 1
        fi
    done
}

restore_transaction() {
    unit_rollback_preflight
    run_env_transaction rollback-preflight
    local index name target changed=false
    for index in "${!journal_names[@]}"; do
        name=${journal_names[$index]}
        target="${systemd_dir}/${name}"
        if [ "$(sha256_file "$target")" != "${journal_old_hashes[$index]}" ]; then
            atomic_install "${unit_backup_dir}/${name}" "$target"
            changed=true
        fi
    done
    run_env_transaction rollback
    if [ "$changed" = true ]; then systemctl daemon-reload; fi
    unit_rollback_preflight
    run_env_transaction rollback-preflight
    set_unit_status restored
    cleanup_staged
}

rollback_on_exit() {
    local result=$1
    trap - EXIT
    if [ "$result" -ne 0 ] && [ "$update_started" = true ]; then
        set +e
        if restore_transaction; then
            echo "✅ control-plane env/unit 更新失败，已统一恢复" >&2
        else
            echo "❌ control-plane env/unit 自动恢复失败，必须人工处置" >&2
            result=1
        fi
    fi
    exit "$result"
}

apply_update() {
    preflight_update
    prepare_unit_backups
    # All nine target preflights are repeated after every backup is durable.
    run_env_transaction preflight
    preflight_units
    update_started=true
    run_env_transaction publish
    run_env_transaction verify
    local service
    for service in "${services[@]}"; do
        atomic_install "${deploy_dir}/${service}.service" \
            "${systemd_dir}/${service}.service"
        cmp --silent "${deploy_dir}/${service}.service" \
            "${systemd_dir}/${service}.service"
    done
    systemctl daemon-reload
    check_unit_states
    run_env_transaction verify
    cleanup_staged
    set_unit_status published
    update_started=false
    echo "✅ 五份 control-plane env 与四个 unit 已完成 release 事务更新"
}

case "$operation" in
    preflight) preflight_update; echo "✅ control-plane env/unit 事务预检通过" ;;
    apply) trap 'rollback_on_exit $?' EXIT; apply_update ;;
    rollback)
        [[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
        restore_transaction
        echo "✅ control-plane env/unit 已从 release transaction 恢复"
        ;;
    *) echo "usage: $0 preflight|apply|rollback <release-sha> [manifest]" >&2; exit 2 ;;
esac
