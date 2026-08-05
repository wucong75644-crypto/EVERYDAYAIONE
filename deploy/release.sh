#!/bin/bash
# 将当前任务文件提交、推送，并从该提交的隔离工作树部署。

set -euo pipefail

fail() {
    echo "错误：$1" >&2
    exit 1
}

show_help() {
    cat <<'EOF'
用法:
  ./deploy/release.sh --message "feat: 描述" --file 路径 [--file 路径...]
                      [--frontend-only|--backend-only|
                       --runtime-flags-off-install] [--skip-test]

默认部署前端和后端。正常路径不进行交互确认；任何门禁失败都会停止。
flags-off 安装路径不能与其他部署范围或 --skip-test 组合。
EOF
}

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
    || fail "当前目录不在 Git 仓库中"
COMMIT_ARGS=()
DEPLOY_ARGS=()
scope_count=0
runtime_flags_off_install=false
skip_test=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--message|--file)
            [[ $# -ge 2 ]] || fail "$1 缺少值"
            COMMIT_ARGS+=("$1" "$2")
            shift 2
            ;;
        -f|--frontend-only|-b|--backend-only|--runtime-flags-off-install)
            DEPLOY_ARGS+=("$1")
            scope_count=$((scope_count + 1))
            if [ "$1" = --runtime-flags-off-install ]; then
                runtime_flags_off_install=true
            fi
            shift
            ;;
        --skip-test)
            DEPLOY_ARGS+=("$1")
            skip_test=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            fail "未知参数: $1"
            ;;
    esac
done

(( scope_count <= 1 )) || fail "不能同时选择多个部署范围"
if [ "$runtime_flags_off_install" = true ] && [ "$skip_test" = true ]; then
    fail "--runtime-flags-off-install 不能与 --skip-test 组合"
fi

"$REPO_ROOT/git-push.sh" "${COMMIT_ARGS[@]}"
release_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
release_dir="$(mktemp -d "${TMPDIR:-/tmp}/everydayai-release.XXXXXX")"

cleanup() {
    git -C "$REPO_ROOT" worktree remove --force "$release_dir" >/dev/null 2>&1 || true
}
trap cleanup EXIT

git -C "$REPO_ROOT" worktree add --detach "$release_dir" "$release_sha"
cp "$REPO_ROOT/deploy/config.env" "$release_dir/deploy/config.env"

(
    cd "$release_dir"
    if (( ${#DEPLOY_ARGS[@]} > 0 )); then
        ./deploy/deploy.sh --expected-sha "$release_sha" "${DEPLOY_ARGS[@]}"
    else
        ./deploy/deploy.sh --expected-sha "$release_sha"
    fi
)

echo "提交部署完成: $release_sha"
