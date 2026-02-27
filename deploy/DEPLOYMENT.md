# EVERYDAYAIONE 部署文档

> 自动化部署脚本使用指南

## 目录

- [前置要求](#前置要求)
- [快速开始](#快速开始)
- [详细步骤](#详细步骤)
- [常见问题](#常见问题)
- [运维指南](#运维指南)

---

## 前置要求

### 本地环境

- macOS 或 Linux 系统
- 已安装 `rsync` 和 `ssh`
- 已配置 SSH 密钥到服务器（推荐）

### 服务器要求

- **操作系统**：Ubuntu 20.04/22.04 或 CentOS 7/8
- **内存**：至少 2GB RAM（推荐 4GB+）
- **磁盘**：至少 20GB 可用空间
- **网络**：开放 22（SSH）、80（HTTP）、443（HTTPS）端口
- **域名**：已解析到服务器IP（用于SSL证书）

---

## 快速开始

### 1. 配置部署参数

```bash
cd deploy

# 首次运行会自动生成配置模板
./deploy.sh

# 编辑配置文件
vim config.env
```

**必填配置项**：

```bash
# 服务器配置
SERVER_HOST=your_server_ip_or_domain
SERVER_USER=root
SERVER_PORT=22

# 域名配置
DOMAIN=your_domain.com
EMAIL=your_email@example.com
```

### 2. 首次部署（包含服务器初始化）

```bash
# 执行首次部署
./deploy.sh --setup

# 脚本会自动完成：
# ✓ 安装 Python 3.10、Node.js 20、Nginx
# ✓ 配置防火墙和系统参数
# ✓ 配置 Nginx 反向代理
# ✓ 申请 Let's Encrypt SSL 证书
# ✓ 配置 systemd 服务
# ✓ 部署前后端代码
```

### 3. 配置环境变量

```bash
# SSH 到服务器
ssh root@your_domain.com

# 运行环境变量配置助手
bash /tmp/setup-env.sh

# 或手动编辑
vim /var/www/everydayai/backend/.env
```

### 4. 验证部署

访问：`https://your_domain.com`

查看服务状态：

```bash
ssh root@your_domain.com

# 后端服务状态
sudo systemctl status everydayai-backend

# Nginx状态
sudo systemctl status nginx

# 查看日志
sudo journalctl -u everydayai-backend -f
```

---

## 详细步骤

### 步骤 1：准备工作

#### 1.1 配置 SSH 密钥（推荐）

```bash
# 本地生成密钥对（如果没有）
ssh-keygen -t ed25519 -C "your_email@example.com"

# 复制公钥到服务器
ssh-copy-id -p 22 root@your_server_ip

# 测试连接
ssh root@your_server_ip
```

#### 1.2 域名解析

在域名服务商控制台添加 A 记录：

```
类型: A
主机记录: @ 或 www
记录值: 你的服务器IP
TTL: 600
```

验证解析：

```bash
ping your_domain.com
```

---

### 步骤 2：配置部署脚本

#### 2.1 创建配置文件

```bash
cd /path/to/EVERYDAYAIONE/deploy

# 首次运行会自动生成 config.env
./deploy.sh

# 编辑配置
vim config.env
```

#### 2.2 配置说明

```bash
# 服务器配置
SERVER_HOST=example.com           # 服务器域名或IP
SERVER_USER=root                  # SSH用户（建议root，后续可改为deploy用户）
SERVER_PORT=22                    # SSH端口

# 部署路径（默认即可）
REMOTE_APP_DIR=/var/www/everydayai
REMOTE_FRONTEND_DIR=/var/www/everydayai/frontend
REMOTE_BACKEND_DIR=/var/www/everydayai/backend

# 域名配置
DOMAIN=example.com                # 你的域名
EMAIL=admin@example.com           # Let's Encrypt 通知邮箱

# 服务配置
BACKEND_PORT=8000                 # 后端API端口
FRONTEND_PORT=3000                # 前端开发端口（生产环境不用）

# 数据库迁移
RUN_MIGRATIONS=true               # 是否自动运行数据库迁移
```

---

### 步骤 3：首次部署

#### 3.1 执行初始化部署

```bash
cd deploy

# 首次部署（包含服务器初始化）
./deploy.sh --setup
```

**执行流程**：

1. ✓ 检查本地依赖（rsync、ssh）
2. ✓ 测试 SSH 连接
3. ✓ 上传初始化脚本到服务器
4. ✓ 在服务器上执行初始化：
   - 安装 Python 3.10、Node.js 20、Nginx
   - 配置防火墙（开放 80、443、22 端口）
   - 创建应用目录
   - 配置 Nginx 反向代理
   - 申请 SSL 证书（Let's Encrypt）
   - 配置 systemd 服务
   - 优化系统参数
5. ✓ 构建前端（npm run build）
6. ✓ 检查后端（pytest）
7. ✓ 同步文件到服务器（rsync）
8. ✓ 在服务器上部署应用
9. ✓ 重启服务

#### 3.2 查看部署结果

```bash
# 部署完成后会显示：
========== 部署完成 ==========
[INFO] 前端访问地址: https://example.com
[INFO] 后端API地址: https://example.com/api
[INFO] 查看实时日志: ssh root@example.com 'sudo journalctl -u everydayai-backend -f'
```

---

### 步骤 4：配置环境变量

#### 4.1 使用交互式配置（推荐）

```bash
# SSH 到服务器
ssh root@your_domain.com

# 运行配置助手
bash /tmp/setup-env.sh
```

按提示输入各项配置：

- Supabase URL、Anon Key、Service Role Key
- Redis 连接信息
- 阿里云短信配置（可选）
- 阿里云 OSS 配置（可选）
- KIE API Key

#### 4.2 手动配置

```bash
# 复制模板
cp /var/www/everydayai/backend/.env.example /var/www/everydayai/backend/.env

# 编辑配置
vim /var/www/everydayai/backend/.env
```

**必填环境变量**：

```ini
# Supabase（必填）
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# JWT（必填，建议随机生成）
JWT_SECRET_KEY=your-jwt-secret-key-at-least-32-characters
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Redis（必填）
REDIS_HOST=your-redis-host
REDIS_PORT=6379
REDIS_PASSWORD=your-redis-password
REDIS_DB=0
REDIS_SSL=true

# KIE API（必填）
KIE_API_KEY=your-kie-api-key
KIE_BASE_URL=https://api.kie.ai/v1
```

#### 4.3 重启服务

```bash
sudo systemctl restart everydayai-backend
sudo systemctl status everydayai-backend
```

---

### 步骤 5：数据库迁移（可选）

```bash
# SSH 到服务器
ssh root@your_domain.com

cd /var/www/everydayai

# 手动运行迁移脚本（如果有）
# 根据 docs/database/migrations/ 目录中的 SQL 文件
# 在 Supabase 控制台的 SQL Editor 中执行
```

---

## 部署命令参考

### 基本命令

```bash
# 首次部署（包含服务器初始化）
./deploy.sh --setup

# 正常部署（前后端都部署）
./deploy.sh

# 仅部署前端
./deploy.sh --frontend-only

# 仅部署后端
./deploy.sh --backend-only

# 跳过构建（使用已有构建）
./deploy.sh --skip-build

# 跳过测试
./deploy.sh --skip-test

# 显示帮助
./deploy.sh --help
```

### 组合使用

```bash
# 快速部署前端（跳过构建和测试）
./deploy.sh -f --skip-build --skip-test

# 仅部署后端且跳过测试
./deploy.sh -b --skip-test
```

---

## 常见问题

### 1. SSH 连接失败

**问题**：`SSH连接失败，请检查...`

**解决方案**：

```bash
# 检查 SSH 服务是否运行
ssh -p 22 root@your_server_ip "echo 'SSH OK'"

# 检查防火墙
# 阿里云ECS：在控制台安全组规则中开放 22 端口

# 配置 SSH 密钥
ssh-copy-id -p 22 root@your_server_ip
```

---

### 2. SSL 证书申请失败

**问题**：`certbot --nginx` 失败

**原因**：

- 域名未正确解析到服务器
- 80 端口未开放
- Nginx 配置错误

**解决方案**：

```bash
# 1. 验证域名解析
ping your_domain.com

# 2. 检查 80 端口
curl http://your_domain.com

# 3. 手动申请证书
sudo certbot --nginx -d your_domain.com --dry-run  # 测试
sudo certbot --nginx -d your_domain.com             # 正式申请
```

---

### 3. 后端服务启动失败

**问题**：`everydayai-backend` 服务无法启动

**排查步骤**：

```bash
# 1. 查看服务状态
sudo systemctl status everydayai-backend

# 2. 查看详细日志
sudo journalctl -u everydayai-backend -n 50 --no-pager

# 3. 检查 .env 文件
cat /var/www/everydayai/backend/.env

# 4. 检查 Python 环境
cd /var/www/everydayai/backend
source venv/bin/activate
python3 -c "import main"

# 5. 手动启动测试
cd /var/www/everydayai/backend
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

### 4. 前端页面 404

**问题**：访问 `https://your_domain.com` 显示 404

**排查步骤**：

```bash
# 1. 检查前端文件是否存在
ssh root@your_domain.com
ls -lh /var/www/everydayai/frontend/

# 2. 检查 Nginx 配置
sudo nginx -t
sudo systemctl status nginx

# 3. 查看 Nginx 日志
sudo tail -f /var/log/nginx/everydayai-error.log

# 4. 重新部署前端
./deploy.sh --frontend-only
```

---

### 5. API 请求 502 错误

**问题**：前端加载但 API 请求返回 502

**原因**：后端服务未运行或连接失败

**解决方案**：

```bash
# 1. 检查后端服务
sudo systemctl status everydayai-backend

# 2. 检查端口监听
sudo netstat -tlnp | grep 8000

# 3. 测试后端连接
curl http://localhost:8000/api/health

# 4. 重启后端服务
sudo systemctl restart everydayai-backend
```

---

## 运维指南

### 日常部署

```bash
# 正常部署（推荐）
./deploy.sh

# 快速部署前端
./deploy.sh -f

# 快速部署后端
./deploy.sh -b
```

---

### 查看日志

```bash
# 实时查看后端日志
ssh root@your_domain.com 'sudo journalctl -u everydayai-backend -f'

# 查看最近 100 行日志
ssh root@your_domain.com 'sudo journalctl -u everydayai-backend -n 100'

# 查看 Nginx 错误日志
ssh root@your_domain.com 'sudo tail -f /var/log/nginx/everydayai-error.log'

# 查看 Nginx 访问日志
ssh root@your_domain.com 'sudo tail -f /var/log/nginx/everydayai-access.log'
```

---

### 服务管理

```bash
# SSH 到服务器
ssh root@your_domain.com

# 后端服务
sudo systemctl start everydayai-backend    # 启动
sudo systemctl stop everydayai-backend     # 停止
sudo systemctl restart everydayai-backend  # 重启
sudo systemctl status everydayai-backend   # 状态

# Nginx
sudo systemctl reload nginx                # 重载配置
sudo systemctl restart nginx               # 重启
sudo systemctl status nginx                # 状态
```

---

### 备份和回滚

#### 备份

```bash
# 备份整个应用目录
ssh root@your_domain.com
cd /var/www
sudo tar -czf everydayai-backup-$(date +%Y%m%d-%H%M%S).tar.gz everydayai/

# 下载到本地
scp root@your_domain.com:/var/www/everydayai-backup-*.tar.gz ./backups/
```

#### 回滚

```bash
# 1. 停止服务
sudo systemctl stop everydayai-backend

# 2. 恢复备份
cd /var/www
sudo tar -xzf everydayai-backup-XXXXXX.tar.gz

# 3. 重启服务
sudo systemctl start everydayai-backend
sudo systemctl reload nginx
```

---

### 性能监控

```bash
# 服务器资源使用
ssh root@your_domain.com 'top'

# 磁盘使用
ssh root@your_domain.com 'df -h'

# 内存使用
ssh root@your_domain.com 'free -h'

# 后端进程状态
ssh root@your_domain.com 'ps aux | grep uvicorn'

# Nginx 连接数
ssh root@your_domain.com 'sudo netstat -an | grep :443 | wc -l'
```

---

### SSL 证书续期

Let's Encrypt 证书已配置自动续期（每天凌晨 3 点检查）。

手动续期：

```bash
# 测试续期
sudo certbot renew --dry-run

# 强制续期
sudo certbot renew --force-renewal

# 续期后重载 Nginx
sudo systemctl reload nginx
```

---

### 更新依赖

#### 前端依赖

```bash
cd frontend

# 更新依赖
npm update

# 重新部署
cd ..
./deploy.sh -f
```

#### 后端依赖

```bash
cd backend

# 激活虚拟环境
source venv/bin/activate

# 更新依赖
pip install --upgrade -r requirements.txt

# 重新部署
cd ..
./deploy.sh -b
```

---

### 安全加固

#### 修改 SSH 端口

```bash
# 编辑 SSH 配置
sudo vim /etc/ssh/sshd_config

# 修改端口（例如改为 2222）
Port 2222

# 重启 SSH
sudo systemctl restart sshd

# 更新防火墙
sudo ufw allow 2222/tcp

# 更新部署配置
vim deploy/config.env
SERVER_PORT=2222
```

#### 创建部署用户

```bash
# 在服务器上创建 deploy 用户
ssh root@your_domain.com

useradd -m -s /bin/bash deploy
usermod -aG sudo deploy

# 配置 SSH 密钥
su - deploy
mkdir -p ~/.ssh
chmod 700 ~/.ssh

# 复制你的公钥到 ~/.ssh/authorized_keys

# 设置目录权限
sudo chown -R deploy:deploy /var/www/everydayai

# 更新部署配置
vim deploy/config.env
SERVER_USER=deploy
```

---

## 架构说明

### 部署架构

```
Internet
    ↓
[Nginx (443/80)]
    ↓
    ├─→ /            → 前端静态文件 (/var/www/everydayai/frontend/)
    └─→ /api         → 后端API (localhost:8000)
            ↓
    [FastAPI + Uvicorn]
            ↓
    ├─→ [Supabase PostgreSQL] (云服务)
    ├─→ [Redis] (云服务/本地)
    └─→ [KIE API] (外部API)
```

### 目录结构

```
/var/www/everydayai/
├── frontend/           # 前端静态文件
│   ├── index.html
│   ├── assets/
│   └── ...
│
├── backend/            # 后端代码
│   ├── main.py
│   ├── requirements.txt
│   ├── venv/          # Python虚拟环境
│   ├── .env           # 环境变量（敏感信息）
│   └── ...
│
/etc/nginx/
├── sites-available/
│   └── everydayai     # Nginx配置
└── sites-enabled/
    └── everydayai -> ../sites-available/everydayai

/etc/systemd/system/
└── everydayai-backend.service  # 后端服务配置

/var/log/
├── nginx/
│   ├── everydayai-access.log
│   └── everydayai-error.log
└── everydayai/
```

---

## 文件清单

部署脚本包含以下文件：

```
deploy/
├── deploy.sh                    # 主部署脚本
├── setup-server.sh              # 服务器初始化脚本
├── setup-env.sh                 # 环境变量配置助手
├── nginx.conf                   # Nginx配置模板
├── everydayai-backend.service   # Systemd服务配置
├── config.env                   # 部署配置（需手动创建）
└── DEPLOYMENT.md                # 本文档
```

---

## 技术支持

### 查看版本信息

```bash
# Python 版本
ssh root@your_domain.com 'python3 --version'

# Node.js 版本
ssh root@your_domain.com 'node --version'

# Nginx 版本
ssh root@your_domain.com 'nginx -v'
```

### 联系支持

如遇到无法解决的问题，请提供以下信息：

1. 操作系统版本：`cat /etc/os-release`
2. 服务状态：`sudo systemctl status everydayai-backend`
3. 错误日志：`sudo journalctl -u everydayai-backend -n 100`
4. Nginx 日志：`sudo tail -100 /var/log/nginx/everydayai-error.log`

---

## 更新日志

- **2026-01-29**：初始版本
  - 支持自动化部署
  - 支持 SSL 证书自动申请
  - 支持前后端分离部署
  - 支持环境变量交互式配置

---

**文档版本**：v1.0
**最后更新**：2026-01-29
