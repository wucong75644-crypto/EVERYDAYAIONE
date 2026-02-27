#!/bin/bash

###############################################################################
# 安全上传环境变量配置文件
# 用途：将本地配置文件安全上传到服务器
###############################################################################

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

echo -e "${GREEN}"
cat << 'EOF'
╔═══════════════════════════════════════════════╗
║   安全上传环境变量配置                       ║
╚═══════════════════════════════════════════════╝
EOF
echo -e "${NC}"

# 检查配置文件是否存在
ENV_FILE="deploy/.env.production"

if [ ! -f "$ENV_FILE" ]; then
    log_error "配置文件不存在: $ENV_FILE"
    log_info "请先编辑 deploy/.env.production 文件"
    exit 1
fi

# 检查是否还有未填写的占位符
if grep -q "请填写\|请替换" "$ENV_FILE"; then
    log_error "配置文件中还有未填写的项目"
    log_info "请编辑 $ENV_FILE 并填写所有必要配置"
    echo ""
    log_info "未填写的项目："
    grep -n "请填写\|请替换" "$ENV_FILE" || true
    exit 1
fi

# 加载部署配置
if [ ! -f "deploy/config.env" ]; then
    log_error "部署配置文件不存在: deploy/config.env"
    exit 1
fi

source deploy/config.env

log_info "目标服务器: ${SERVER_HOST}"
echo ""

log_warning "即将上传环境变量配置到服务器，该操作会覆盖现有配置"
read -p "是否继续？(y/N): " confirm

if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    log_info "取消上传"
    exit 0
fi

log_info "开始上传配置文件..."

# 上传到临时位置
scp -P ${SERVER_PORT} "$ENV_FILE" ${SERVER_USER}@${SERVER_HOST}:/tmp/.env.production

# 在服务器上移动到正确位置并设置权限
ssh -p ${SERVER_PORT} ${SERVER_USER}@${SERVER_HOST} bash << 'ENDSSH'
    set -e

    echo "1. 备份旧配置（如果存在）"
    if [ -f "/var/www/everydayai/backend/.env" ]; then
        cp /var/www/everydayai/backend/.env /var/www/everydayai/backend/.env.backup.$(date +%s)
        echo "   已备份到: /var/www/everydayai/backend/.env.backup.*"
    fi

    echo "2. 移动新配置到目标位置"
    mv /tmp/.env.production /var/www/everydayai/backend/.env

    echo "3. 设置安全权限（600 - 仅所有者可读写）"
    chmod 600 /var/www/everydayai/backend/.env
    chown root:root /var/www/everydayai/backend/.env

    echo "4. 验证配置文件"
    ls -lh /var/www/everydayai/backend/.env

    echo ""
    echo "配置文件已更新！"
ENDSSH

log_success "环境变量配置已上传"
echo ""
log_info "下一步："
log_info "1. 启动后端服务: ssh ${SERVER_USER}@${SERVER_HOST} 'sudo systemctl start everydayai-backend'"
log_info "2. 查看服务状态: ssh ${SERVER_USER}@${SERVER_HOST} 'sudo systemctl status everydayai-backend'"
log_info "3. 查看实时日志: ssh ${SERVER_USER}@${SERVER_HOST} 'sudo journalctl -u everydayai-backend -f'"
echo ""

# 询问是否立即启动服务
read -p "是否现在启动后端服务？(y/N): " start_service

if [ "$start_service" = "y" ] || [ "$start_service" = "Y" ]; then
    log_info "启动后端服务..."
    ssh -p ${SERVER_PORT} ${SERVER_USER}@${SERVER_HOST} bash << 'ENDSSH'
        systemctl restart everydayai-backend
        sleep 3
        systemctl status everydayai-backend --no-pager
ENDSSH

    log_success "后端服务已启动"
    log_info "访问测试: curl https://everydayai.com.cn/api/health"
fi
