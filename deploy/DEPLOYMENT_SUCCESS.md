# 🎉 部署成功！

**部署时间**：2026-01-29
**域名**：everydayai.com.cn
**服务器**：47.110.94.25

---

## ✅ 部署状态

### 前端
- ✅ React 应用已构建并部署
- ✅ HTTPS 已启用（SSL证书有效期：2026-04-29）
- ✅ Nginx 反向代理已配置
- 🌐 **访问地址**：https://everydayai.com.cn

### 后端
- ✅ FastAPI 应用已部署
- ✅ Python 3.11 环境就绪
- ✅ 所有依赖已安装
- ✅ Systemd 服务运行中（4 workers）
- ✅ 环境变量已配置
- ✅ Redis 连接成功
- ✅ Supabase 数据库已连接
- 🔗 **API地址**：https://everydayai.com.cn/api
- 🏥 **健康检查**：https://everydayai.com.cn/api/health

---

## 🔗 快速访问

```bash
# 前端网站
open https://everydayai.com.cn

# 后端健康检查
curl https://everydayai.com.cn/api/health
```

---

## 🛠️ 常用运维命令

### 服务管理

```bash
# SSH 登录服务器
ssh root@47.110.94.25

# 查看后端服务状态
sudo systemctl status everydayai-backend

# 重启后端服务
sudo systemctl restart everydayai-backend

# 停止后端服务
sudo systemctl stop everydayai-backend

# 启动后端服务
sudo systemctl start everydayai-backend

# 查看实时日志
sudo journalctl -u everydayai-backend -f

# 查看最近100行日志
sudo journalctl -u everydayai-backend -n 100

# Nginx 重载配置
sudo systemctl reload nginx

# Nginx 重启
sudo systemctl restart nginx
```

### 查看日志

```bash
# 后端日志（实时）
ssh root@47.110.94.25 'sudo journalctl -u everydayai-backend -f'

# Nginx 访问日志
ssh root@47.110.94.25 'sudo tail -f /var/log/nginx/everydayai-access.log'

# Nginx 错误日志
ssh root@47.110.94.25 'sudo tail -f /var/log/nginx/everydayai-error.log'
```

---

## 🚀 日常部署流程

### 更新前端

```bash
cd /Users/wucong/EVERYDAYAIONE

# 仅部署前端
./deploy/deploy.sh --frontend-only
```

### 更新后端

```bash
cd /Users/wucong/EVERYDAYAIONE

# 仅部署后端
./deploy/deploy.sh --backend-only
```

### 完整部署（前后端）

```bash
cd /Users/wucong/EVERYDAYAIONE

# 部署前后端
./deploy/deploy.sh
```

### 快速部署（跳过测试）

```bash
# 快速部署前端
./deploy/deploy.sh -f --skip-test

# 快速部署后端
./deploy/deploy.sh -b --skip-test
```

---

## 🔧 环境变量更新

如果需要修改环境变量配置：

### 方式1：本地编辑后上传（推荐）

```bash
# 1. 编辑本地配置文件
vim deploy/.env.production

# 2. 上传到服务器
./deploy/upload-env.sh

# 3. 重启服务
ssh root@47.110.94.25 'sudo systemctl restart everydayai-backend'
```

### 方式2：服务器上直接编辑

```bash
# 1. SSH 到服务器
ssh root@47.110.94.25

# 2. 编辑环境变量
vim /var/www/everydayai/backend/.env

# 3. 重启服务
sudo systemctl restart everydayai-backend
```

---

## 📊 系统监控

### 检查服务状态

```bash
# 后端服务
ssh root@47.110.94.25 'sudo systemctl status everydayai-backend'

# Nginx
ssh root@47.110.94.25 'sudo systemctl status nginx'

# 端口监听
ssh root@47.110.94.25 'sudo netstat -tlnp | grep -E ":(80|443|8000)"'
```

### 资源使用情况

```bash
# CPU 和内存
ssh root@47.110.94.25 'top -bn1 | head -20'

# 磁盘使用
ssh root@47.110.94.25 'df -h'

# 应用目录大小
ssh root@47.110.94.25 'du -sh /var/www/everydayai/*'
```

---

## 🔒 安全管理

### SSL 证书

```bash
# 查看证书信息
ssh root@47.110.94.25 'sudo certbot certificates'

# 手动续期测试
ssh root@47.110.94.25 'sudo certbot renew --dry-run'

# 强制续期
ssh root@47.110.94.25 'sudo certbot renew --force-renewal'
```

证书自动续期已配置（每天凌晨3点检查）

### 防火墙状态

```bash
# 阿里云安全组已开放：22、80、443 端口
```

---

## 💾 备份策略

### 备份应用目录

```bash
# 在服务器上创建备份
ssh root@47.110.94.25 << 'EOF'
cd /var/www
sudo tar -czf everydayai-backup-$(date +%Y%m%d-%H%M%S).tar.gz everydayai/
ls -lh everydayai-backup-*.tar.gz | tail -5
EOF

# 下载备份到本地
scp root@47.110.94.25:/var/www/everydayai-backup-*.tar.gz ./backups/
```

### 备份数据库

Supabase 云数据库自动备份，可在控制台查看。

---

## 🐛 故障排查

### 后端服务无法启动

```bash
# 查看详细日志
ssh root@47.110.94.25 'sudo journalctl -u everydayai-backend -n 100 --no-pager'

# 检查配置文件
ssh root@47.110.94.25 'cat /var/www/everydayai/backend/.env'

# 手动测试启动
ssh root@47.110.94.25 << 'EOF'
cd /var/www/everydayai/backend
python3.11 -m uvicorn main:app --host 0.0.0.0 --port 8000
EOF
```

### 502 错误

```bash
# 检查后端是否运行
ssh root@47.110.94.25 'sudo systemctl status everydayai-backend'

# 检查端口监听
ssh root@47.110.94.25 'sudo netstat -tlnp | grep 8000'

# 重启后端
ssh root@47.110.94.25 'sudo systemctl restart everydayai-backend'
```

### 前端页面不更新

```bash
# 清除浏览器缓存
# 或使用硬刷新：Cmd+Shift+R (Mac) / Ctrl+Shift+R (Windows)

# 检查前端文件
ssh root@47.110.94.25 'ls -lh /var/www/everydayai/frontend/'

# 重新部署前端
./deploy/deploy.sh --frontend-only
```

---

## 📁 服务器文件位置

```
/var/www/everydayai/
├── frontend/              # 前端静态文件
│   ├── index.html
│   └── assets/
└── backend/               # 后端代码
    ├── main.py
    ├── .env              # 环境变量（权限：600）
    └── requirements.txt

/etc/nginx/conf.d/
└── everydayai.conf        # Nginx 配置

/etc/systemd/system/
└── everydayai-backend.service  # Systemd 服务

/etc/letsencrypt/live/everydayai.com.cn/
├── fullchain.pem          # SSL 证书
└── privkey.pem            # 私钥

/root/backup-*/            # 旧配置备份
```

---

## 📝 数据库管理

### 运行迁移脚本

```bash
# 1. 登录 Supabase 控制台
open https://supabase.com/dashboard

# 2. 选择项目：qcaatwmlzqqnzfjdzlzm

# 3. 进入 SQL Editor

# 4. 执行迁移脚本（按顺序）
# 迁移脚本位置：docs/database/migrations/
```

### 查看数据库连接

```bash
# 测试数据库连接
ssh root@47.110.94.25 << 'EOF'
cd /var/www/everydayai/backend
python3.11 -c "
from core.database import get_supabase_client
client = get_supabase_client()
print('数据库连接成功！')
"
EOF
```

---

## 🎯 性能优化建议

1. **启用 Gzip 压缩**（已配置）
2. **配置 CDN**（阿里云 OSS 已配置）
3. **数据库索引优化**（根据查询模式优化）
4. **Redis 缓存策略**（已启用）
5. **定期清理日志**：
   ```bash
   # 清理7天前的日志
   ssh root@47.110.94.25 'sudo journalctl --vacuum-time=7d'
   ```

---

## 📞 技术支持

### 查看版本信息

```bash
# Python 版本
ssh root@47.110.94.25 'python3.11 --version'

# Node.js 版本
ssh root@47.110.94.25 'node --version'

# Nginx 版本
ssh root@47.110.94.25 'nginx -v'
```

### 重要配置信息

- **域名**：everydayai.com.cn
- **服务器IP**：47.110.94.25
- **后端端口**：8000
- **SSL证书过期**：2026-04-29
- **Python版本**：3.11.13
- **Node.js版本**：18.20.8

---

## ✅ 下一步建议

1. ✅ **测试所有功能**：注册、登录、图片生成、视频生成等
2. ✅ **配置域名备案**（如需要）
3. ✅ **设置监控告警**（可选）
4. ✅ **定期备份数据库**
5. ✅ **查看并优化性能**

---

**部署脚本文档**：[deploy/DEPLOYMENT.md](./DEPLOYMENT.md)
**快速入门**：[deploy/README.md](./README.md)

---

🎊 **恭喜！你的应用已成功部署到生产环境！** 🎊
