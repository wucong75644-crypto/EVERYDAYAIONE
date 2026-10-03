#!/usr/bin/env bash

# Prepare one isolated personal subtree inside the existing Skill NAS.
# Called by deploy.sh only while release.sh owns the production release lock.
set -euo pipefail

backend_env=/var/www/everydayai/backend/.env
skill_root=/mnt/platform-skills
workspace_root=/mnt/nas-workspace
personal_mount="$skill_root/personal"
personal_alias="$workspace_root/.platform-skills/personal"
fstab=/etc/fstab

if [[ ! -f "$backend_env" ]] || ! grep -Eq '^SKILL_CATALOG_ENABLED=(true|1)$' "$backend_env"; then
    echo "Skill catalog 未启用，跳过个人 Skill 存储准备"
    exit 0
fi

configured_skill_root=$(sed -n 's/^SKILL_STORAGE_ROOT=//p' "$backend_env" | tail -n 1)
if [[ "$configured_skill_root" != "$skill_root" ]]; then
    echo "❌ Skill catalog 已启用，但存储根目录与受控 NAS 配置不匹配"
    exit 1
fi

mount_fields() {
    findmnt --noheadings --raw --mountpoint "$1" --output SOURCE,FSTYPE,OPTIONS
}

read -r workspace_source workspace_fstype workspace_options \
    <<< "$(findmnt --noheadings --raw --target "$workspace_root" --output SOURCE,FSTYPE,OPTIONS)"
read -r platform_source platform_fstype platform_options \
    <<< "$(mount_fields "$skill_root")"

case "$workspace_fstype" in nfs|nfs4) ;; *) echo "❌ 用户 NAS 根目录不是 NFS"; exit 1 ;; esac
case ",$workspace_options," in *,rw,*) ;; *) echo "❌ 用户 NAS 根目录不可写，无法准备 Skill 子目录"; exit 1 ;; esac
case "$platform_fstype" in nfs|nfs4) ;; *) echo "❌ Skill 存储根目录不是 NFS"; exit 1 ;; esac
case ",$platform_options," in *,ro,*) ;; *) echo "❌ Skill 存储父目录必须保持只读"; exit 1 ;; esac

expected_platform_source="${workspace_source%/}/.platform-skills"
if [[ "${platform_source%/}" != "$expected_platform_source" ]]; then
    echo "❌ 用户空间与 Skill 存储不在同一 NAS 根源"
    exit 1
fi

if [[ ! -e "$personal_alias" ]]; then
    install -d -o root -g root -m 0750 "$personal_alias"
fi
if [[ -L "$personal_alias" ]]; then
    echo "❌ NAS personal Skill 根目录不能是符号链接"
    exit 1
fi
alias_owner_mode=$(stat -c '%u:%g:%a' "$personal_alias")
if [[ "$alias_owner_mode" != '0:0:750' ]]; then
    echo "❌ NAS personal Skill 根目录需要 root:root 0750"
    exit 1
fi

# The read-only parent export must expose the same NAS directory before it can
# be mounted as an independent read-write child. Account for NFS lookup caching.
for _ in $(seq 1 15); do
    [[ -d "$personal_mount" ]] && break
    sleep 1
done
if [[ ! -d "$personal_mount" ]]; then
    echo "❌ NAS personal 目录尚未通过只读 Skill 挂载可见，未修改 fstab"
    exit 1
fi
if [[ -L "$personal_mount" ]]; then
    echo "❌ Skill personal 挂载目录不能是符号链接"
    exit 1
fi
if [[ "$(stat -c '%i' "$personal_alias")" != "$(stat -c '%i' "$personal_mount")" ]]; then
    echo "❌ NAS 别名与 Skill 根目录中的 personal 不是同一个目录"
    exit 1
fi

expected_personal_source="${platform_source%/}/personal"
existing_entries=$(awk -v target="$personal_mount" '$2 == target {count++} END {print count+0}' "$fstab")
if (( existing_entries > 1 )); then
    echo "❌ fstab 中存在重复的 personal Skill 挂载项"
    exit 1
fi
if (( existing_entries == 1 )); then
    read -r fstab_source _ fstab_type fstab_options _ _ \
        <<< "$(awk -v target="$personal_mount" '$2 == target {print}' "$fstab")"
    if [[ "$fstab_source" != "$expected_personal_source" || "$fstab_type" != "$platform_fstype" ]]; then
        echo "❌ 已有 personal Skill fstab 项指向其他存储，拒绝覆盖"
        exit 1
    fi
    case ",$fstab_options," in *,rw,*) ;; *) echo "❌ personal Skill fstab 项必须可写"; exit 1 ;; esac
else
    org_entry=$(awk '$2 == "/mnt/platform-skills/org" {count++; line=$0} END {if (count == 1) print line; else exit 1}' "$fstab") || {
        echo "❌ 无法从现有 org 挂载推导 Skill NAS 选项"
        exit 1
    }
    read -r org_source _ org_type org_options org_dump org_pass <<< "$org_entry"
    if [[ "$org_source" != "${platform_source%/}/org" || "$org_type" != "$platform_fstype" ]]; then
        echo "❌ 现有 org 挂载与 Skill NAS 根目录不一致"
        exit 1
    fi
    case ",$org_options," in *,rw,*) ;; *) echo "❌ 现有 org Skill 挂载不是可写配置"; exit 1 ;; esac

    backup="$fstab.skill-personal.$(date -u +%Y%m%dT%H%M%SZ).bak"
    if [[ -e "$backup" ]]; then
        echo "❌ fstab 备份路径已存在，拒绝覆盖: $backup"
        exit 1
    fi
    cp -a "$fstab" "$backup"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$expected_personal_source" "$personal_mount" "$org_type" "$org_options" \
        "$org_dump" "$org_pass" >> "$fstab"
    echo "已备份 fstab 并添加一条 personal Skill 子挂载"
fi

if ! mountpoint -q "$personal_mount"; then
    mount "$personal_mount"
fi

read -r mounted_source mounted_type mounted_options <<< "$(mount_fields "$personal_mount")"
if [[ "$mounted_source" != "$expected_personal_source" || "$mounted_type" != "$platform_fstype" ]]; then
    echo "❌ personal Skill 实际挂载与现有 Skill NAS 不一致"
    exit 1
fi
case ",$mounted_options," in *,rw,*) ;; *) echo "❌ personal Skill NAS 子挂载不是可写状态"; exit 1 ;; esac
case ",$platform_options," in *,ro,*) ;; *) echo "❌ Skill 存储父目录必须保持只读"; exit 1 ;; esac
if [[ "$(stat -c '%u:%g:%a' "$personal_mount")" != '0:0:750' ]]; then
    echo "❌ personal Skill 挂载根目录需要 root:root 0750"
    exit 1
fi

echo "✅ 个人 Skill 子目录已在现有 NAS 上持久挂载；父目录只读、personal 子目录可写"
