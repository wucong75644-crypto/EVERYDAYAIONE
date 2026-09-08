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
fake_production="$tmp_root/production"

write_test_config() {
    local destination=$1
    {
        printf '%s\n' 'SERVER_HOST=example.invalid' 'SERVER_USER=test' 'SERVER_PORT=22'
        printf 'REMOTE_APP_DIR=%q\n' "$fake_production"
    } > "$destination"
}

run git init --bare "$remote"
run git init "$seed"
run git -C "$seed" config user.name lifecycle-test
run git -C "$seed" config user.email lifecycle-test@example.invalid
mkdir -p "$seed/deploy" "$seed/scripts/testing"
cp "$source_root/deploy/release.sh" "$seed/deploy/release.sh"
cp "$source_root/deploy/release-coordination.sh" "$seed/deploy/release-coordination.sh"
cp "$source_root/scripts/task-worktree.sh" "$seed/scripts/task-worktree.sh"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "%s\\n" "$@" > "${DEPLOY_ARGS_FILE:?}"' > "$seed/deploy/deploy.sh"
chmod +x "$seed/deploy/release.sh" "$seed/deploy/deploy.sh" "$seed/scripts/task-worktree.sh"
printf 'base\n' > "$seed/product.txt"
printf 'other-base\n' > "$seed/other-product.txt"
printf 'deploy/config.env\n' > "$seed/.gitignore"
run git -C "$seed" add deploy/release.sh deploy/release-coordination.sh deploy/deploy.sh scripts/task-worktree.sh product.txt other-product.txt .gitignore
run git -C "$seed" commit -m base
run git -C "$seed" branch -M main
run git -C "$seed" remote add origin "$remote"
run git -C "$seed" push -u origin main
run git --git-dir="$remote" symbolic-ref HEAD refs/heads/main
run git clone "$remote" "$root"
run git -C "$root" config user.name lifecycle-test
run git -C "$root" config user.email lifecycle-test@example.invalid

missing="$tmp_root/missing-config"
if (
    cd "$root"
    ./scripts/task-worktree.sh start missing-config --path "$missing"
) > "$tmp_root/missing-config.log" 2>&1; then
    fail "主工作树缺少 deploy/config.env 时仍创建任务工作树"
fi
grep -F "主工作树缺少 deploy/config.env" "$tmp_root/missing-config.log" >/dev/null \
    || fail "缺少 deploy/config.env 时未提供创建失败说明"
[[ ! -e "$missing" ]] || fail "缺少 deploy/config.env 时创建了任务工作树"

write_test_config "$root/deploy/config.env"
chmod 600 "$root/deploy/config.env"

run_in "$root" ./scripts/task-worktree.sh start other --path "$other"
run_in "$root" ./scripts/task-worktree.sh start candidate --path "$candidate"
[[ -f "$other/deploy/config.env" && -f "$candidate/deploy/config.env" ]] \
    || fail "任务工作树未复制 deploy/config.env"
cmp -s "$root/deploy/config.env" "$other/deploy/config.env" \
    || fail "任务工作树 deploy/config.env 内容与主工作树不一致"
[[ "$(stat -f '%Lp' "$other/deploy/config.env")" == 600 ]] \
    || fail "任务工作树 deploy/config.env 权限不是 600"
[[ -z "$(git -C "$other" status --porcelain --untracked-files=all)" ]] \
    || fail "deploy/config.env 出现在任务工作树提交文件清单中"

# A 验收时 B 正在开发：同时保留已暂存、未暂存和未跟踪内容。
printf 'other-staged\n' >> "$other/other-product.txt"
run git -C "$other" add other-product.txt
printf 'other-unstaged\n' >> "$other/other-product.txt"
printf 'other-untracked\n' > "$other/other-new.txt"
other_head=$(git -C "$other" rev-parse HEAD)
other_index=$(git -C "$other" write-tree)
git -C "$other" status --porcelain --untracked-files=all > "$tmp_root/other-status-before"
git -C "$other" diff --binary > "$tmp_root/other-diff-before"
git -C "$other" diff --cached --binary > "$tmp_root/other-cached-before"
cp "$other/other-product.txt" "$tmp_root/other-product-before"
cp "$other/other-new.txt" "$tmp_root/other-new-before"
mkdir -p "$candidate/backend/migrations"
printf 'candidate\n' >> "$candidate/product.txt"
printf '%s\n' 'SELECT 1;' > "$candidate/backend/migrations/242_delivery_outbox.sql"
run git -C "$candidate" add product.txt backend/migrations/242_delivery_outbox.sql
run git -C "$candidate" commit -m candidate
run git -C "$candidate" push origin HEAD
candidate_sha=$(git -C "$candidate" rev-parse HEAD)

# 候选分支创建后 main 前进；发布必须自动合入最新 main，而不是覆盖它。
printf 'main-before-candidate-deploy\n' > "$root/main-before-candidate-deploy.txt"
run git -C "$root" add main-before-candidate-deploy.txt
run git -C "$root" commit -m main-before-candidate-deploy
run git -C "$root" push origin main
main_before_candidate_deploy=$(git -C "$root" rev-parse HEAD)

mkdir -p "$fake_bin" "$fake_production"
cat > "$fake_bin/ssh" <<'FAKE_SSH'
#!/usr/bin/env python3
import os
import shlex
import subprocess
import sys

command = shlex.split(sys.argv[-1])
if (sys.argv[-2] != "test@example.invalid"
        or command[:3] != ["bash", "-s", "--"]
        or command[3] != os.environ["FAKE_PRODUCTION"]):
    raise SystemExit("拒绝测试目录以外的 SSH 请求")
raise SystemExit(subprocess.run(command).returncode)
FAKE_SSH
chmod +x "$fake_bin/ssh"
export PATH="$fake_bin:$PATH"
export FAKE_PRODUCTION="$fake_production"

migration_deploy_args="$tmp_root/migration-deploy-args.txt"
(
    cd "$candidate"
    DEPLOY_ARGS_FILE="$migration_deploy_args" \
        ./deploy/release.sh --deploy-task "$candidate_sha" \
        --migration-file backend/migrations/242_delivery_outbox.sql
) > "$tmp_root/migration-retry.log"
candidate_sha=$(git -C "$candidate" rev-parse HEAD)
git -C "$candidate" merge-base --is-ancestor "$main_before_candidate_deploy" "$candidate_sha" \
    || fail "任务部署前未自动同步最新 main"
rg -Fx -- '--migration-file' "$migration_deploy_args" >/dev/null \
    || fail "任务重试未将显式迁移转发给部署脚本"
rg -Fx -- 'backend/migrations/242_delivery_outbox.sql' "$migration_deploy_args" >/dev/null \
    || fail "任务重试转发的迁移路径不正确"

(
    cd "$candidate"
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
[[ "$(git -C "$other" rev-parse HEAD)" == "$other_head" ]] \
    || fail "同步稳定基座改变了 B 的 HEAD"
[[ "$(git -C "$other" write-tree)" == "$other_index" ]] \
    || fail "同步稳定基座改变了 B 的暂存区"
git -C "$other" status --porcelain --untracked-files=all > "$tmp_root/other-status-after"
git -C "$other" diff --binary > "$tmp_root/other-diff-after"
git -C "$other" diff --cached --binary > "$tmp_root/other-cached-after"
for part in status diff cached; do
    cmp -s "$tmp_root/other-$part-before" "$tmp_root/other-$part-after" \
        || fail "同步稳定基座改变了 B 的 $part"
done
cmp -s "$other/other-product.txt" "$tmp_root/other-product-before" \
    || fail "同步稳定基座改变了 B 的跟踪文件内容"
cmp -s "$other/other-new.txt" "$tmp_root/other-new-before" \
    || fail "同步稳定基座改变了 B 的未跟踪文件内容"

# B 后续发布才合入 A 的稳定成果。暂存保护先拒绝，不能代用户清理。
if (
    cd "$other"
    ./deploy/release.sh --message other-task --file other-product.txt --file other-new.txt
) > "$tmp_root/other-staged-reject.log" 2>&1; then
    fail "B 有预先暂存内容时仍继续发布"
fi
[[ "$(git -C "$other" write-tree)" == "$other_index" ]] \
    || fail "拒绝发布时改变了 B 的暂存区"
# 只取消临时测试仓库内由本测试创建的暂存，不改文件内容。
run git -C "$other" reset -- other-product.txt
(
    cd "$other"
    DEPLOY_ARGS_FILE="$tmp_root/other-deploy-args.txt" \
        ./deploy/release.sh --message other-task --file other-product.txt --file other-new.txt
) > "$tmp_root/other-deploy.log"
other_deployed_sha=$(git -C "$other" rev-parse HEAD)
git -C "$other" merge-base --is-ancestor "$stable_main" "$other_deployed_sha" \
    || fail "B 发布未合入 A 的稳定成果"
cmp -s "$other/other-product.txt" "$tmp_root/other-product-before" \
    || fail "B 发布合入稳定基座后丢失原有修改"
cmp -s "$other/other-new.txt" "$tmp_root/other-new-before" \
    || fail "B 发布合入稳定基座后丢失原未跟踪文件"
[[ "$(git -C "$root" ls-remote origin refs/heads/main | awk '{print $1}')" == "$stable_main" ]] \
    || fail "B 发布提前改变了 main"
[[ -d "$other" ]] || fail "B 发布后被提前清理"

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
# 构造已测试候选，但 main 已额外前进的生产状态。
printf 'commit=%s\n' "$mismatch_sha" > "$fake_production/.release-provenance"

if (
    cd "$mismatch"
    ./deploy/release.sh --accept-and-close
) > "$tmp_root/reject.log" 2>&1; then
    fail "包含未测试 main 变更时仍允许清理"
fi
[[ -d "$mismatch" ]] || fail "树不一致时错误清理了任务工作树"
[[ "$(git -C "$root" rev-parse origin/main)" == "$main_before_rejection" ]] \
    || fail "树不一致时错误更新了 main"

conflict="$tmp_root/conflict"
run_in "$root" ./scripts/task-worktree.sh start conflict --path "$conflict"
printf 'task-conflict\n' > "$conflict/product.txt"
run git -C "$conflict" add product.txt
run git -C "$conflict" commit -m task-conflict
run git -C "$conflict" push origin HEAD
conflict_sha=$(git -C "$conflict" rev-parse HEAD)

printf 'main-conflict\n' > "$root/product.txt"
run git -C "$root" add product.txt
run git -C "$root" commit -m main-conflict
run git -C "$root" push origin main

if (
    cd "$conflict"
    ./deploy/release.sh --deploy-task "$conflict_sha"
) > "$tmp_root/conflict.log" 2>&1; then
    fail "自动同步 main 冲突时仍继续部署"
fi
grep -F "自动同步最新 main 出现冲突" "$tmp_root/conflict.log" >/dev/null \
    || fail "自动同步冲突时未提供恢复说明"
git -C "$conflict" diff --name-only --diff-filter=U | grep -Fx product.txt >/dev/null \
    || fail "自动同步冲突后未保留给任务处理的冲突现场"

normal="$tmp_root/normal"
run_in "$root" ./scripts/task-worktree.sh start normal --path "$normal"
printf 'normal-task\n' > "$normal/normal-task.txt"
printf 'main-before-normal-deploy\n' > "$root/main-before-normal-deploy.txt"
run git -C "$root" add main-before-normal-deploy.txt
run git -C "$root" commit -m main-before-normal-deploy
run git -C "$root" push origin main
main_before_normal_deploy=$(git -C "$root" rev-parse HEAD)
(
    cd "$normal"
    DEPLOY_ARGS_FILE="$tmp_root/normal-deploy-args.txt" \
        ./deploy/release.sh --message normal-task --file normal-task.txt
) > "$tmp_root/normal-deploy.log"
normal_sha=$(git -C "$normal" rev-parse HEAD)
git -C "$normal" merge-base --is-ancestor "$main_before_normal_deploy" "$normal_sha" \
    || fail "常规提交部署前未自动同步最新 main"
[[ "$(git -C "$normal" ls-remote origin "refs/heads/$(git -C "$normal" branch --show-current)" | awk '{print $1}')" == "$normal_sha" ]] \
    || fail "常规提交部署未推送同步后的任务提交"

echo "release acceptance lifecycle tests passed"
