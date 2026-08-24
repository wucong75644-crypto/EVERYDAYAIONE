#!/usr/bin/env bash

# 任务工作树生命周期入口。
# start 只创建隔离工作树；close 只在用户明确验收后删除当前任务工作树和本地分支。

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
  ./scripts/task-worktree.sh sync-check [--exclude PATH]  # 只检查，不修改任务
  ./scripts/task-worktree.sh close --current --confirm
  ./scripts/task-worktree.sh close --path PATH --confirm

约束：
  start 从 origin/main 创建 codex/task/* 分支和独立工作树。
  close 只接受 codex/task/* 分支、干净工作树和明确 --confirm；
  删除本地工作树及本地分支，但保留远程分支和 Git 历史。
EOF
}

list_active_task_worktrees() {
    local root=$1
    git -C "$root" worktree list --porcelain \
    | awk '
        /^worktree / { path=substr($0, 10); branch="" }
        /^branch refs\/heads\/codex\/task\// {
            branch=substr($0, 19); print path "\t" branch
        }
    '
}

check_active_task_bases() {
    local root=$1
    local remote=$2
    local excluded_path=${3:-}
    local main_sha
    git -C "$root" fetch --prune "$remote" main >/dev/null \
        || fail "无法同步 ${remote}/main，不能检查活动任务基座"
    main_sha=$(git -C "$root" rev-parse "${remote}/main^{commit}") \
        || fail "无法解析 ${remote}/main"

    local checked=0
    local stale=0
    local task_path task_branch
    while IFS=$'\t' read -r task_path task_branch; do
        [[ -n "$task_path" && -n "$task_branch" ]] || continue
        [[ "$task_path" == "$excluded_path" ]] && continue
        checked=$((checked + 1))
        if ! git -C "$root" merge-base --is-ancestor "$main_sha" "$task_branch"; then
            echo -e "${RED}[TASK_ERROR]${NC} 活动任务未同步最新 main：$task_branch ($task_path)" >&2
            stale=1
        fi
    done < <(list_active_task_worktrees "$root")

    [[ "$stale" -eq 0 ]] \
        || fail "存在未同步的活动任务，拒绝清理；先将它们同步到 ${remote}/main（$main_sha）"
    echo "TASK_SYNC_RESULT status=success main=$main_sha checked=$checked stale=0"
}

sync_active_task_bases() {
    local root=$1
    local remote=$2
    local excluded_path=${3:-}
    local expected_main_sha=${4:-}
    local main_sha
    git -C "$root" fetch --prune "$remote" main >/dev/null \
        || fail "无法同步 ${remote}/main，不能更新活动任务基座"
    main_sha=$(git -C "$root" rev-parse "${remote}/main^{commit}") \
        || fail "无法解析 ${remote}/main"
    [[ -z "$expected_main_sha" || "$main_sha" == "$expected_main_sha" ]] \
        || fail "清理期间 ${remote}/main 已变化，拒绝把未确认版本同步给其他任务"

    local task_path task_branch local_sha remote_sha
    local -a pending_paths=()
    local -a pending_branches=()
    while IFS=$'\t' read -r task_path task_branch; do
        [[ -n "$task_path" && -n "$task_branch" ]] || continue
        [[ "$task_path" == "$excluded_path" ]] && continue

        git -C "$root" fetch --prune "$remote" "$task_branch" >/dev/null \
            || fail "活动任务远程分支不存在或无法同步：$remote/$task_branch"
        local_sha=$(git -C "$task_path" rev-parse HEAD) \
            || fail "无法读取活动任务当前提交：$task_path"
        remote_sha=$(git -C "$root" rev-parse "${remote}/${task_branch}^{commit}") \
            || fail "无法读取活动任务远程提交：$remote/$task_branch"

        [[ "$local_sha" == "$remote_sha" ]] \
            || fail "活动任务本地分支与远程分支不一致，拒绝覆盖：$task_branch ($task_path)"

        if git -C "$root" merge-base --is-ancestor "$main_sha" "$task_branch"; then
            info "活动任务已包含稳定 main：$task_branch ($task_path)"
            continue
        fi

        [[ -z "$(git -C "$task_path" status --porcelain --untracked-files=all)" ]] \
            || fail "活动任务有未提交或未跟踪修改，拒绝同步：$task_branch ($task_path)"
        pending_paths+=("$task_path")
        pending_branches+=("$task_branch")
    done < <(list_active_task_worktrees "$root")

    local i
    # 先在工作树外做一次合并可行性检查，避免稳定标记后才发现第一个冲突。
    # 真正合并仍在目标任务工作树执行，并且始终使用非 force push。
    for i in "${!pending_paths[@]}"; do
        task_branch=${pending_branches[$i]}
        git -C "$root" merge-tree --write-tree --quiet "$task_branch" "$main_sha" \
            || fail "稳定 main 与活动任务存在合并冲突，拒绝进入清理：$task_branch"
    done

    for i in "${!pending_paths[@]}"; do
        task_path=${pending_paths[$i]}
        task_branch=${pending_branches[$i]}
        info "同步稳定 main 到活动任务：$task_branch ($task_path)"
        if ! git -C "$task_path" merge --no-edit "$main_sha"; then
            git -C "$task_path" merge --abort >/dev/null 2>&1 || true
            fail "同步活动任务时发生冲突，已保留工作树：$task_branch ($task_path)"
        fi
        git -C "$task_path" push "$remote" "HEAD:refs/heads/$task_branch" >/dev/null \
            || fail "活动任务基座推送失败，已保留工作树：$remote/$task_branch"
        git -C "$root" fetch "$remote" "$task_branch" >/dev/null \
            || fail "无法验证活动任务基座推送：$remote/$task_branch"
        git -C "$root" merge-base --is-ancestor "$main_sha" "$task_branch" \
            || fail "活动任务基座同步验证失败：$task_branch ($task_path)"
    done

    echo "TASK_BASE_SYNC_RESULT status=success main=$main_sha synced=${#pending_paths[@]}"
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

    local stamp branch path
    stamp=$(date '+%Y%m%d%H%M%S')
    branch="codex/task/${stamp}-${slug}"
    path=${requested_path:-"${root}-worktrees/${stamp}-${slug}"}
    [[ "$path" != "$root" ]] || fail "任务工作树不能等于主工作树"
    [[ ! -e "$path" ]] || fail "目标路径已存在：$path"

    mkdir -p "$(dirname "$path")"
    git -C "$root" worktree add -b "$branch" "$path" "$base_ref" >/dev/null

    info "TASK_STARTED status=success path=$path branch=$branch base=$base_ref"
    info "请在该路径对应的对话中开发；不要回到主工作树继续发布本任务。"
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

    local branch commit main_sha stable_tag
    branch=$(git -C "$task_path" symbolic-ref --quiet --short HEAD) \
        || fail "目标工作树不是分支，拒绝清理：$task_path"
    [[ "$branch" == codex/task/* ]] \
        || fail "只允许清理 codex/task/* 分支，实际是：$branch"
    [[ -z "$(git -C "$task_path" status --porcelain --untracked-files=all)" ]] \
        || fail "工作树仍有未提交内容，拒绝清理：$task_path"

    commit=$(git -C "$task_path" rev-parse HEAD)
    git -C "$main_root" ls-remote --exit-code "$remote" "refs/heads/$branch" >/dev/null \
        || fail "远程分支不存在，拒绝清理：$remote/$branch"

    git -C "$main_root" fetch --prune "$remote" main >/dev/null \
        || fail "无法同步 ${remote}/main，拒绝清理工作树"
    main_sha=$(git -C "$main_root" rev-parse "${remote}/main^{commit}") \
        || fail "无法解析 ${remote}/main，拒绝清理工作树"
    git -C "$main_root" merge-base --is-ancestor "$commit" "$main_sha" \
        || fail "当前任务尚未进入最新 ${remote}/main，拒绝清理：$commit"

    stable_tag="production-stable-$(date -u '+%Y%m%d%H%M%S')-${main_sha:0:12}"
    git -C "$main_root" tag -a "$stable_tag" "$main_sha" \
        -m "production stable ${main_sha}" \
        || fail "无法标记生产稳定版本：$stable_tag"
    git -C "$main_root" push "$remote" "refs/tags/$stable_tag" >/dev/null \
        || fail "生产稳定版本标签推送失败，工作树保留：$stable_tag"
    echo "STABLE_VERSION_RESULT status=success tag=$stable_tag main=$main_sha"

    # 只有进入“清理工作树”流程，才把刚确认的稳定 main 同步给其他活动任务。
    # 普通提交部署不会调用此函数，因此候选版本仍可回退，不会改变其他任务基座。
    sync_active_task_bases "$main_root" "$remote" "$task_path" "$main_sha"

    # 不使用 --force：任何未预期的工作树状态都必须阻止删除。
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
        close)
            shift
            close_task "$@"
            ;;
        sync-check)
            shift
            root=$(repo_root)
            remote=origin
            excluded_path=''
            while [[ $# -gt 0 ]]; do
                case "$1" in
                    --exclude)
                        [[ $# -ge 2 ]] || fail "sync-check --exclude 缺少参数"
                        excluded_path=$(cd "$2" 2>/dev/null && pwd -P) \
                            || fail "同步检查排除路径不存在：$2"
                        shift 2
                        ;;
                    --remote)
                        [[ $# -ge 2 ]] || fail "sync-check --remote 缺少参数"
                        remote=$2
                        shift 2
                        ;;
                    *)
                        fail "sync-check 未知参数：$1"
                        ;;
                esac
            done
            check_active_task_bases "$root" "$remote" "$excluded_path"
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
