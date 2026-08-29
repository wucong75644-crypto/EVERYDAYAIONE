# 部署脚本

EVERYDAYAIONE 项目的一键自动部署脚本。

## 快速开始

### 1. 配置服务器信息

```bash
cd deploy

# 首次运行会生成配置模板
./deploy.sh

# 编辑配置文件
vim config.env
```

修改以下必填项：

```bash
SERVER_HOST=your_server_ip       # 服务器IP或域名
DOMAIN=your_domain.com           # 你的域名
EMAIL=your_email@example.com     # 邮箱（用于SSL证书）
```

### 2. 首次部署

```bash
# 执行首次部署（包含服务器初始化）
./deploy.sh --setup
```

### 3. 配置环境变量

```bash
# SSH到服务器
ssh root@your_domain.com

# 运行环境变量配置助手
bash /tmp/setup-env.sh
```

### 4. 验证部署

访问：`https://your_domain.com`

---

## 常用命令

```bash
# 正常部署（前后端都部署）
./deploy.sh

# 仅部署前端
./deploy.sh --frontend-only

# 仅部署后端
./deploy.sh --backend-only

# 跳过测试快速部署
./deploy.sh --skip-test

# 查看帮助
./deploy.sh --help
```

## 安全发布

正式发布使用 `release.sh`。它会校验工作区范围，提交并推送明确列出的文件，
再从该提交的隔离工作树执行完整部署；工作区存在未列入范围的变更时会直接停止。

发布入口会收集当前任务分支相对 `origin/main` 新增的所有
`backend/migrations/NNN_*.sql`（可用 `--source-only-file` 明确排除），并在重启服务前执行。
生产端用路径与 SHA-256 账本去重：失败发布后再次部署同一任务会自动补跑遗漏迁移；已应用文件不会重复执行，
同一路径内容被改写会停止发布。迁移失败时不会重启服务；应用版本回滚也不会自动回滚数据库迁移。

```bash
./release.sh --message "feat: upgrade conversation actor runtime" \
  --file backend/services/conversation_commands.py \
  --file backend/migrations/138_conversation_control_events.sql
```

应用版本需要回退时，使用历史提交重新部署；数据库迁移不随应用版本回退：

```bash
./release.sh --rollback <commit-sha>
```

### 任务发布与验收关闭

开发任务始终在独立 `codex/task/*` 工作树中完成。

- “提交部署”：提交、推送并完整部署当前任务提交，供生产测试；不会合并 `main`。
- 若当前任务没有新改动但需要再次测试同一提交，使用 `./deploy/release.sh --deploy-task <commit-sha>`；它会重新部署该已推送提交，并自动依据任务分支与迁移账本补齐遗漏的正向迁移。
- “清理工作树”：只在用户验收后执行。脚本会核验生产记录的候选提交就是当前任务，合并到 `main` 后再次核对最终代码树与该候选完全一致；一致时同步其他活跃任务的稳定基座并关闭当前任务，**不重复部署**。
- 若 `main` 在此期间出现额外改动，最终代码树不一致，脚本会停止并保留工作树。此时必须重新提交部署最终版本，不能把未测试代码标记为稳定。

```bash
# 开始新任务：自动从最新 origin/main 创建独立工作树
./scripts/task-worktree.sh start task-slug

# 验收通过后：合并、同步稳定基座、关闭；不部署
./deploy/release.sh --accept-and-close
```

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `deploy.sh` | 主部署脚本（在本地运行） |
| `setup-server.sh` | 服务器初始化脚本（自动上传到服务器运行） |
| `setup-env.sh` | 环境变量配置助手（在服务器上运行） |
| `nginx.conf` | Nginx配置模板 |
| `everydayai-backend.service` | Systemd服务配置 |
| `config.env` | 部署配置文件（需手动创建） |
| `DEPLOYMENT.md` | 完整部署文档 |

---

## 详细文档

请查看 [DEPLOYMENT.md](./DEPLOYMENT.md) 获取完整的部署指南，包括：

- 前置要求
- 详细步骤
- 常见问题
- 运维指南
- 故障排查

---

## 技术栈

- **前端**：React + Vite → Nginx静态文件服务
- **后端**：Python FastAPI → Systemd服务管理
- **代理**：Nginx反向代理
- **SSL**：Let's Encrypt自动证书
- **同步**：rsync高效文件传输

---

## 支持

遇到问题？

1. 查看 [DEPLOYMENT.md](./DEPLOYMENT.md) 的"常见问题"章节
2. 检查服务日志：`ssh root@your_domain.com 'sudo journalctl -u everydayai-backend -f'`
3. 查看Nginx日志：`ssh root@your_domain.com 'sudo tail -f /var/log/nginx/everydayai-error.log'`
