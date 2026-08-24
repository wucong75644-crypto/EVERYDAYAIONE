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

## 安全发布流程

正式发布统一使用 `release.sh`，不要直接运行底层 `deploy.sh`：

```bash
# 提交、推送、合并到 main，并部署生产供测试
./deploy/release.sh --message "feat: description" --file path/to/file

# 生产测试通过后，确认稳定版本、同步其他任务基座并清理当前工作树
./deploy/release.sh --merge-and-deploy
```

每次“提交部署”都会重新提交、合并 `main` 并部署，工作树不会自动删除。
此时发布的是可回退的候选版本，不会推进稳定版本，也不会修改其他任务的开发基座。
只有明确执行“清理工作树”时，流程才会确认当前生产候选提交（不重复部署）、创建生产稳定版本标签，并把该稳定 `main`
合并推送到其他干净的 `codex/task/*` 工作树；发现未提交修改、分支分叉或合并冲突时
会停止，不会覆盖其他对话。同步成功后才删除当前任务工作树和本地分支，远程分支及
Git 历史始终保留。

## 常用命令

```bash
# 正常部署（前后端都部署）
./deploy/release.sh --message "feat: description" --file path/to/file

# 仅部署前端
./deploy/release.sh --message "feat: frontend change" --file path/to/file --frontend-only

# 仅部署后端
./deploy/release.sh --message "feat: backend change" --file path/to/file --backend-only

# 仅查看底层部署参数帮助（不作为正式发布入口）
./deploy/deploy.sh --help

# 查看帮助
./deploy/release.sh --help
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

前端发布先同步带哈希的 `assets/`，再更新入口文件，并保留 14 天旧资源供已打开页面平滑过渡。Nginx 对缺失资源返回 404，只有前端页面路由才回退到 `index.html`。

---

## 支持

遇到问题？

1. 查看 [DEPLOYMENT.md](./DEPLOYMENT.md) 的"常见问题"章节
2. 检查服务日志：`ssh root@your_domain.com 'sudo journalctl -u everydayai-backend -f'`
3. 查看Nginx日志：`ssh root@your_domain.com 'sudo tail -f /var/log/nginx/everydayai-error.log'`
