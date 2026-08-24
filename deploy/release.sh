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
  ./deploy/release.sh --merge-and-deploy
  ./deploy/release.sh --deploy-main <commit-sha>
  ./deploy/release.sh --rollback <commit-sha>

选项：
  --message MSG        本次发布提交信息（提交发布必填）
  --file PATH          明确纳入本次提交的文件，可重复
  --file-list PATH     从本地清单逐行读取发布文件
  --merge-and-deploy   验收后确认生产版本、同步活动任务并清理工作树
  --deploy-main SHA    仅从已合并到 origin/main 的确定提交部署
  --frontend-only      仅部署前端
  --backend-only       仅部署后端
  --skip-test          跳过部署前测试
  --runtime-flags-off-install
                       仅安装关闭状态 Runtime 单元
  --runtime-control-plane-flags-off-update
                       更新三控制面 unit
  --expected-unit-manifest PATH
                       control-plane flags-off 更新的 unit 清单
  --rollback SHA       从指定历史提交部署应用版本，不回滚数据库迁移
  -h, --help           显示帮助
EOF
}

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) \
    || fail "当前目录不在 Git 仓库内"
cd "$repo_root"

confirm_production_candidate() {
    [[ -f "$repo_root/deploy/config.env" ]] \
        || fail "缺少 deploy/config.env，无法确认生产候选版本"
    # 仅加载发布连接配置；不输出配置内容。
    source "$repo_root/deploy/config.env"
    : "${SERVER_HOST:?deploy/config.env 缺少 SERVER_HOST}"
    : "${SERVER_USER:?deploy/config.env 缺少 SERVER_USER}"
    : "${SERVER_PORT:?deploy/config.env 缺少 SERVER_PORT}"
    : "${REMOTE_APP_DIR:?deploy/config.env 缺少 REMOTE_APP_DIR}"
    : "${DOMAIN:?deploy/config.env 缺少 DOMAIN}"

    local deployed_record deployed_sha deployed_scope
    deployed_sha=$(ssh -p "$SERVER_PORT" -o ConnectTimeout=10 -o BatchMode=yes \
        "$SERVER_USER@$SERVER_HOST" \
        "sudo cat '${REMOTE_APP_DIR}/.deployed-release'" 2>/dev/null) \
        || fail "无法读取生产已部署提交标记；不能确认候选版本，拒绝清理"
    deployed_record=$deployed_sha
    deployed_sha=$(printf '%s\n' "$deployed_record" | sed -n 's/^sha=//p' | tr -d '[:space:]')
    deployed_scope=$(printf '%s\n' "$deployed_record" | sed -n 's/^scope=//p' | tr -d '[:space:]')
    [[ "$deployed_sha" == "$commit_sha" ]] \
        || fail "生产已部署提交不是当前候选版本：production=$deployed_sha candidate=$commit_sha"
    [[ "$deployed_scope" == "frontend+backend" ]] \
        || fail "生产候选版本不是完整前后端发布（scope=$deployed_scope），拒绝标记稳定"

    curl --fail --silent --show-error "https://${DOMAIN}/api/health" \
        | grep -q '"status":"ok"' \
        || fail "生产健康检查未通过，拒绝确认候选版本"
    info "生产候选版本确认通过：$commit_sha"
}

message=''
rollback_sha=''
deploy_main_sha=''
merge_and_deploy=false
frontend_only=false
backend_only=false
skip_test=false
runtime_flags_off_install=false
control_plane_flags_off_update=false
expected_unit_manifest=''
scope_count=0
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
            scope_count=$((scope_count + 1))
            shift
            ;;
        --backend-only)
            backend_only=true
            scope_count=$((scope_count + 1))
            shift
            ;;
        --skip-test)
            skip_test=true
            shift
            ;;
        --runtime-flags-off-install|--runtime-control-plane-flags-off-update)
            if [[ "$1" == "--runtime-flags-off-install" ]]; then
                runtime_flags_off_install=true
            else
                control_plane_flags_off_update=true
            fi
            scope_count=$((scope_count + 1))
            shift
            ;;
        --expected-unit-manifest)
            [[ $# -ge 2 ]] || fail "--expected-unit-manifest 缺少参数"
            [[ -f "$2" && ! -L "$2" ]] \
                || fail "--expected-unit-manifest 必须指向普通文件"
            expected_unit_manifest=$(cd "$(dirname "$2")" && pwd)/$(basename "$2")
            shift 2
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
        --merge-and-deploy)
            merge_and_deploy=true
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
(( scope_count <= 1 )) || fail "不能同时选择多个部署范围"
[[ "$runtime_flags_off_install" == true && "$skip_test" == true ]] \
    && fail "--runtime-flags-off-install 不能与 --skip-test 组合"
[[ "$control_plane_flags_off_update" == true && "$skip_test" == true ]] \
    && fail "--runtime-control-plane-flags-off-update 不能与 --skip-test 组合"
[[ "$control_plane_flags_off_update" == true && -z "$expected_unit_manifest" ]] \
    && fail "control-plane flags-off update 缺少 --expected-unit-manifest"
[[ "$control_plane_flags_off_update" == false && -n "$expected_unit_manifest" ]] \
    && fail "--expected-unit-manifest 仅用于 control-plane flags-off update"

[[ -n "$rollback_sha" && -n "$deploy_main_sha" ]] \
    && fail "--rollback 与 --deploy-main 不能同时使用"
[[ "$merge_and_deploy" == true && ( -n "$rollback_sha" || -n "$deploy_main_sha" ) ]] \
    && fail "--merge-and-deploy 不能与部署或回滚目标同时使用"

if [[ "$merge_and_deploy" == true ]]; then
    [[ -z "$message" && ${#task_files[@]} -eq 0 ]] \
        || fail "--merge-and-deploy 不能同时提交新文件"
elif [[ -n "$deploy_main_sha" ]]; then
    [[ -z "$message" && ${#task_files[@]} -eq 0 ]] \
        || fail "--deploy-main 不能同时提交新文件"
elif [[ -n "$rollback_sha" ]]; then
    [[ -z "$message" && ${#task_files[@]} -eq 0 ]] \
        || fail "回滚模式不能同时提交新文件"
else
    [[ -n "$message" ]] || fail "提交任务必须提供 --message"
    [[ ${#task_files[@]} -gt 0 ]] || fail "提交任务必须至少提供一个 --file"
fi

set +u
for path in "${task_files[@]}"; do
    [[ "$path" != /* && "$path" != ../* && "$path" != */../* && "$path" != .git/* ]] \
        || fail "非法发布路径：$path"
    [[ "$path" != .env* && "$path" != */.env* && "$path" != .cursor/* && "$path" != .codex/* ]] \
        || fail "发布禁止路径：$path"
done
set -u

if [[ "$merge_and_deploy" == false && -z "$rollback_sha" && -z "$deploy_main_sha" ]]; then
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
    git push origin "$branch"
    git fetch --prune origin main "$branch" >/dev/null \
        || fail "无法同步任务分支和 origin/main，不能合并"
    local_head=$(git rev-parse HEAD)
    remote_task_head=$(git rev-parse "origin/$branch")
    [[ "$local_head" == "$remote_task_head" ]] \
        || fail "本地任务分支未与远程同步，不能合并"

    integration_worktree=$(mktemp -d "${TMPDIR:-/tmp}/everydayai-merge.XXXXXX")
    git worktree add --detach "$integration_worktree" origin/main >/dev/null
    if ! git -C "$integration_worktree" merge --no-ff --no-edit "origin/$branch"; then
        fail "任务分支与 origin/main 合并冲突，未修改 main，任务工作树保留"
    fi
    git -C "$integration_worktree" push origin HEAD:refs/heads/main \
        || fail "推送 main 失败，未完成合并，任务工作树保留"
    commit_sha=$(git -C "$integration_worktree" rev-parse HEAD)
    release_source=origin/main
    release_mode=merge
    info "任务已提交、合并到 main，开始部署生产测试：$commit_sha"
elif [[ -n "$deploy_main_sha" ]]; then
    git cat-file -e "${deploy_main_sha}^{commit}" \
        || fail "部署目标不是有效提交：$deploy_main_sha"
    git fetch --prune origin main >/dev/null \
        || fail "无法同步 origin/main，拒绝依据可能过期的生产来源发布"
    git rev-parse --verify origin/main^{commit} >/dev/null \
        || fail "本地缺少 origin/main，不能确认生产来源"
    git merge-base --is-ancestor "$deploy_main_sha" origin/main \
        || fail "部署目标不在 origin/main 历史中，拒绝发布：$deploy_main_sha"
    commit_sha=$(git rev-parse "${deploy_main_sha}^{commit}")
    release_source=origin/main
    release_mode=normal
    info "生产来源确认：$release_source -> $commit_sha"
elif [[ -n "$rollback_sha" ]]; then
    git cat-file -e "${rollback_sha}^{commit}" \
        || fail "回滚目标不是有效提交：$rollback_sha"
    git fetch --prune origin main >/dev/null \
        || fail "无法同步 origin/main，拒绝依据可能过期的回滚来源发布"
    git merge-base --is-ancestor "$rollback_sha" origin/main \
        || fail "回滚目标不在 origin/main 历史中，拒绝发布：$rollback_sha"
    commit_sha=$(git rev-parse "${rollback_sha}^{commit}")
    release_source=origin/main
    release_mode=rollback
    info "回滚目标确认：$release_source -> $commit_sha"
elif [[ "$merge_and_deploy" == true ]]; then
    branch=$(git symbolic-ref --quiet --short HEAD) \
        || fail "当前不是任务分支，不能清理工作树"
    [[ "$branch" == codex/task/* ]] \
        || fail "清理只能在 codex/task/* 工作树中执行，实际分支：$branch"
    [[ -z "$(git status --porcelain --untracked-files=all)" ]] \
        || fail "当前任务工作树不干净，不能清理"

    git fetch --prune origin main "$branch" >/dev/null \
        || fail "无法同步任务分支和 origin/main，不能确认生产候选版本"
    local_head=$(git rev-parse HEAD)
    remote_task_head=$(git rev-parse "origin/$branch")
    [[ "$local_head" == "$remote_task_head" ]] \
        || fail "本地任务分支未与远程同步，不能清理"
    commit_sha=$(git rev-parse origin/main^{commit})
    git merge-base --is-ancestor "$local_head" "$commit_sha" \
        || fail "当前任务尚未进入 origin/main，不能清理"
    release_source=origin/main
    release_mode=finalize
    info "跳过重复部署，确认已部署生产候选版本：$commit_sha"
else
    branch=$(git symbolic-ref --quiet --short HEAD) \
        || fail "当前不是任务分支，不能执行合并"
    [[ "$branch" == codex/task/* ]] \
        || fail "合并只能在 codex/task/* 工作树中执行，实际分支：$branch"
    [[ -z "$(git status --porcelain --untracked-files=all)" ]] \
        || fail "当前任务工作树不干净，不能合并"

    git fetch --prune origin main "$branch" >/dev/null \
        || fail "无法同步任务分支和 origin/main，不能合并"
    local_head=$(git rev-parse HEAD)
    remote_task_head=$(git rev-parse "origin/$branch")
    [[ "$local_head" == "$remote_task_head" ]] \
        || fail "本地任务分支未与远程同步，不能合并"

    integration_worktree=$(mktemp -d "${TMPDIR:-/tmp}/everydayai-merge.XXXXXX")
    git worktree add --detach "$integration_worktree" origin/main >/dev/null
    if ! git -C "$integration_worktree" merge --no-ff --no-edit "origin/$branch"; then
        fail "任务分支与 origin/main 合并冲突，未修改 main，任务工作树保留"
    fi
    git -C "$integration_worktree" push origin HEAD:refs/heads/main \
        || fail "推送 main 失败，未完成合并，任务工作树保留"
    commit_sha=$(git -C "$integration_worktree" rev-parse HEAD)
    release_source=origin/main
    release_mode=merge
    info "任务已合并到 main，开始按最终 main 提交部署：$commit_sha"
fi

for forbidden_path in \
    backend/services/agent/runtime \
    backend/api/routes/runtime_admin.py \
    backend/config/runtime_read_tools.py \
    backend/services/agent/runtime/composition.py; do
    git cat-file -e "${commit_sha}:$forbidden_path" 2>/dev/null \
        && fail "待发布提交包含已废弃 Runtime 平台路径：$forbidden_path"
done

if [[ "$merge_and_deploy" == true ]]; then
    confirm_production_candidate
else
    release_worktree=$(mktemp -d "${TMPDIR:-/tmp}/everydayai-release.XXXXXX")
fi
integration_worktree=${integration_worktree:-}
cleanup() {
    if [[ -n "${release_worktree:-}" ]]; then
        git worktree remove --force "$release_worktree" >/dev/null 2>&1 || true
    fi
    if [[ -n "$integration_worktree" ]]; then
        git worktree remove --force "$integration_worktree" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

if [[ "$merge_and_deploy" == false ]]; then
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
    [[ "$skip_test" == true ]] && deploy_args+=(--skip-test)
    [[ "$runtime_flags_off_install" == true ]] \
        && deploy_args+=(--runtime-flags-off-install)
    [[ "$control_plane_flags_off_update" == true ]] \
        && deploy_args+=(--runtime-control-plane-flags-off-update)
    [[ -n "$expected_unit_manifest" ]] \
        && deploy_args+=(--expected-unit-manifest "$expected_unit_manifest")
    if [[ ${#deploy_args[@]} -gt 0 ]]; then
        EVERYDAYAI_RELEASE_CONTEXT=release.sh \
        EVERYDAYAI_RELEASE_COMMIT="$commit_sha" \
        EVERYDAYAI_RELEASE_MODE="$release_mode" \
            bash deploy/deploy.sh "${deploy_args[@]}"
    else
        EVERYDAYAI_RELEASE_CONTEXT=release.sh \
        EVERYDAYAI_RELEASE_COMMIT="$commit_sha" \
        EVERYDAYAI_RELEASE_MODE="$release_mode" \
            bash deploy/deploy.sh
    fi
    popd >/dev/null
fi

if [[ "$merge_and_deploy" == true ]]; then
    # 先移除临时发布工作树，再删除当前任务工作树，避免当前目录被删除后无法清理临时树。
    if [[ -n "${release_worktree:-}" ]]; then
        git -C "$repo_root" worktree remove --force "$release_worktree" >/dev/null 2>&1 || true
    fi
    release_worktree=''
    if [[ -n "$integration_worktree" ]]; then
        git -C "$repo_root" worktree remove --force "$integration_worktree" >/dev/null 2>&1 || true
        integration_worktree=''
    fi
    ./scripts/task-worktree.sh close --current --confirm
fi

if [[ "$merge_and_deploy" == true ]]; then
    echo "RELEASE_RESULT status=success commit=$commit_sha source=$release_source mode=$release_mode status_after=TASK_CLOSED"
else
    echo "RELEASE_RESULT status=success commit=$commit_sha source=$release_source mode=$release_mode status_after=DEPLOYED_PENDING_ACCEPTANCE"
fi
