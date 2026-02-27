#!/bin/bash

###############################################################################
# 清理旧部署脚本 - EVERYDAYAIONE
# 用途：备份并清理服务器上的旧部署
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

SERVER_HOST=${1:-"47.110.94.25"}

echo -e "${GREEN}"
cat << 'EOF'
╔═══════════════════════════════════════════════╗
║   清理旧部署脚本                             ║
╚═══════════════════════════════════════════════╝
EOF
echo -e "${NC}"

log_warning "此脚本将在服务器上执行以下操作："
echo "  1. 备份旧的Nginx配置"
echo "  2. 备份/var/www/html目录"
echo "  3. 清理默认配置（为新部署做准备）"
echo ""

read -p "是否继续？(y/N): " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    log_info "取消清理"
    exit 0
fi

log_info "开始清理旧部署..."

ssh root@${SERVER_HOST} bash << 'ENDSSH'
    set -e

    BACKUP_DIR="/root/backup-$(date +%Y%m%d-%H%M%S)"

    echo "1. 创建备份目录: $BACKUP_DIR"
    mkdir -p $BACKUP_DIR

    echo "2. 备份Nginx配置"
    cp -r /etc/nginx/conf.d $BACKUP_DIR/

    echo "3. 备份/var/www/html（如果存在）"
    if [ -d "/var/www/html" ]; then
        cp -r /var/www/html $BACKUP_DIR/
    fi

    echo "4. 备份Nginx主配置"
    cp /etc/nginx/nginx.conf $BACKUP_DIR/

    echo "5. 清理默认配置（保留文件，但重命名）"
    if [ -f "/etc/nginx/conf.d/default.conf" ]; then
        mv /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf.bak
    fi

    echo "6. 测试Nginx配置"
    nginx -t

    echo "7. 重载Nginx"
    systemctl reload nginx

    echo ""
    echo "备份已保存到: $BACKUP_DIR"
    ls -lh $BACKUP_DIR
ENDSSH

log_success "清理完成！"
log_info "旧配置已备份，现在可以运行 ./deploy.sh --setup 进行新部署"

