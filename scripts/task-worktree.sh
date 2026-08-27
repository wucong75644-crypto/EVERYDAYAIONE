#!/usr/bin/env bash

# 任务工作树生命周期入口。
# start 创建隔离工作树并记录其稳定基座；sync-stable-base 只更新本地 Git
# worktree 元数据，不改动任何任务代码；close 只在验收后删除当前任务工作树。

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

fail() {
    echo -e "${RED}[TASK_ERROR]${NC} $1" >&2
    exit 1
}

info() {
    echo -e "${GREEN}[TASK]${NC} $1"
}

usage() {
    cat <<'EOF'
用法：
  ./scripts/task-worktree.sh start <slug> [--base origin/main] [--path PATH]
  ./scripts/task-worktree.sh sync-stable-base --commit <origin-main-sha> [--exclude-path PATH]
  ./scripts/task-worktree.sh close --current --confirm
  ./scripts/task-worktree.sh close --path PATH --confirm

约束：
  start 从最新 origin/main 创建 codex/task/* 分支，并记录其稳定基座。
  sync-stable-base 只更新活跃 codex/task/* 工作树的本地基座元数据，不改代码。
  close 只接受 codex/task/* 分支、干净工作树和明确 --confirm；
  删除本地工作树及本地分支，但保留远程分支和 Git 历史。
EOF
}

repo_root() {
    git rev-parse --show-toplevel 2>/dev/null || fail "当前目录不在 Git 仓库内"
}

sanitize_slug() {
    local slug=$1
    slug=$(printf '%s' "$slug" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9._-]/-/g; s/--*/-/g; s/^[.-]*//; s/[.-]*$//')
    [[ -n "$slug" ]] || fail "任务 slug 为空或不包含有效字符"
    printf '%s' "$slug"
}

set_stable_base() {
    local root=$1
    local task_path=$2
    local commit=$3

    git -C "$root" config extensions.worktreeConfig true
    git -C "$task_path" config --worktree codex.taskStableBase "$commit"
}

start_task() {
    [[ $# -ge 1 ]] || fail "start 缺少任务 slug"
    local slug
    slug=$(sanitize_slug "$1")
    shift

    local base_ref='origin/main'
    local requested_path=''
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --base)
                [[ $# -ge 2 ]] || fail "--base 缺少参数"
                base_ref=$2
                shift 2
                ;;
            --path)
                [[ $# -ge 2 ]] || fail "--path 缺少参数"
                requested_path=$2
                shift 2
                ;;
            *)
                fail "start 未知参数：$1"
                ;;
        esac
    done

    local root
    root=$(repo_root)
    git -C "$root" fetch --prune origin main >/dev/null \
        || fail "无法同步 origin/main，拒绝从可能过期的代码创建任务工作树"
    git -C "$root" rev-parse --verify "${base_ref}^{commit}" >/dev/null \
        || fail "基础提交不存在：$base_ref（请先同步远程 main）"

    local base_commit stamp branch path
    base_commit=$(git -C "$root" rev-parse "${base_ref}^{commit}")
    stamp=$(date '+%Y%m%d%H%M%S')
    branch="codex/task/${stamp}-${slug}"
    path=${requested_path:-"${root}-worktrees/${stamp}-${slug}"}
    [[ "$path" != "$root" ]] || fail "任务工作树不能等于主工作树"
    [[ ! -e "$path" ]] || fail "目标路径已存在：$path"

    mkdir -p "$(dirname "$path")"
    git -C "$root" worktree add -b "$branch" "$path" "$base_commit" >/dev/null
    set_stable_base "$root" "$path" "$base_commit"

    info "TASK_STARTED status=success path=$path branch=$branch base=$base_commit"
    info "请在该路径对应的对话中开发；不要回到主工作树继续发布本任务。"
}

sync_stable_base() {
    local commit=''
    local exclude_path=''

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --commit)
                [[ $# -ge 2 ]] || fail "--commit 缺少参数"
                commit=$2
                shift 2
                ;;
            --exclude-path)
                [[ $# -ge 2 ]] || fail "--exclude-path 缺少参数"
                exclude_path=$2
                shift 2
                ;;
            *)
                fail "sync-stable-base 未知参数：$1"
                ;;
        esac
    done

    [[ -n "$commit" ]] || fail "sync-stable-base 必须提供 --commit"
    local root
    root=$(repo_root)
    commit=$(git -C "$root" rev-parse "${commit}^{commit}") \
        || fail "稳定基座不是有效提交：$commit"
    local origin_main
    origin_main=$(git -C "$root" rev-parse origin/main 2>/dev/null) \
        || fail "无法读取 origin/main，拒绝同步稳定基座"
    [[ "$commit" == "$origin_main" ]] \
        || fail "稳定基座必须等于当前 origin/main：$commit"

    if [[ -n "$exclude_path" ]]; then
        exclude_path=$(cd "$exclude_path" 2>/dev/null && pwd -P) \
            || fail "排除工作树路径不存在：$exclude_path"
    fi

    local path='' branch='' updated=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            'worktree '*)
                path=${line#worktree }
                branch=''
                ;;
            'branch refs/heads/'*)
                branch=${line#branch refs/heads/}
                if [[ "$branch" == codex/task/* && "$path" != "$exclude_path" ]]; then
                    [[ -d "$path" ]] || fail "活跃任务工作树不存在：$path"
                    set_stable_base "$root" "$path" "$commit"
                    ((updated += 1))
                fi
                ;;
        esac
    done < <(git -C "$root" worktree list --porcelain)

    echo "TASK_BASE_SYNC_RESULT status=success commit=$commit updated=$updated excluded=${exclude_path:-none}"
}

close_task() {
    local task_path=''
    local confirmed=false
    local remote='origin'
    local use_current=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --current)
                use_current=true
                shift
                ;;
            --path)
                [[ $# -ge 2 ]] || fail "--path 缺少参数"
                task_path=$2
                shift 2
                ;;
            --confirm)
                confirmed=true
                shift
                ;;
            --remote)
                [[ $# -ge 2 ]] || fail "--remote 缺少参数"
                remote=$2
                shift 2
                ;;
            *)
                fail "close 未知参数：$1"
                ;;
        esac
    done

    [[ "$confirmed" == true ]] || fail "清理是破坏性操作，必须由验收后的关闭技能显式传入 --confirm"
    [[ "$use_current" == true && -z "$task_path" || "$use_current" == false && -n "$task_path" ]] \
        || fail "close 必须二选一：--current 或 --path PATH"

    local current_dir root
    current_dir=$(pwd -P)
    if [[ "$use_current" == true ]]; then
        task_path=$current_dir
    else
        task_path=$(cd "$task_path" 2>/dev/null && pwd -P) \
            || fail "工作树路径不存在：$task_path"
    fi

    root=$(git -C "$task_path" rev-parse --show-toplevel 2>/dev/null) \
        || fail "目标不是 Git 工作树：$task_path"
    root=$(cd "$root" && pwd -P)

    local main_root
    main_root=$(git -C "$root" worktree list --porcelain \
        | awk '/^worktree / && !found {sub(/^worktree /, ""); first=$0; found=1} END {if (found) print first}')
    [[ -n "$main_root" ]] || fail "无法确定仓库主工作树"
    main_root=$(cd "$main_root" && pwd -P)
    [[ "$task_path" != "$main_root" ]] || fail "拒绝清理主工作树：$main_root"

    local branch commit
    branch=$(git -C "$task_path" symbolic-ref --quiet --short HEAD) \
        || fail "目标工作树不是分支，拒绝清理：$task_path"
    [[ "$branch" == codex/task/* ]] \
        || fail "只允许清理 codex/task/* 分支，实际是：$branch"
    [[ -z "$(git -C "$task_path" status --porcelain --untracked-files=all)" ]] \
        || fail "工作树仍有未提交内容，拒绝清理：$task_path"

    commit=$(git -C "$task_path" rev-parse HEAD)
    git -C "$main_root" ls-remote --exit-code "$remote" "refs/heads/$branch" >/dev/null \
        || fail "远程分支不存在，拒绝清理：$remote/$branch"

    git -C "$main_root" worktree remove "$task_path" >/dev/null
    git -C "$main_root" branch -D "$branch" >/dev/null

    echo "TASK_CLOSE_RESULT status=success path=$task_path branch=$branch commit=$commit remote=$remote/$branch worktree_removed=true local_branch_removed=true remote_preserved=true"
}

main() {
    [[ $# -gt 0 ]] || { usage; exit 1; }
    case "$1" in
        start)
            shift
            start_task "$@"
            ;;
        sync-stable-base)
            shift
            sync_stable_base "$@"
            ;;
        close)
            shift
            close_task "$@"
            ;;
        -h|--help)
            usage
            ;;
        *)
            usage
            fail "未知命令：$1"
            ;;
    esac
}

main "$@"
