#!/bin/bash
# 仅提交并推送当前任务明确指定的文件。

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

show_help() {
    cat <<'EOF'
用法:
  ./git-push.sh --message "feat: 描述" --file 路径 [--file 路径...]

规则:
  - 必须明确列出本任务文件，不接受 git add -A。
  - deploy/release-policy.conf 中禁止的文件永远不会提交。
  - 如果已有暂存内容、发现疑似密钥或远端校验失败，立即停止。
EOF
}

fail() {
    echo -e "${RED}错误：$1${NC}" >&2
    exit 1
}

normalize_path() {
    local candidate="$1"
    candidate="${candidate#./}"
    [[ "$candidate" != /* ]] || fail "文件必须使用仓库内相对路径: $1"
    [[ "$candidate" != ".." && "$candidate" != ../* && "$candidate" != */../* ]] \
        || fail "文件路径不能越出仓库: $1"
    printf '%s\n' "$candidate"
}

is_forbidden_path() {
    local path="$1"
    local pattern
    while IFS= read -r pattern || [[ -n "$pattern" ]]; do
        [[ -z "$pattern" || "$pattern" == \#* ]] && continue
        if [[ "$path" == $pattern ]]; then
            return 0
        fi
    done < "$POLICY_FILE"
    return 1
}

COMMIT_MSG=""
FILES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--message)
            [[ $# -ge 2 ]] || fail "--message 缺少值"
            COMMIT_MSG="$2"
            shift 2
            ;;
        --file)
            [[ $# -ge 2 ]] || fail "--file 缺少值"
            FILES+=("$(normalize_path "$2")")
            shift 2
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

[[ -n "$COMMIT_MSG" ]] || fail "必须提供 --message"
[[ "$COMMIT_MSG" =~ ^(feat|fix|refactor|docs|test|chore|perf|ci):[[:space:]].+ ]] \
    || fail "提交信息必须符合 <type>: <description>"
[[ ${#FILES[@]} -gt 0 ]] || fail "必须用 --file 明确列出本任务文件"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
    || fail "当前目录不在 Git 仓库中"
cd "$REPO_ROOT"
POLICY_FILE="$REPO_ROOT/deploy/release-policy.conf"
[[ -f "$POLICY_FILE" ]] || fail "缺少发布策略: deploy/release-policy.conf"

if ! git diff --cached --quiet; then
    fail "暂存区已有内容；为避免混入其他任务，本次提交已停止"
fi

for path in "${FILES[@]}"; do
    is_forbidden_path "$path" && fail "发布策略禁止提交: $path"
    if [[ ! -e "$path" ]] && ! git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
        fail "文件不存在且不是受版本控制的删除项: $path"
    fi
    git status --porcelain -- "$path" | grep -q . \
        || fail "指定文件没有待提交变更: $path"
done

echo -e "${YELLOW}=== 本任务提交范围 ===${NC}"
printf '  %s\n' "${FILES[@]}"
git add -- "${FILES[@]}"

while IFS= read -r staged_path; do
    [[ -z "$staged_path" ]] && continue
    is_forbidden_path "$staged_path" && fail "暂存内容命中禁止规则: $staged_path"
    object_size="$(git cat-file -s ":$staged_path" 2>/dev/null || printf '0')"
    (( object_size <= 10485760 )) || fail "文件超过 10 MiB: $staged_path"
done < <(git diff --cached --name-only --diff-filter=ACMR)

SECRET_PATTERN='-----BEGIN [A-Z ]*PRIVATE KEY-----|(API_KEY|SECRET_KEY|ACCESS_TOKEN|PASSWORD)[[:space:]]*=[[:space:]]*["'\''][^"'\'']{8,}'
secret_files="$(git grep --cached -I -l -E "$SECRET_PATTERN" -- "${FILES[@]}" 2>/dev/null || true)"
[[ -z "$secret_files" ]] || fail "疑似敏感内容，已停止提交（仅显示文件名）: $secret_files"

git diff --cached --quiet && fail "没有可提交的变更"
git commit -m "$COMMIT_MSG"

branch="$(git branch --show-current)"
[[ -n "$branch" ]] || fail "当前处于 detached HEAD，不能自动推送"
if git rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1; then
    git push
else
    git push -u origin "$branch"
fi

local_sha="$(git rev-parse HEAD)"
remote_sha="$(git ls-remote --exit-code origin "refs/heads/$branch" | awk '{print $1}')"
[[ "$local_sha" == "$remote_sha" ]] || fail "远端 SHA 与本地提交不一致"

echo -e "${GREEN}提交与推送完成: ${local_sha}${NC}"
