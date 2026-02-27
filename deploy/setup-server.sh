#!/bin/bash

###############################################################################
# 服务器初始化脚本 - EVERYDAYAIONE
# 用途：在阿里云ECS上安装和配置运行环境
# 运行环境：Ubuntu 20.04/22.04 或 CentOS 7/8
# 注意：此脚本需要root权限执行
###############################################################################

set -e

# 颜色输出
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

# 检查是否为root用户
check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "请使用root权限运行此脚本"
        exit 1
    fi
}

# 检测操作系统
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VERSION=$VERSION_ID
    else
        log_error "无法检测操作系统版本"
        exit 1
    fi

    log_info "检测到操作系统: $OS $VERSION"
}

# 安装基础工具
install_basics() {
    log_info "安装基础工具..."

    if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
        apt-get update
        apt-get install -y curl wget git vim unzip software-properties-common
    elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ]; then
        yum install -y curl wget git vim unzip epel-release
    fi

    log_success "基础工具安装完成"
}

# 安装Python 3.10+
install_python() {
    log_info "检查Python版本..."

    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
        log_info "当前Python版本: $PYTHON_VERSION"

        if [ "$(echo "$PYTHON_VERSION >= 3.8" | bc)" -eq 1 ]; then
            log_success "Python版本满足要求"
            return
        fi
    fi

    log_info "安装Python 3.10..."

    if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
        add-apt-repository -y ppa:deadsnakes/ppa
        apt-get update
        apt-get install -y python3.10 python3.10-venv python3.10-dev python3-pip
        update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1
    elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ]; then
        yum install -y python3 python3-devel python3-pip
    fi

    log_success "Python安装完成"
}

# 安装Node.js 18+
install_nodejs() {
    log_info "检查Node.js版本..."

    if command -v node &> /dev/null; then
        NODE_VERSION=$(node --version | cut -d'v' -f2 | cut -d'.' -f1)
        log_info "当前Node.js版本: v$NODE_VERSION"

        if [ "$NODE_VERSION" -ge 18 ]; then
            log_success "Node.js版本满足要求"
            return
        fi
    fi

    log_info "安装Node.js 20 LTS..."

    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
        apt-get install -y nodejs
    elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ]; then
        yum install -y nodejs
    fi

    log_success "Node.js安装完成: $(node --version)"
}

# 安装Nginx
install_nginx() {
    log_info "检查Nginx..."

    if command -v nginx &> /dev/null; then
        log_success "Nginx已安装: $(nginx -v 2>&1)"
        return
    fi

    log_info "安装Nginx..."

    if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
        apt-get install -y nginx
    elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ]; then
        yum install -y nginx
    fi

    systemctl enable nginx
    systemctl start nginx

    log_success "Nginx安装完成"
}

# 配置防火墙
configure_firewall() {
    log_info "配置防火墙..."

    if command -v ufw &> /dev/null; then
        # Ubuntu/Debian 使用 ufw
        ufw allow 80/tcp
        ufw allow 443/tcp
        ufw allow 22/tcp
        log_success "UFW防火墙规则已配置"
    elif command -v firewall-cmd &> /dev/null; then
        # CentOS/RHEL 使用 firewalld
        firewall-cmd --permanent --add-service=http
        firewall-cmd --permanent --add-service=https
        firewall-cmd --permanent --add-service=ssh
        firewall-cmd --reload
        log_success "Firewalld防火墙规则已配置"
    else
        log_warning "未检测到防火墙，请手动开放80、443、22端口"
    fi
}

# 创建应用目录
create_app_directories() {
    log_info "创建应用目录..."

    mkdir -p /var/www/everydayai/{frontend,backend}
    mkdir -p /var/log/everydayai

    log_success "应用目录创建完成"
}

# 配置Nginx
configure_nginx() {
    local DOMAIN=$1
    local BACKEND_PORT=$2

    log_info "配置Nginx..."

    # 备份原有配置
    if [ -f /etc/nginx/sites-available/everydayai ]; then
        cp /etc/nginx/sites-available/everydayai /etc/nginx/sites-available/everydayai.backup.$(date +%s)
    fi

    # 复制新配置
    if [ -f /tmp/nginx.conf ]; then
        # 替换域名和端口
        sed "s/your_domain.com/$DOMAIN/g; s/8000/$BACKEND_PORT/g" /tmp/nginx.conf > /etc/nginx/sites-available/everydayai

        # 创建软链接
        ln -sf /etc/nginx/sites-available/everydayai /etc/nginx/sites-enabled/

        # 删除默认配置
        rm -f /etc/nginx/sites-enabled/default

        # 测试配置
        nginx -t

        log_success "Nginx配置完成"
    else
        log_warning "未找到nginx.conf模板，跳过Nginx配置"
    fi
}

# 安装SSL证书（Let's Encrypt）
install_ssl_certificate() {
    local DOMAIN=$1
    local EMAIL=$2

    log_info "安装SSL证书..."

    # 安装certbot
    if ! command -v certbot &> /dev/null; then
        if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
            apt-get install -y certbot python3-certbot-nginx
        elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ]; then
            yum install -y certbot python3-certbot-nginx
        fi
    fi

    # 申请证书
    certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email $EMAIL --redirect

    # 设置自动续期
    if ! crontab -l | grep -q 'certbot renew'; then
        (crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet --post-hook 'systemctl reload nginx'") | crontab -
    fi

    log_success "SSL证书安装完成"
}

# 配置后端systemd服务
configure_backend_service() {
    local BACKEND_PORT=$1

    log_info "配置后端systemd服务..."

    if [ -f /tmp/everydayai-backend.service ]; then
        # 替换端口
        sed "s/8000/$BACKEND_PORT/g" /tmp/everydayai-backend.service > /etc/systemd/system/everydayai-backend.service

        # 重载systemd
        systemctl daemon-reload

        # 启用服务（但不立即启动，等待代码部署）
        systemctl enable everydayai-backend

        log_success "后端服务配置完成"
    else
        log_warning "未找到systemd服务模板，跳过服务配置"
    fi
}

# 优化系统参数
optimize_system() {
    log_info "优化系统参数..."

    # 增加文件描述符限制
    cat >> /etc/security/limits.conf << EOF
* soft nofile 65535
* hard nofile 65535
EOF

    # 优化内核参数
    cat >> /etc/sysctl.conf << EOF
# 网络优化
net.core.somaxconn = 1024
net.ipv4.tcp_max_syn_backlog = 2048
net.ipv4.tcp_tw_reuse = 1
net.ipv4.ip_local_port_range = 10000 65535

# 文件系统优化
fs.file-max = 65535
EOF

    sysctl -p

    log_success "系统参数优化完成"
}

# 创建部署用户（可选）
create_deploy_user() {
    log_info "创建部署用户..."

    if id "deploy" &>/dev/null; then
        log_info "deploy用户已存在"
    else
        useradd -m -s /bin/bash deploy
        usermod -aG sudo deploy
        log_success "deploy用户创建完成"
    fi

    # 设置目录权限
    chown -R deploy:deploy /var/www/everydayai
    chmod -R 755 /var/www/everydayai
}

# 显示摘要信息
show_summary() {
    local DOMAIN=$1

    echo ""
    log_success "========== 服务器初始化完成 =========="
    echo ""
    log_info "已安装的服务："
    log_info "  - Python: $(python3 --version)"
    log_info "  - Node.js: $(node --version)"
    log_info "  - Nginx: $(nginx -v 2>&1)"
    log_info "  - Certbot: $(certbot --version 2>&1 | head -n1)"
    echo ""
    log_info "应用目录："
    log_info "  - 前端: /var/www/everydayai/frontend"
    log_info "  - 后端: /var/www/everydayai/backend"
    echo ""
    log_info "下一步操作："
    log_info "1. 在本地配置 deploy/config.env"
    log_info "2. 在服务器上配置后端环境变量: /var/www/everydayai/backend/.env"
    log_info "3. 运行部署脚本: ./deploy/deploy.sh"
    echo ""
    log_warning "重要提醒："
    log_warning "- 请确保域名 $DOMAIN 已正确解析到本服务器"
    log_warning "- 请配置后端 .env 文件（数据库、Redis、API密钥等）"
    log_warning "- 首次部署前请检查防火墙和安全组设置"
    echo ""
}

# 主函数
main() {
    DOMAIN=${1:-"your_domain.com"}
    EMAIL=${2:-"admin@example.com"}
    BACKEND_PORT=${3:-8000}

    echo -e "${GREEN}"
    cat << 'EOF'
╔═══════════════════════════════════════════════╗
║   EVERYDAYAIONE 服务器初始化脚本             ║
║   自动安装运行环境和配置服务                 ║
╚═══════════════════════════════════════════════╝
EOF
    echo -e "${NC}"

    log_info "开始服务器初始化..."
    log_info "域名: $DOMAIN"
    log_info "后端端口: $BACKEND_PORT"
    echo ""

    check_root
    detect_os
    install_basics
    install_python
    install_nodejs
    install_nginx
    configure_firewall
    create_app_directories
    configure_nginx "$DOMAIN" "$BACKEND_PORT"
    configure_backend_service "$BACKEND_PORT"
    optimize_system

    # 如果域名已配置，安装SSL证书
    if [ "$DOMAIN" != "your_domain.com" ] && [ "$EMAIL" != "admin@example.com" ]; then
        install_ssl_certificate "$DOMAIN" "$EMAIL"
    else
        log_warning "跳过SSL证书安装（域名或邮箱未配置）"
    fi

    show_summary "$DOMAIN"

    log_success "服务器初始化脚本执行完成！"
}

# 执行主函数
main "$@"
