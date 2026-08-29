#!/usr/bin/env bash

# 本测试只在临时本地 Git remote 和伪 SSH 中验证发布生命周期；不会访问生产。

set -euo pipefail

source_root=$(cd "$(dirname "$0")/../.." && pwd -P)
tmp_root=$(mktemp -d "${TMPDIR:-/tmp}/everydayai-release-lifecycle-test.XXXXXX")

cleanup() {
    if [[ "${KEEP_TEST_ARTIFACTS:-}" == true ]]; then
        printf 'test_artifacts=%s\n' "$tmp_root" >&2
        return
    fi
    rm -rf "$tmp_root"
}
trap cleanup EXIT

fail() {
    echo "TEST_FAILED: $1" >&2
    exit 1
}

run() {
    "$@" >/dev/null
}

run_in() {
    local directory=$1
    shift
    (
        cd "$directory"
        "$@"
    ) >/dev/null
}

remote="$tmp_root/remote.git"
seed="$tmp_root/seed"
root="$tmp_root/root"
other="$tmp_root/other"
candidate="$tmp_root/candidate"
mismatch="$tmp_root/mismatch"
fake_bin="$tmp_root/bin"

run git init --bare "$remote"
run git init "$seed"
run git -C "$seed" config user.name lifecycle-test
run git -C "$seed" config user.email lifecycle-test@example.invalid
mkdir -p "$seed/deploy" "$seed/scripts/testing"
cp "$source_root/deploy/release.sh" "$seed/deploy/release.sh"
cp "$source_root/scripts/task-worktree.sh" "$seed/scripts/task-worktree.sh"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "%s\\n" "$@" > "${DEPLOY_ARGS_FILE:?}"' > "$seed/deploy/deploy.sh"
chmod +x "$seed/deploy/release.sh" "$seed/deploy/deploy.sh" "$seed/scripts/task-worktree.sh"
printf 'base\n' > "$seed/product.txt"
printf 'deploy/config.env\n' > "$seed/.gitignore"
run git -C "$seed" add deploy/release.sh deploy/deploy.sh scripts/task-worktree.sh product.txt .gitignore
run git -C "$seed" commit -m base
run git -C "$seed" branch -M main
run git -C "$seed" remote add origin "$remote"
run git -C "$seed" push -u origin main
run git --git-dir="$remote" symbolic-ref HEAD refs/heads/main
run git clone "$remote" "$root"
run git -C "$root" config user.name lifecycle-test
run git -C "$root" config user.email lifecycle-test@example.invalid

run_in "$root" ./scripts/task-worktree.sh start other --path "$other"
run_in "$root" ./scripts/task-worktree.sh start candidate --path "$candidate"
mkdir -p "$candidate/backend/migrations"
printf 'candidate\n' >> "$candidate/product.txt"
printf '%s\n' 'SELECT 1;' > "$candidate/backend/migrations/242_delivery_outbox.sql"
run git -C "$candidate" add product.txt backend/migrations/242_delivery_outbox.sql
run git -C "$candidate" commit -m candidate
run git -C "$candidate" push origin HEAD
candidate_sha=$(git -C "$candidate" rev-parse HEAD)

mkdir -p "$fake_bin"
printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\\n" "$FAKE_DEPLOYED_COMMIT"' > "$fake_bin/ssh"
chmod +x "$fake_bin/ssh"
printf '%s\n' \
    'SERVER_HOST=example.invalid' \
    'SERVER_USER=test' \
    'SERVER_PORT=22' \
    'REMOTE_APP_DIR=/tmp/everydayai' > "$candidate/deploy/config.env"

migration_deploy_args="$tmp_root/migration-deploy-args.txt"
(
    cd "$candidate"
    DEPLOY_ARGS_FILE="$migration_deploy_args" \
        ./deploy/release.sh --deploy-task "$candidate_sha" \
        --migration-file backend/migrations/242_delivery_outbox.sql
) > "$tmp_root/migration-retry.log"
rg -Fx -- '--migration-file' "$migration_deploy_args" >/dev/null \
    || fail "任务重试未将显式迁移转发给部署脚本"
rg -Fx -- 'backend/migrations/242_delivery_outbox.sql' "$migration_deploy_args" >/dev/null \
    || fail "任务重试转发的迁移路径不正确"

(
    cd "$candidate"
    PATH="$fake_bin:$PATH" FAKE_DEPLOYED_COMMIT="$candidate_sha" \
        ./deploy/release.sh --accept-and-close
) > "$tmp_root/accept.log"

stable_main=$(git -C "$root" ls-remote origin refs/heads/main | awk '{print $1}')
candidate_tree=$(git -C "$root" rev-parse "${candidate_sha}^{tree}")
stable_tree=$(git -C "$root" rev-parse "${stable_main}^{tree}")
[[ "$candidate_tree" == "$stable_tree" ]] \
    || fail "验收合并后的 main 不等于已部署候选树"
[[ ! -e "$candidate" ]] || fail "成功验收后候选工作树未清理"
[[ "$(git -C "$other" config --worktree --get codex.taskStableBase)" == "$stable_main" ]] \
    || fail "其他活跃任务未同步最新稳定基座"
[[ -z "$(git -C "$other" status --porcelain)" ]] \
    || fail "同步稳定基座改动了其他任务代码"

run_in "$root" ./scripts/task-worktree.sh start mismatch --path "$mismatch"
printf 'candidate-two\n' >> "$mismatch/product.txt"
run git -C "$mismatch" add product.txt
run git -C "$mismatch" commit -m candidate-two
run git -C "$mismatch" push origin HEAD
mismatch_sha=$(git -C "$mismatch" rev-parse HEAD)

run git -C "$root" pull --ff-only
printf 'main-only\n' > "$root/main-only.txt"
run git -C "$root" add main-only.txt
run git -C "$root" commit -m main-change
run git -C "$root" push origin main
main_before_rejection=$(git -C "$root" rev-parse origin/main)
printf '%s\n' \
    'SERVER_HOST=example.invalid' \
    'SERVER_USER=test' \
    'SERVER_PORT=22' \
    'REMOTE_APP_DIR=/tmp/everydayai' > "$mismatch/deploy/config.env"

if (
    cd "$mismatch"
    PATH="$fake_bin:$PATH" FAKE_DEPLOYED_COMMIT="$mismatch_sha" \
        ./deploy/release.sh --accept-and-close
) > "$tmp_root/reject.log" 2>&1; then
    fail "包含未测试 main 变更时仍允许清理"
fi
[[ -d "$mismatch" ]] || fail "树不一致时错误清理了任务工作树"
[[ "$(git -C "$root" rev-parse origin/main)" == "$main_before_rejection" ]] \
    || fail "树不一致时错误更新了 main"

echo "release acceptance lifecycle tests passed"
