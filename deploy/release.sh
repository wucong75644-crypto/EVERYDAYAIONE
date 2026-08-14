#!/bin/bash
# 将当前任务文件提交、推送，并从该提交的隔离工作树部署。

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

fail() {
    echo "错误：$1" >&2
    exit 1
}

show_help() {
    cat <<'EOF'
用法:
  ./deploy/release.sh --message "feat: 描述" --file 路径 [--file 路径...]
                      [--frontend-only|--backend-only|
                       --runtime-flags-off-install|
                       --runtime-control-plane-flags-off-update \
                         --expected-unit-manifest PATH] [--skip-test]

默认部署前端和后端。正常路径不进行交互确认；任何门禁失败都会停止。
flags-off 安装/更新路径不能与其他部署范围或 --skip-test 组合。
EOF
}

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
    || fail "当前目录不在 Git 仓库中"
source "$REPO_ROOT/deploy/deploy-helpers.sh"
init_deploy_log
COMMIT_ARGS=()
DEPLOY_ARGS=()
scope_count=0
runtime_flags_off_install=false
control_plane_flags_off_update=false
expected_unit_manifest=
skip_test=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--message|--file)
            [[ $# -ge 2 ]] || fail "$1 缺少值"
            COMMIT_ARGS+=("$1" "$2")
            shift 2
            ;;
        -f|--frontend-only|-b|--backend-only|--runtime-flags-off-install|--runtime-control-plane-flags-off-update)
            DEPLOY_ARGS+=("$1")
            scope_count=$((scope_count + 1))
            if [ "$1" = --runtime-flags-off-install ]; then
                runtime_flags_off_install=true
            elif [ "$1" = --runtime-control-plane-flags-off-update ]; then
                control_plane_flags_off_update=true
            fi
            shift
            ;;
        --expected-unit-manifest)
            [[ $# -ge 2 ]] || fail "$1 缺少值"
            [ -z "$expected_unit_manifest" ] || fail "$1 不能重复"
            [ -f "$2" ] && [ ! -L "$2" ] || fail "$1 必须指向普通文件"
            expected_unit_manifest="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
            DEPLOY_ARGS+=("$1" "$expected_unit_manifest")
            shift 2
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
if [ "$control_plane_flags_off_update" = true ] && [ "$skip_test" = true ]; then
    fail "--runtime-control-plane-flags-off-update 不能与 --skip-test 组合"
fi
if [ "$control_plane_flags_off_update" = true ] && [ -z "$expected_unit_manifest" ]; then
    fail "control-plane flags-off update 缺少 --expected-unit-manifest"
fi
if [ "$control_plane_flags_off_update" = false ] && [ -n "$expected_unit_manifest" ]; then
    fail "--expected-unit-manifest 仅用于 control-plane flags-off update"
fi

run_stage "提交并推送任务文件" "$REPO_ROOT/git-push.sh" "${COMMIT_ARGS[@]}"
release_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
release_dir="$(mktemp -d "${TMPDIR:-/tmp}/everydayai-release.XXXXXX")"

cleanup() {
    git -C "$REPO_ROOT" worktree remove --force "$release_dir" >/dev/null 2>&1 || true
}
trap cleanup EXIT

run_stage "创建隔离发布工作树" \
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

echo "RELEASE_RESULT sha=$release_sha status=passed log=$DEPLOY_LOG_FILE"
