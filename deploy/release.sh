#!/usr/bin/env bash

# 受控发布入口。
# 提交部署只发布任务候选；清理工作树只验收、合并、同步基座、关闭，不重复部署。

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

fail() {
    echo -e "${RED}[RELEASE_ERROR]${NC} $1" >&2
    exit 1
}

info() {
    echo -e "${GREEN}[RELEASE]${NC} $1"
}

usage() {
    cat <<'EOF'
用法：
  ./deploy/release.sh --message "type: description" --file path/to/file [--file ...]
  ./deploy/release.sh --message "type: description" --source-only-file backend/migrations/NNN_name.sql
  ./deploy/release.sh --message "type: description" --file path/to/file --migration-file backend/migrations/NNN_name.sql
  ./deploy/release.sh --message "type: description" --file-list /tmp/release-files.txt
  ./deploy/release.sh --deploy-task <commit-sha> [--migration-file backend/migrations/NNN_name.sql]
  ./deploy/release.sh --accept-and-close
  ./deploy/release.sh --deploy-main <commit-sha>
  ./deploy/release.sh --rollback <commit-sha>

选项：
  --message MSG          本次任务提交信息（提交部署必填）
  --file PATH            明确纳入本次提交的文件，可重复
  --source-only-file PATH 纳入提交但本次不执行的正向 SQL 迁移，可重复
  --file-list PATH       从本地清单逐行读取发布文件
  --migration-file PATH  明确执行目标提交中已有的正向 SQL 迁移，可重复
  --deploy-task SHA      重新部署当前任务分支上已推送的确定提交；可显式补执行该提交内的正向迁移
  --accept-and-close     已验收任务：核验生产候选、合并 main、同步基座并清理；不部署
  --deploy-main SHA      从已合并到 origin/main 的确定提交部署
  --frontend-only        仅部署前端
  --backend-only         仅部署后端
  --rollback SHA         从 origin/main 历史中的提交回滚应用版本，不回滚数据库迁移
  -h, --help             显示帮助
EOF
}

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) \
    || fail "当前目录不在 Git 仓库内"
main_repo_root=$(git -C "$repo_root" worktree list --porcelain \
    | awk '/^worktree / && !found {sub(/^worktree /, ""); print; found=1}')
[[ -n "$main_repo_root" ]] || fail "无法确定仓库主工作树"
cd "$repo_root"

message=''
rollback_sha=''
deploy_task_sha=''
deploy_main_sha=''
accept_and_close=false
frontend_only=false
backend_only=false
declare -a task_files=()
declare -a source_only_files=()
declare -a migration_files=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --message)
            [[ $# -ge 2 ]] || fail "--message 缺少参数"
            message=$2
            shift 2
            ;;
        --file)
            [[ $# -ge 2 ]] || fail "--file 缺少参数"
            task_files+=("$2")
            shift 2
            ;;
        --source-only-file)
            [[ $# -ge 2 ]] || fail "--source-only-file 缺少参数"
            source_only_files+=("$2")
            shift 2
            ;;
        --file-list)
            [[ $# -ge 2 ]] || fail "--file-list 缺少参数"
            [[ -f "$2" ]] || fail "发布文件清单不存在：$2"
            while IFS= read -r path; do
                [[ -n "$path" ]] && task_files+=("$path")
            done < "$2"
            shift 2
            ;;
        --migration-file)
            [[ $# -ge 2 ]] || fail "--migration-file 缺少参数"
            migration_files+=("$2")
            shift 2
            ;;
        --frontend-only)
            frontend_only=true
            shift
            ;;
        --backend-only)
            backend_only=true
            shift
            ;;
        --rollback)
            [[ $# -ge 2 ]] || fail "--rollback 缺少提交 SHA"
            rollback_sha=$2
            shift 2
            ;;
        --deploy-task)
            [[ $# -ge 2 ]] || fail "--deploy-task 缺少提交 SHA"
            deploy_task_sha=$2
            shift 2
            ;;
        --deploy-main)
            [[ $# -ge 2 ]] || fail "--deploy-main 缺少提交 SHA"
            deploy_main_sha=$2
            shift 2
            ;;
        --accept-and-close)
            accept_and_close=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "未知参数：$1"
            ;;
    esac
done

[[ "$frontend_only" == true && "$backend_only" == true ]] \
    && fail "--frontend-only 与 --backend-only 不能同时使用"

target_count=0
[[ -n "$rollback_sha" ]] && ((target_count += 1))
[[ -n "$deploy_task_sha" ]] && ((target_count += 1))
[[ -n "$deploy_main_sha" ]] && ((target_count += 1))
[[ "$accept_and_close" == true ]] && ((target_count += 1))
[[ "$target_count" -le 1 ]] \
    || fail "--rollback、--deploy-task、--deploy-main 与 --accept-and-close 只能指定一个"

set +u
if [[ "$accept_and_close" == true ]]; then
    [[ -z "$message" && ${#task_files[@]} -eq 0 && ${#source_only_files[@]} -eq 0 && ${#migration_files[@]} -eq 0 && "$frontend_only" == false && "$backend_only" == false ]] \
        || fail "--accept-and-close 不接受提交文件或部署范围参数"
elif [[ -n "$deploy_task_sha" || -n "$deploy_main_sha" ]]; then
    [[ -z "$message" && ${#task_files[@]} -eq 0 && ${#source_only_files[@]} -eq 0 ]] \
        || fail "指定部署目标时不能同时提交新文件"
elif [[ -n "$rollback_sha" ]]; then
    [[ -z "$message" && ${#task_files[@]} -eq 0 && ${#source_only_files[@]} -eq 0 && ${#migration_files[@]} -eq 0 ]] \
        || fail "回滚目标不能同时提交新文件或执行迁移"
else
    [[ -n "$message" ]] || fail "提交部署必须提供 --message"
    [[ $((${#task_files[@]} + ${#source_only_files[@]})) -gt 0 ]] || fail "提交部署必须至少提供一个 --file 或 --source-only-file"
fi
set -u
set +u
for path in "${source_only_files[@]}"; do
    [[ "$path" != /* && "$path" != ../* && "$path" != */../* && "$path" != .git/* ]] \
        || fail "非法发布路径：$path"
    [[ "$path" == backend/migrations/[0-9][0-9][0-9]_*.sql ]] \
        || fail "--source-only-file 只能用于三位编号正向迁移：$path"
done
set -u

set +u
for path in "${task_files[@]}" "${source_only_files[@]}"; do
    [[ "$path" != /* && "$path" != ../* && "$path" != */../* && "$path" != .git/* ]] \
        || fail "非法发布路径：$path"
    [[ "$path" != .env* && "$path" != */.env* && "$path" != .cursor/* && "$path" != .codex/* ]] \
        || fail "发布禁止路径：$path"
done
set -u
set +u
for path in "${migration_files[@]}"; do
    [[ "$path" != /* && "$path" != ../* && "$path" != */../* && "$path" != .git/* ]] \
        || fail "非法迁移路径：$path"
    [[ "$path" == backend/migrations/[0-9][0-9][0-9]_*.sql ]] \
        || fail "迁移文件必须使用三位编号命名且位于 backend/migrations/：$path"
done
set -u

ensure_supported_release_tree() {
    local commit_sha=$1
    local forbidden_path
    for forbidden_path in \
        backend/services/agent/runtime \
        backend/api/routes/runtime_admin.py \
        backend/config/runtime_read_tools.py \
        backend/services/agent/runtime/composition.py; do
        if git cat-file -e "${commit_sha}:$forbidden_path" 2>/dev/null; then
            fail "待发布/验收提交包含已废弃 Runtime 平台路径：$forbidden_path"
        fi
    done
}

read_production_release_commit() {
    local config_file="$repo_root/deploy/config.env"
    [[ -f "$config_file" ]] || fail "缺少 deploy/config.env，无法核验已部署候选"
    # shellcheck disable=SC1090
    source "$config_file"
    [[ -n "${SERVER_HOST:-}" && -n "${SERVER_USER:-}" && -n "${SERVER_PORT:-}" && -n "${REMOTE_APP_DIR:-}" ]] \
        || fail "deploy/config.env 缺少生产核验所需配置"
    ssh -p "$SERVER_PORT" -o ConnectTimeout=10 -o BatchMode=yes "$SERVER_USER@${SERVER_HOST}" \
        "test -r '$REMOTE_APP_DIR/.release-provenance' && sed -n 's/^commit=//p' '$REMOTE_APP_DIR/.release-provenance' | head -n 1"
}

record_local_deployed_candidate() {
    local commit_sha=$1
    git config extensions.worktreeConfig true
    git config --worktree codex.taskDeployedCommit "$commit_sha"
}

release_worktree=''
integration_worktree=''
cleanup_temp_worktrees() {
    [[ -z "$release_worktree" ]] \
        || git -C "$main_repo_root" worktree remove --force "$release_worktree" >/dev/null 2>&1 || true
    [[ -z "$integration_worktree" ]] \
        || git -C "$main_repo_root" worktree remove --force "$integration_worktree" >/dev/null 2>&1 || true
}
trap cleanup_temp_worktrees EXIT

accept_and_close_task() {
    local branch
    branch=$(git symbolic-ref --quiet --short HEAD) \
        || fail "当前不是任务分支，不能清理工作树"
    [[ "$branch" == codex/task/* ]] \
        || fail "清理工作树只能在 codex/task/* 分支执行，实际分支：$branch"
    [[ -z "$(git status --porcelain --untracked-files=all)" ]] \
        || fail "当前任务工作树不干净，不能清理"

    git fetch --prune origin main "$branch" >/dev/null \
        || fail "无法同步 origin/main 和任务分支，不能清理"
    local candidate_sha remote_task_sha
    candidate_sha=$(git rev-parse HEAD)
    remote_task_sha=$(git rev-parse "origin/$branch")
    [[ "$candidate_sha" == "$remote_task_sha" ]] \
        || fail "本地任务分支未与远程同步，不能清理"
    ensure_supported_release_tree "$candidate_sha"

    local deployed_sha
    deployed_sha=$(read_production_release_commit) \
        || fail "无法读取生产发布标记，拒绝在未核验候选的情况下清理"
    [[ "$deployed_sha" == "$candidate_sha" ]] \
        || fail "当前任务提交尚未作为生产测试版本部署：生产=$deployed_sha，任务=$candidate_sha"

    integration_worktree=$(mktemp -d "${TMPDIR:-/tmp}/everydayai-accept.XXXXXX")
    git worktree add --detach "$integration_worktree" origin/main >/dev/null
    if ! git -C "$integration_worktree" merge --no-ff --no-edit "origin/$branch"; then
        fail "任务分支与 origin/main 合并冲突，未修改 main，任务工作树保留"
    fi

    local final_sha candidate_tree final_tree
    final_sha=$(git -C "$integration_worktree" rev-parse HEAD)
    candidate_tree=$(git rev-parse "${candidate_sha}^{tree}")
    final_tree=$(git -C "$integration_worktree" rev-parse "${final_sha}^{tree}")
    [[ "$candidate_tree" == "$final_tree" ]] \
        || fail "最终 main 包含未测试代码，拒绝标记稳定或清理；请先重新提交部署最终合并版本"

    git -C "$integration_worktree" push origin HEAD:refs/heads/main \
        || fail "推送 main 失败，任务工作树保留"
    git fetch --prune origin main >/dev/null \
        || fail "main 已推送但本地无法同步；任务工作树已保留，拒绝继续清理"
    [[ "$(git rev-parse origin/main)" == "$final_sha" ]] \
        || fail "origin/main 与已验收合并提交不一致，拒绝继续清理"

    ./scripts/task-worktree.sh sync-stable-base --commit "$final_sha" --exclude-path "$repo_root"
    ./scripts/task-worktree.sh close --current --confirm
    echo "TASK_ACCEPTANCE_RESULT status=success deployed_commit=$candidate_sha stable_main=$final_sha deployment=skipped"
}

if [[ "$accept_and_close" == true ]]; then
    accept_and_close_task
    exit 0
fi

if [[ -z "$rollback_sha" && -z "$deploy_task_sha" && -z "$deploy_main_sha" ]]; then
    branch=$(git symbolic-ref --quiet --short HEAD) \
        || fail "当前不是分支，拒绝提交任务"
    [[ "$branch" == codex/task/* ]] \
        || fail "任务提交必须在 codex/task/* 工作树中，实际分支：$branch"
    git diff --cached --quiet || fail "已有暂存内容，无法确认其是否属于本次发布"

    declare -a changed_files=()
    while IFS= read -r path; do
        [[ -n "$path" ]] && changed_files+=("$path")
    done < <(
        {
            git -c core.quotePath=false diff --name-only
            git -c core.quotePath=false ls-files --others --exclude-standard -- \
                . ':(exclude).codex/**' ':(exclude)worktrees/**'
        } | sort -u
    )

    set +u
    for changed in "${changed_files[@]}"; do
        allowed=false
        for task_file in "${task_files[@]}" "${source_only_files[@]}"; do
            if [[ "$changed" == "$task_file" ]]; then
                allowed=true
                break
            fi
        done
        [[ "$allowed" == true ]] || fail "工作区存在未列入发布范围的变更：$changed"
    done
    set -u

    if ((${#task_files[@]} > 0)); then
        for task_file in "${task_files[@]}"; do
            [[ -e "$task_file" ]] || fail "发布文件不存在：$task_file"
        done
    fi
    if ((${#source_only_files[@]} > 0)); then
        for source_only_file in "${source_only_files[@]}"; do
            [[ -e "$source_only_file" ]] || fail "发布文件不存在：$source_only_file"
        done
    fi
    if ((${#migration_files[@]} > 0)); then
        for migration_file in "${migration_files[@]}"; do
            [[ -e "$migration_file" ]] || fail "迁移文件不存在：$migration_file"
        done
    fi

    if ((${#source_only_files[@]} > 0)); then
        git add -- "${task_files[@]}" "${source_only_files[@]}"
    else
        git add -- "${task_files[@]}"
    fi
    git diff --cached --quiet && fail "指定文件没有可提交的变更"
    git commit -m "$message"
    commit_sha=$(git rev-parse HEAD)
    git push origin "$branch"
    release_source=$branch
    release_mode=preview
    info "任务提交并推送完成，开始部署生产测试：$commit_sha"
elif [[ -n "$deploy_task_sha" ]]; then
    branch=$(git symbolic-ref --quiet --short HEAD) \
        || fail "当前不是任务分支，拒绝部署任务提交"
    [[ "$branch" == codex/task/* ]] \
        || fail "--deploy-task 仅允许当前 codex/task/* 分支使用"
    git cat-file -e "${deploy_task_sha}^{commit}" \
        || fail "--deploy-task 目标不是有效提交：$deploy_task_sha"
    commit_sha=$(git rev-parse "${deploy_task_sha}^{commit}")
    git merge-base --is-ancestor "$commit_sha" HEAD \
        || fail "--deploy-task 目标不属于当前任务分支"
    remote_task_sha=$(git ls-remote origin "refs/heads/$branch" | awk '{print $1}')
    [[ "$remote_task_sha" == "$commit_sha" ]] \
        || fail "--deploy-task 目标尚未作为当前任务分支的远端最新提交推送"
    release_source=$branch
    release_mode=preview
    info "已确认重新部署任务提交：$commit_sha"
elif [[ -n "$deploy_main_sha" ]]; then
    git fetch --prune origin main >/dev/null \
        || fail "无法同步 origin/main，拒绝依据可能过期的生产来源发布"
    git cat-file -e "${deploy_main_sha}^{commit}" \
        || fail "部署目标不是有效提交：$deploy_main_sha"
    git merge-base --is-ancestor "$deploy_main_sha" origin/main \
        || fail "部署目标不在 origin/main 历史中，拒绝发布：$deploy_main_sha"
    commit_sha=$(git rev-parse "${deploy_main_sha}^{commit}")
    release_source=origin/main
    release_mode=main
    info "生产来源确认：$release_source -> $commit_sha"
else
    git fetch --prune origin main >/dev/null \
        || fail "无法同步 origin/main，拒绝依据可能过期的回滚来源发布"
    git cat-file -e "${rollback_sha}^{commit}" \
        || fail "回滚目标不是有效提交：$rollback_sha"
    git merge-base --is-ancestor "$rollback_sha" origin/main \
        || fail "回滚目标不在 origin/main 历史中，拒绝发布：$rollback_sha"
    commit_sha=$(git rev-parse "${rollback_sha}^{commit}")
    release_source=origin/main
    release_mode=rollback
    info "回滚目标确认：$commit_sha"
fi

ensure_supported_release_tree "$commit_sha"

set +u
for migration_file in "${migration_files[@]}"; do
    git cat-file -e "${commit_sha}:${migration_file}" \
        || fail "目标提交不包含迁移文件：$migration_file"
done
set -u

release_worktree=$(mktemp -d "${TMPDIR:-/tmp}/everydayai-release.XXXXXX")
git worktree add --detach "$release_worktree" "$commit_sha" >/dev/null
if [[ -f "$repo_root/deploy/config.env" ]]; then
    cp "$repo_root/deploy/config.env" "$release_worktree/deploy/config.env"
else
    fail "缺少 deploy/config.env，不能执行生产发布"
fi

release_log="${TMPDIR:-/tmp}/everydayai-release-${commit_sha}.log"
pushd "$release_worktree" >/dev/null
chmod +x deploy/deploy.sh
deploy_args=()
[[ "$frontend_only" == true ]] && deploy_args+=(--frontend-only)
[[ "$backend_only" == true ]] && deploy_args+=(--backend-only)
if [[ "$frontend_only" != true ]]; then
    if ((${#task_files[@]} > 0)); then
        for task_file in "${task_files[@]}"; do
            case "$task_file" in
                backend/migrations/[0-9][0-9][0-9]_*.sql)
                    deploy_args+=(--migration-file "$task_file")
                    ;;
            esac
        done
    fi
    if ((${#migration_files[@]} > 0)); then
        for migration_file in "${migration_files[@]}"; do
            deploy_args+=(--migration-file "$migration_file")
        done
    fi
fi
set +u
if [[ ${#deploy_args[@]} -gt 0 ]]; then
    deploy_command=(bash deploy/deploy.sh "${deploy_args[@]}")
else
    deploy_command=(bash deploy/deploy.sh)
fi
set -u
if ! EVERYDAYAI_RELEASE_CONTEXT=release.sh \
    EVERYDAYAI_RELEASE_COMMIT="$commit_sha" \
    EVERYDAYAI_RELEASE_MODE="$release_mode" \
    "${deploy_command[@]}" >"$release_log" 2>&1; then
    echo "RELEASE_RESULT status=failed commit=$commit_sha log=$release_log" >&2
    tail -n 160 "$release_log" >&2
    exit 1
fi
tail -n 40 "$release_log"
popd >/dev/null

if [[ "$release_mode" == preview ]]; then
    record_local_deployed_candidate "$commit_sha"
fi
echo "RELEASE_RESULT status=success commit=$commit_sha source=$release_source mode=$release_mode status_after=DEPLOYED_PENDING_ACCEPTANCE"
