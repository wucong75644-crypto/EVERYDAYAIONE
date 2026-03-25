#!/bin/bash
# ============================================================
# 服务器数据库安装脚本 — PostgreSQL 16 + pgvector + Redis 7
# 适用系统：Alibaba Cloud Linux 3 (基于 CentOS/RHEL)
# 用法：sudo bash setup-database.sh
# ============================================================

set -euo pipefail

# ============ 配置 ============
DB_NAME="everydayai"
DB_USER="everydayai"
DB_PASSWORD=""  # 将在脚本中交互式输入
PG_VERSION="16"

# ============ 颜色输出 ============
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ============ 前置检查 ============
if [[ $EUID -ne 0 ]]; then
    log_error "请使用 root 权限运行：sudo bash setup-database.sh"
    exit 1
fi

# 交互式输入数据库密码
read -sp "请输入数据库密码（用于 ${DB_USER} 用户）: " DB_PASSWORD
echo
if [[ -z "$DB_PASSWORD" ]]; then
    log_error "密码不能为空"
    exit 1
fi
read -sp "请再次确认密码: " DB_PASSWORD_CONFIRM
echo
if [[ "$DB_PASSWORD" != "$DB_PASSWORD_CONFIRM" ]]; then
    log_error "两次密码不一致"
    exit 1
fi

# ============================================================
# 第一步：安装 PostgreSQL 16
# ============================================================
log_info "========== 安装 PostgreSQL ${PG_VERSION} =========="

# 安装 PostgreSQL 官方仓库
dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-8-x86_64/pgdg-redhat-repo-latest.noarch.rpm || true

# 禁用自带的 PostgreSQL 模块（避免版本冲突）
dnf -qy module disable postgresql || true

# 安装 PostgreSQL 16
dnf install -y postgresql${PG_VERSION}-server postgresql${PG_VERSION}-devel postgresql${PG_VERSION}-contrib

# 初始化数据库
if [[ ! -f /var/lib/pgsql/${PG_VERSION}/data/PG_VERSION ]]; then
    /usr/pgsql-${PG_VERSION}/bin/postgresql-${PG_VERSION}-setup initdb
    log_info "PostgreSQL ${PG_VERSION} 数据库已初始化"
else
    log_warn "PostgreSQL 数据目录已存在，跳过初始化"
fi

# 启动并设置开机自启
systemctl enable postgresql-${PG_VERSION}
systemctl start postgresql-${PG_VERSION}
log_info "PostgreSQL ${PG_VERSION} 已启动"

# ============================================================
# 第二步：安装 pgvector 扩展
# ============================================================
log_info "========== 安装 pgvector 扩展 =========="

# 安装编译依赖
dnf install -y git gcc make

# 编译安装 pgvector
PGVECTOR_DIR="/tmp/pgvector"
if [[ -d "$PGVECTOR_DIR" ]]; then
    rm -rf "$PGVECTOR_DIR"
fi

cd /tmp
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git
cd pgvector
export PATH="/usr/pgsql-${PG_VERSION}/bin:$PATH"
make
make install
cd /
rm -rf "$PGVECTOR_DIR"

log_info "pgvector 扩展已安装"

# ============================================================
# 第三步：配置 PostgreSQL
# ============================================================
log_info "========== 配置 PostgreSQL =========="

PG_HBA="/var/lib/pgsql/${PG_VERSION}/data/pg_hba.conf"
PG_CONF="/var/lib/pgsql/${PG_VERSION}/data/postgresql.conf"

# 配置连接认证（仅允许本地连接）
cat > "${PG_HBA}" << 'PGEOF'
# TYPE  DATABASE        USER            ADDRESS                 METHOD
# 本地 socket 连接
local   all             postgres                                peer
local   all             all                                     md5
# 本机 TCP 连接（应用连接用）
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
# 禁止远程连接（安全）
PGEOF

# 优化 PostgreSQL 配置（2C8G 服务器）
cat >> "${PG_CONF}" << 'CONFEOF'

# ---- EverydayAI 优化配置（2C8G）----
listen_addresses = '127.0.0.1'      # 仅本地监听
port = 5432
max_connections = 100

# 内存配置（8G RAM，分配 ~2G 给 PG）
shared_buffers = 512MB
effective_cache_size = 2GB
work_mem = 16MB
maintenance_work_mem = 256MB

# WAL 配置
wal_buffers = 16MB
min_wal_size = 256MB
max_wal_size = 1GB

# 查询优化
random_page_cost = 1.1              # SSD 优化
effective_io_concurrency = 200      # SSD 优化
default_statistics_target = 100

# 日志
log_timezone = 'Asia/Shanghai'
timezone = 'Asia/Shanghai'
CONFEOF

# 重启 PostgreSQL 使配置生效
systemctl restart postgresql-${PG_VERSION}
log_info "PostgreSQL 配置优化完成"

# ============================================================
# 第四步：创建数据库和用户
# ============================================================
log_info "========== 创建数据库和用户 =========="

sudo -u postgres psql -c "DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${DB_USER}') THEN
        CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}';
    END IF;
END
\$\$;"

sudo -u postgres psql -c "ALTER ROLE ${DB_USER} CREATEDB;"

# 创建数据库
sudo -u postgres psql -c "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1 || \
    sudo -u postgres createdb -O ${DB_USER} ${DB_NAME}

# 安装扩展（需要 superuser）
sudo -u postgres psql -d ${DB_NAME} -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"
sudo -u postgres psql -d ${DB_NAME} -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d ${DB_NAME} -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

# 授权
sudo -u postgres psql -d ${DB_NAME} -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};"
sudo -u postgres psql -d ${DB_NAME} -c "GRANT ALL ON SCHEMA public TO ${DB_USER};"
sudo -u postgres psql -d ${DB_NAME} -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ${DB_USER};"
sudo -u postgres psql -d ${DB_NAME} -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ${DB_USER};"
sudo -u postgres psql -d ${DB_NAME} -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO ${DB_USER};"

log_info "数据库 ${DB_NAME} 和用户 ${DB_USER} 已创建"

# ============================================================
# 第五步：安装 Redis 7
# ============================================================
log_info "========== 安装 Redis 7 =========="

# 安装 Remi 仓库获取 Redis 7
dnf install -y https://rpms.remirepo.net/enterprise/remi-release-8.rpm || true
dnf module enable -y redis:remi-7.2 || {
    # 如果 module 不可用，直接从 Remi 安装
    dnf install -y --enablerepo=remi redis
}
dnf install -y redis

# 配置 Redis
REDIS_CONF="/etc/redis/redis.conf"
if [[ ! -f "$REDIS_CONF" ]]; then
    REDIS_CONF="/etc/redis.conf"
fi

# 基础安全配置
sed -i 's/^bind .*/bind 127.0.0.1 -::1/' "$REDIS_CONF"
sed -i 's/^# maxmemory .*/maxmemory 512mb/' "$REDIS_CONF"
sed -i 's/^# maxmemory-policy .*/maxmemory-policy allkeys-lru/' "$REDIS_CONF"

# 启动并设置开机自启
systemctl enable redis
systemctl start redis

log_info "Redis 7 已安装并启动"

# ============================================================
# 第六步：验证安装
# ============================================================
log_info "========== 验证安装 =========="

# 验证 PostgreSQL
PG_VERSION_ACTUAL=$(sudo -u postgres psql -t -c "SHOW server_version;" | xargs)
log_info "PostgreSQL 版本: ${PG_VERSION_ACTUAL}"

# 验证扩展
EXTENSIONS=$(sudo -u postgres psql -d ${DB_NAME} -t -c "SELECT extname FROM pg_extension ORDER BY extname;" | xargs)
log_info "已安装扩展: ${EXTENSIONS}"

# 验证连接
PGPASSWORD="${DB_PASSWORD}" psql -h 127.0.0.1 -U ${DB_USER} -d ${DB_NAME} -c "SELECT 1;" > /dev/null 2>&1 && \
    log_info "PostgreSQL 连接测试 ✓" || \
    log_error "PostgreSQL 连接测试 ✗"

# 验证 Redis
redis-cli ping > /dev/null 2>&1 && \
    log_info "Redis 连接测试 ✓" || \
    log_error "Redis 连接测试 ✗"

# ============================================================
# 输出连接信息
# ============================================================
echo ""
echo "============================================"
echo "  数据库安装完成！"
echo "============================================"
echo ""
echo "PostgreSQL 连接信息："
echo "  Host:     127.0.0.1"
echo "  Port:     5432"
echo "  Database: ${DB_NAME}"
echo "  User:     ${DB_USER}"
echo "  Password: (你刚才输入的密码)"
echo ""
echo "连接串（.env 用）："
echo "  DATABASE_URL=postgresql://${DB_USER}:<密码>@127.0.0.1:5432/${DB_NAME}"
echo ""
echo "Redis 连接信息："
echo "  Host:     127.0.0.1"
echo "  Port:     6379"
echo "  无密码（仅本地访问）"
echo ""
echo "下一步：运行迁移脚本初始化表结构"
echo "============================================"
