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

```bash
./release.sh --message "feat: upgrade conversation actor runtime" \
  --file backend/services/conversation_commands.py \
  --file backend/migrations/138_conversation_control_events.sql
```

应用版本需要回退时，使用历史提交重新部署；数据库迁移不随应用版本回退：

```bash
./release.sh --rollback <commit-sha>
```

正常后端发布会在本地前后端构建和测试全部通过后才同步线上。后端同步完成后，
若 `deploy/config.env` 中 `RUN_MIGRATIONS=true`，会按顺序将本次 Actor 的
138、139、140 三份正向迁移放在同一个 PostgreSQL 事务中执行，成功后才重启后端服务。
迁移失败会停止发布，不执行服务重启。

后端本地构建默认优先使用 `python3.12`，其次是 `python3.11`，也可以显式指定：

```bash
EVERYDAYAI_BUILD_PYTHON=/path/to/python3.12 ./deploy/deploy.sh --backend-only
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
