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
  ./deploy/release.sh --message "type: description" --file-list /tmp/release-files.txt
  ./deploy/release.sh --rollback <commit-sha>

选项：
  --message MSG        本次发布提交信息（提交发布必填）
  --file PATH          明确纳入本次提交的文件，可重复
  --file-list PATH     从本地清单逐行读取发布文件
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
frontend_only=false
backend_only=false
declare -a task_files=()

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

if [[ -n "$rollback_sha" ]]; then
    [[ -z "$message" && ${#task_files[@]} -eq 0 ]] \
        || fail "回滚模式不能同时提交新文件"
else
    [[ -n "$message" ]] || fail "提交发布必须提供 --message"
    [[ ${#task_files[@]} -gt 0 ]] || fail "提交发布必须至少提供一个 --file"
fi

set +u
for path in "${task_files[@]}"; do
    [[ "$path" != /* && "$path" != ../* && "$path" != */../* && "$path" != .git/* ]] \
        || fail "非法发布路径：$path"
    [[ "$path" != .env* && "$path" != */.env* && "$path" != .cursor/* && "$path" != .codex/* ]] \
        || fail "发布禁止路径：$path"
done
set -u

if [[ -z "$rollback_sha" ]]; then
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

    git add -- "${task_files[@]}"
    git diff --cached --quiet && fail "指定文件没有可提交的变更"
    git commit -m "$message"
    commit_sha=$(git rev-parse HEAD)
    branch=$(git symbolic-ref --quiet --short HEAD) \
        || fail "当前不是分支，拒绝自动推送"
    git push origin "$branch"
    info "提交并推送完成：$commit_sha"
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

pushd "$release_worktree" >/dev/null
chmod +x deploy/deploy.sh
deploy_args=()
[[ "$frontend_only" == true ]] && deploy_args+=(--frontend-only)
[[ "$backend_only" == true ]] && deploy_args+=(--backend-only)
if [[ "$frontend_only" != true ]]; then
    for task_file in "${task_files[@]}"; do
        case "$task_file" in
            backend/migrations/[0-9][0-9][0-9]_*.sql)
                deploy_args+=(--migration-file "$task_file")
                ;;
        esac
    done
fi
if [[ ${#deploy_args[@]} -gt 0 ]]; then
    bash deploy/deploy.sh "${deploy_args[@]}"
else
    bash deploy/deploy.sh
fi
popd >/dev/null

release_mode=normal
[[ -n "$rollback_sha" ]] && release_mode=rollback
echo "RELEASE_RESULT status=success commit=$commit_sha mode=$release_mode"
