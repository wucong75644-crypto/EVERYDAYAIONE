#!/bin/bash
# Git 快速提交推送脚本
# 用法: ./git-push.sh "提交信息"
# 或:   ./git-push.sh (将提示输入提交信息)

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查是否在 git 仓库中
if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    echo -e "${RED}错误：当前目录不是 git 仓库${NC}"
    exit 1
fi

# 显示当前状态
echo -e "${YELLOW}=== Git 状态 ===${NC}"
git status -s

# 检查是否有更改
if git diff --quiet && git diff --staged --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    echo -e "${GREEN}没有需要提交的更改${NC}"
    exit 0
fi

# 获取提交信息
if [ -n "$1" ]; then
    COMMIT_MSG="$1"
else
    echo ""
    read -p "请输入提交信息: " COMMIT_MSG
    if [ -z "$COMMIT_MSG" ]; then
        echo -e "${RED}错误：提交信息不能为空${NC}"
        exit 1
    fi
fi

# 添加所有更改
echo -e "\n${YELLOW}=== 添加更改 ===${NC}"
git add -A
git status -s

# 提交
echo -e "\n${YELLOW}=== 提交 ===${NC}"
git commit -m "$COMMIT_MSG"

# 推送
echo -e "\n${YELLOW}=== 推送到远程 ===${NC}"
git push

echo -e "\n${GREEN}✓ 完成！${NC}"
