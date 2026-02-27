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
