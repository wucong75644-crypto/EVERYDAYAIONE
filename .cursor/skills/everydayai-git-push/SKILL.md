---
name: "everydayai-git-push"
description: "当用户说\"推送代码\"、\"push\"、\"提交推送\"、\"推到git\"等需要提交并推送代码到远程仓库时,执行Git提交推送流程"
---

# Git 提交推送

> **触发**：用户说"推送代码""push""提交推送""推到git"

## 执行流程

### 1. 检查当前状态
```bash
git status
git diff --stat
```
确认有需要提交的更改，向用户展示改动概览。

### 2. 生成 Commit Message
根据本次改动内容，按 Conventional Commits 格式生成提交信息：
- `feat:` 新功能
- `fix:` 修复
- `refactor:` 重构
- `docs:` 文档
- `test:` 测试
- `chore:` 杂项
- `perf:` 性能

### 3. 提交并推送
```bash
cd /Users/wucong/EVERYDAYAIONE && ./git-push.sh "生成的提交信息"
```

### 4. 输出报告
```
✅ 代码已推送到远程仓库

**提交信息**：feat: xxx
**分支**：main
**改动文件**：X个
```
