#!/usr/bin/env bash

# 安全发布入口：只允许发布明确列出的任务文件，并从确定提交的隔离工作树部署。

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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
  ./deploy/release.sh --message "type: description" --file path/to/file --migration-file backend/migrations/NNN_name.sql
  ./deploy/release.sh --message "type: description" --file-list /tmp/release-files.txt
  ./deploy/release.sh --deploy-task <commit-sha>
  ./deploy/release.sh --deploy-main <commit-sha>
  ./deploy/release.sh --rollback <commit-sha>

选项：
  --message MSG        本次发布提交信息（提交发布必填）
  --file PATH          明确纳入本次提交的文件，可重复
  --file-list PATH     从本地清单逐行读取发布文件
  --migration-file PATH  明确执行已存在于目标提交中的正向 SQL 迁移，可重复
  --deploy-task SHA      重新部署当前任务分支上已推送的确定提交
  --deploy-main SHA    仅从已合并到 origin/main 的确定提交部署
  --frontend-only      仅部署前端
  --backend-only       仅部署后端
  --rollback SHA       从指定历史提交部署应用版本，不回滚数据库迁移
  -h, --help           显示帮助
EOF
}

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) \
    || fail "当前目录不在 Git 仓库内"
cd "$repo_root"

message=''
rollback_sha=''
deploy_task_sha=''
deploy_main_sha=''
frontend_only=false
backend_only=false
declare -a task_files=()
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
        --deploy-main)
            [[ $# -ge 2 ]] || fail "--deploy-main 缺少提交 SHA"
            deploy_main_sha=$2
            shift 2
            ;;
        --deploy-task)
            [[ $# -ge 2 ]] || fail "--deploy-task 缺少提交 SHA"
            deploy_task_sha=$2
            shift 2
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
release_target_count=0
[[ -n "$rollback_sha" ]] && ((release_target_count += 1))
[[ -n "$deploy_task_sha" ]] && ((release_target_count += 1))
[[ -n "$deploy_main_sha" ]] && ((release_target_count += 1))
[[ "$release_target_count" -le 1 ]] \
    || fail "--rollback、--deploy-task 与 --deploy-main 只能指定一个"

# macOS 自带 Bash 在 nounset 模式下对空数组的长度展开不兼容；发布入口
# 必须允许“未指定迁移文件”的正常发布路径通过参数校验。
set +u
if [[ -n "$rollback_sha" ]]; then
    [[ -z "$message" && ${#task_files[@]} -eq 0 && ${#migration_files[@]} -eq 0 ]] \
        || fail "回滚模式不能同时提交新文件"
elif [[ -n "$deploy_task_sha" ]]; then
    [[ -z "$message" && ${#task_files[@]} -eq 0 && ${#migration_files[@]} -eq 0 ]] \
        || fail "--deploy-task 不能同时提交新文件"
elif [[ -n "$deploy_main_sha" ]]; then
    [[ -z "$message" && ${#task_files[@]} -eq 0 && ${#migration_files[@]} -eq 0 ]] \
        || fail "--deploy-main 不能同时提交新文件"
else
    [[ -n "$message" ]] || fail "提交发布必须提供 --message"
    [[ ${#task_files[@]} -gt 0 ]] || fail "提交发布必须至少提供一个 --file"
fi
set -u

set +u
for path in "${task_files[@]}"; do
    [[ "$path" != /* && "$path" != ../* && "$path" != */../* && "$path" != .git/* ]] \
        || fail "非法发布路径：$path"
    [[ "$path" != .env* && "$path" != */.env* && "$path" != .cursor/* && "$path" != .codex/* ]] \
        || fail "发布禁止路径：$path"
done
if [[ -n ${migration_files[*]-} ]]; then
    for path in "${migration_files[@]}"; do
        [[ "$path" != /* && "$path" != ../* && "$path" != */../* && "$path" != .git/* ]] \
            || fail "非法迁移路径：$path"
        [[ "$path" == backend/migrations/[0-9][0-9][0-9]_*.sql ]] \
            || fail "迁移文件必须使用三位编号命名且位于 backend/migrations/：$path"
    done
fi
set -u

if [[ -z "$rollback_sha" && -z "$deploy_task_sha" && -z "$deploy_main_sha" ]]; then
    git diff --cached --quiet || fail "已有暂存内容，无法确认其是否属于本次发布"

    declare -a changed_files=()
    while IFS= read -r path; do
        [[ -n "$path" ]] && changed_files+=("$path")
    done < <(
        {
            git -c core.quotePath=false diff --name-only
            # Codex 与本地协作 worktree 不属于产品发布内容；其余未跟踪文件
            # 仍必须显式列入 --file，避免误把用户文件带入发布提交。
            git -c core.quotePath=false ls-files --others --exclude-standard -- \
                . ':(exclude).codex/**' ':(exclude)worktrees/**'
        } | sort -u
    )

    for changed in "${changed_files[@]}"; do
        allowed=false
        for task_file in "${task_files[@]}"; do
            if [[ "$changed" == "$task_file" ]]; then
                allowed=true
                break
            fi
        done
        [[ "$allowed" == true ]] || fail "工作区存在未列入发布范围的变更：$changed"
    done

    for task_file in "${task_files[@]}"; do
        [[ -e "$task_file" ]] || fail "发布文件不存在：$task_file"
    done
    if [[ -n ${migration_files[*]-} ]]; then
        for migration_file in "${migration_files[@]}"; do
            [[ -e "$migration_file" ]] || fail "迁移文件不存在：$migration_file"
        done
    fi

    git add -- "${task_files[@]}"
    git diff --cached --quiet && fail "指定文件没有可提交的变更"
    git commit -m "$message"
    commit_sha=$(git rev-parse HEAD)
    branch=$(git symbolic-ref --quiet --short HEAD) \
        || fail "当前不是分支，拒绝自动推送"
    git push origin "$branch"
    info "提交并推送完成：$commit_sha"
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
    remote_sha=$(git ls-remote origin "refs/heads/$branch" | awk '{print $1}')
    [[ "$remote_sha" == "$commit_sha" ]] \
        || fail "--deploy-task 目标尚未作为当前任务分支的远端最新提交推送"
    info "已确认重新部署任务提交：$commit_sha"
elif [[ -n "$deploy_main_sha" ]]; then
    git cat-file -e "${deploy_main_sha}^{commit}" \
        || fail "--deploy-main 目标不是有效提交：$deploy_main_sha"
    origin_main_sha=$(git rev-parse origin/main 2>/dev/null) \
        || fail "无法读取 origin/main，拒绝发布"
    git merge-base --is-ancestor "$deploy_main_sha" "$origin_main_sha" \
        || fail "部署目标不是 origin/main 的已合并提交：$deploy_main_sha"
    commit_sha=$(git rev-parse "${deploy_main_sha}^{commit}")
    info "已确认从 origin/main 发布：$commit_sha"
else
    git cat-file -e "${rollback_sha}^{commit}" \
        || fail "回滚目标不是有效提交：$rollback_sha"
    commit_sha=$(git rev-parse "${rollback_sha}^{commit}")
    info "回滚目标确认：$commit_sha"
fi

release_worktree=$(mktemp -d "${TMPDIR:-/tmp}/everydayai-release.XXXXXX")
cleanup() {
    git worktree remove --force "$release_worktree" >/dev/null 2>&1 || true
}
trap cleanup EXIT

git worktree add --detach "$release_worktree" "$commit_sha" >/dev/null

# config.env 被 git 忽略，只作为本地发布运行时配置注入隔离工作树。
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
    if [[ ${#task_files[@]} -gt 0 ]]; then
        for task_file in "${task_files[@]}"; do
            case "$task_file" in
                backend/migrations/[0-9][0-9][0-9]_*.sql)
                    deploy_args+=(--migration-file "$task_file")
                    ;;
            esac
        done
    fi
    if [[ -n ${migration_files[*]-} ]]; then
        for migration_file in "${migration_files[@]}"; do
            deploy_args+=(--migration-file "$migration_file")
        done
    fi
fi
if [[ ${#deploy_args[@]} -gt 0 ]]; then
    deploy_command=(bash deploy/deploy.sh "${deploy_args[@]}")
else
    deploy_command=(bash deploy/deploy.sh)
fi
if ! "${deploy_command[@]}" >"$release_log" 2>&1; then
    echo "RELEASE_RESULT status=failed commit=$commit_sha log=$release_log" >&2
    tail -n 160 "$release_log" >&2
    exit 1
fi
tail -n 40 "$release_log"
popd >/dev/null

release_mode=normal
[[ -n "$rollback_sha" ]] && release_mode=rollback
[[ -n "$deploy_task_sha" ]] && release_mode=task
[[ -n "$deploy_main_sha" ]] && release_mode=main
echo "RELEASE_RESULT status=success commit=$commit_sha mode=$release_mode log=$release_log"
