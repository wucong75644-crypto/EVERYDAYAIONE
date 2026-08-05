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
RUN_MIGRATIONS=false              # 有 pending 时阻断部署；完成账本 baseline 后才可设 true
MIGRATION_DATABASE_URL=postgresql://everydayai_migrator:<独立密码>@127.0.0.1:5432/everydayai
# 仅部署迁移使用；不得复用 Backend/Worker 的 DATABASE_URL
```

数据库角色环境文件模板位于 `deploy/env-templates/`。真实文件必须安装为：

- `backend/.env.runtime`：包含 runtime `DATABASE_URL` 及模板白名单内的 Runtime
  ingress、confirmation、definition 配置；禁止未知键和重复键
- `backend/.env.wecom-runtime`：包含 WeCom runtime `DATABASE_URL` 及模板白名单内的
  ingress、definition 配置；禁止未知键和重复键
- `backend/.env.worker`：仅包含 worker `DATABASE_URL`
- `backend/.env.worker-client`：仅包含 worker `WORKER_DATABASE_URL`
- `backend/.env.sync`：仅包含 Sync `everydayai_sync` 的 `DATABASE_URL`
- `backend/.env.migrator`：仅包含 `MIGRATION_DATABASE_URL`

上述数据库角色文件权限必须为 `0600`，并在切换服务前执行：

```bash
bash deploy/validate-tenant-db-env.sh /var/www/everydayai/backend
```

安装 production flags-off v3 单元前必须执行严格入口；该入口额外要求两个 Runtime
文件使用 `everydayai-default/v3`，且 `AGENT_RUNTIME_INGRESS_ENABLED`、
`TOOL_CONFIRMATION_V3_ENABLED` 全部为 `false`：

```bash
bash deploy/validate-tenant-db-env.sh \
  /var/www/everydayai/backend --runtime-flags-off-v3
```

Secret-capable 服务另使用 `backend/.env.kek`，格式参考
`deploy/env-templates/kek.env.template`。该文件不得放入公共 `.env`，必须为 `0600`，
current/previous keyring 中每个值均为 base64 编码的 32 字节 KEK。安装后执行：

```bash
bash deploy/validate-kek-env.sh /var/www/everydayai/backend/.env.kek
```

Backend 配置管理接口与 Sync Bundle 解析均需要加解密，因此两个服务单元必须加载
`.env.kek`；Actor、WeCom 和普通 Worker 不得加载该文件。

独立 Agent Runtime Worker 只能加载
`/etc/everydayai/agent-runtime-worker.env`。该文件仅包含窄数据库角色、进程身份、
release、health socket 与 Sandbox 路径等运行配置，不得包含 Provider API Key、KEK
或原始凭证。Runtime Provider 凭证只能通过
`CredentialBroker` 的 opaque handle 和短期 `CredentialLease` 提供；缺少安全 backend
时必须失败关闭。该进程使用 `env_file=None` 的独立 typed settings，Systemd 同时将
`backend/.env` 与历史 `agent-runtime-model.env` 标记为不可访问；即使服务器残留这些
文件，也不能作为 Runtime 配置来源。Projection/Authorization 仍沿用通用 Settings，
需在后续独立安全批次审计。

在 Agent Runtime grant、policy 和测试库 RLS 验证完成前，不得修改 Systemd
`EnvironmentFile` 指向这些角色文件。

### Production flags-off 单元安装

`--runtime-flags-off-install` 是与前端、后端、setup 和 skip 选项互斥的安装路径。
它只同步 B1 安装文件，并安装以下四个单元及 Sandbox cgroup wrapper：

- `everydayai-agent-runtime.service`
- `everydayai-agent-projection.service`
- `everydayai-agent-authorization.service`
- `everydayai-sandbox-worker.service`

执行前必须由运维人员准备 `/etc/everydayai/` 下四个对应 Worker 环境文件，权限为
`0640`；每个文件必须与仓库模板键集合完全一致、无重复键或占位符，四个文件的
`AGENT_RUNTIME_RELEASE_REVISION` 必须等于本次 40 位 release SHA。Runtime Worker 的
`AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED` 必须保持 `false`。Sandbox 配置也必须
包含 release revision。

发布脚本会在任何远端写入前确认四个服务均为 `inactive + disabled`，随后调用：

```bash
bash deploy/install-service-units.sh \
  /var/www/everydayai/backend agent-runtime-only <40位-release-sha>
```

该模式只执行 `systemctl daemon-reload`，不运行 migration，不重启旧服务，不启停或
enable 新服务，也不切换数据库 Owner。若任一已有 unit 或 wrapper 与发布内容不同，
安装会在写入任何目标前失败，原文件保持不变；需先人工审查差异并制定恢复方案，禁止
通过该入口直接覆盖。

首次所有权转移必须由 PostgreSQL 管理员执行，且必须先完成数据库备份：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/transfer-agent-runtime-ownership.sh
```

脚本转移迁移账本、首组 13 张 Agent Runtime 表及资产函数，不启用 RLS。生产已有
7 张表处于“ENABLE RLS、无 policy”状态，因此脚本会先将旧应用角色临时加入
`everydayai_owner`，保持其原有 owner 能力；单独授予 CRUD 不能绕过 RLS。
所有权回滚必须先关闭 FORCE RLS，并显式设置：

```bash
ALLOW_TENANT_DB_OWNERSHIP_ROLLBACK=true \
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/rollback-agent-runtime-ownership.sh
```

第二批 Runtime/Message 对象必须在迁移 152 前由管理员原子接管；152 会收紧
`wecom_get_or_create_user` 等第二批函数的权限，153 则继续建立第二批表的 RLS。
脚本不会启用 RLS，并继续保留旧服务角色：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/transfer-runtime-message-ownership.sh
```

第二批回滚必须先把相关服务切回旧数据库 URL，并关闭这些表的 FORCE RLS：

```bash
ALLOW_RUNTIME_MESSAGE_OWNERSHIP_ROLLBACK=true \
RUNTIME_MESSAGE_SERVICES_RESTORED=true \
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/rollback-runtime-message-ownership.sh
```

Memory Runtime 在应用迁移 165 前，必须由管理员单独转移四张表与两个原子提交函数：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/transfer-memory-runtime-ownership.sh
```

随后才可通过标准迁移 Runner 应用
`165_memory_runtime_tenant_boundary.sql`。迁移会对四张 Memory 表启用 FORCE RLS，
撤销 `PUBLIC/service_role` 的旧提交能力，并为 Web runtime 与 Actor worker 授予
最小权限。回滚必须先应用 165 rollback 关闭 FORCE RLS，再显式执行：

```bash
ALLOW_TENANT_DB_OWNERSHIP_ROLLBACK=true \
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/rollback-memory-runtime-ownership.sh
```

Worker Control 在应用 171–180 前，必须由管理员转移错误日志、知识指标、定时任务和
执行记录四表及实际列序列：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/transfer-worker-control-ownership.sh
```

迁移 180 会对 `scheduled_tasks`、`scheduled_task_runs` 启用 FORCE RLS；Worker 只调用
171–179 的受控函数，Web Runtime 只获得企业 Scope 内的任务管理和运行历史读取。
所有权回滚必须先应用 180 rollback 关闭 FORCE RLS：

```bash
ALLOW_DESTRUCTIVE_TENANT_DB_ROLLBACK=true \
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/rollback-worker-control-ownership.sh
```

Sync 数据域在应用 181–185 前，必须先创建独立 `everydayai_sync` 登录角色并转移
ERP/快麦表、序列、物化视图及函数 owner：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/transfer-sync-domain-ownership.sh
```

迁移完成后 Sync 仅通过 `.env.sync` 使用独立角色；Runtime 只经窄 RPC 读取或入队，
Worker 不获得 Sync 表直权。回滚必须先逆序执行 185–181 rollback，再运行
`rollback-sync-domain-ownership.sh`。

只有在 150–185 全部应用、Actor/媒体/定时 Worker 与 Sync Facade 门禁通过、
所有服务已切换独立角色、
旧角色活动连接归零后，才能撤销
临时 owner 兼容能力：

```bash
ALLOW_TENANT_DB_ROLE_FINALIZE=true \
TENANT_SERVICES_USE_ISOLATED_ROLES=true \
TENANT_DB_ADMIN_URL='postgresql://...' \
LEGACY_DATABASE_OWNER=everydayai \
bash deploy/finalize-tenant-db-role-cutover.sh
```

该步骤必须使用不同于旧应用角色的数据库管理员连接；能力域未闭合时禁止执行。

150–164 的核心切换见
`docs/document/RUNBOOK_150_161_生产租户架构切换.md`。普通 `deploy.sh` 不替代该
架构迁移流程。在初始审计、两批 owner 均完成后及迁移完成后，使用
`deploy/preflight-tenant-cutover.sh` 进行只读状态核验。
171–185、Sync 角色切换及 failed 账本恢复见
`docs/document/RUNBOOK_171_180_Worker_Control生产恢复.md`。

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
5. ✓ 清理旧 dist 并构建前端（npm run build）
6. ✓ 检查后端（pytest）
7. ✓ 同步文件到服务器（rsync，排除 .DS_Store）
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
