#!/bin/bash

###############################################################################
# 自动部署脚本 - EVERYDAYAIONE
# 用途：将前后端代码部署到阿里云ECS服务器
# 使用方法：./deploy/deploy.sh [选项]
###############################################################################

set -e  # 遇到错误立即退出

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
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

# 显示帮助信息
show_help() {
    cat << EOF
使用方法: $0 [选项]

选项:
    -h, --help              显示此帮助信息
    -s, --setup             首次部署，执行服务器初始化
    -f, --frontend-only     仅部署前端
    -b, --backend-only      仅部署后端
    --skip-build           跳过构建步骤
    --skip-test            跳过测试

示例:
    $0 -s                   首次部署（包含服务器初始化）
    $0                      正常部署（前后端都部署）
    $0 -f                   仅部署前端
    $0 -b                   仅部署后端
EOF
}

# 检查配置文件
check_config() {
    if [ ! -f "deploy/config.env" ]; then
        log_error "配置文件 deploy/config.env 不存在"
        log_info "正在创建配置文件模板..."
        cat > deploy/config.env << 'EOF'
# 服务器配置
SERVER_HOST=your_server_ip_or_domain
SERVER_USER=root
SERVER_PORT=22

# 部署路径
REMOTE_APP_DIR=/var/www/everydayai
REMOTE_FRONTEND_DIR=/var/www/everydayai/frontend
REMOTE_BACKEND_DIR=/var/www/everydayai/backend

# 域名配置（用于Nginx和SSL）
DOMAIN=your_domain.com
EMAIL=your_email@example.com

# 服务配置
BACKEND_PORT=8000
FRONTEND_PORT=3000

# 数据库迁移（可选）
RUN_MIGRATIONS=true
EOF
        log_error "请编辑 deploy/config.env 填写服务器信息后重新运行"
        exit 1
    fi

    # 加载配置
    source deploy/config.env

    # 验证必填配置
    if [ "$SERVER_HOST" = "your_server_ip_or_domain" ]; then
        log_error "请在 deploy/config.env 中配置 SERVER_HOST"
        exit 1
    fi
}

# 检查必要工具
check_dependencies() {
    log_info "检查本地依赖..."

    local missing_deps=()

    if ! command -v rsync &> /dev/null; then
        missing_deps+=("rsync")
    fi

    if ! command -v ssh &> /dev/null; then
        missing_deps+=("ssh")
    fi

    if [ ${#missing_deps[@]} -gt 0 ]; then
        log_error "缺少必要工具: ${missing_deps[*]}"
        log_info "请先安装: brew install ${missing_deps[*]}"
        exit 1
    fi

    log_success "本地依赖检查完成"
}

# 测试SSH连接
test_ssh_connection() {
    log_info "测试SSH连接: ${SERVER_USER}@${SERVER_HOST}:${SERVER_PORT}..."

    if ssh -p ${SERVER_PORT} -o ConnectTimeout=10 -o BatchMode=yes ${SERVER_USER}@${SERVER_HOST} "echo 'SSH连接成功'" &> /dev/null; then
        log_success "SSH连接测试成功"
    else
        log_error "SSH连接失败，请检查："
        log_error "1. 服务器地址和端口是否正确"
        log_error "2. SSH密钥是否已配置（建议使用密钥认证）"
        log_error "3. 防火墙是否允许SSH连接"
        exit 1
    fi
}

# 构建前端
build_frontend() {
    if [ "$SKIP_BUILD" = true ]; then
        log_warning "跳过前端构建"
        return
    fi

    log_info "开始构建前端..."

    cd frontend

    # 检查 node_modules
    if [ ! -d "node_modules" ]; then
        log_info "安装前端依赖..."
        npm install
    fi

    # 运行测试（可选）
    if [ "$SKIP_TEST" != true ]; then
        log_info "运行前端测试..."
        npm run test:run || log_warning "前端测试失败，继续部署"
    fi

    # 构建
    log_info "执行前端构建..."
    npm run build

    if [ ! -d "dist" ]; then
        log_error "前端构建失败，dist 目录不存在"
        exit 1
    fi

    cd ..
    log_success "前端构建完成"
}

# 构建后端（检查语法和依赖）
build_backend() {
    if [ "$SKIP_BUILD" = true ]; then
        log_warning "跳过后端构建检查"
        return
    fi

    log_info "开始后端构建检查..."

    cd backend

    # 检查虚拟环境
    if [ ! -d "venv" ]; then
        log_info "创建Python虚拟环境..."
        python3 -m venv venv
    fi

    # 激活虚拟环境
    source venv/bin/activate

    # 安装依赖
    log_info "检查后端依赖..."
    pip install -q -r requirements.txt

    # 运行测试（可选）
    if [ "$SKIP_TEST" != true ]; then
        log_info "运行后端测试..."
        pytest || log_warning "后端测试失败，继续部署"
    fi

    # 语法检查
    log_info "Python语法检查..."
    python3 -m py_compile main.py || {
        log_error "Python语法检查失败"
        exit 1
    }

    deactivate
    cd ..
    log_success "后端构建检查完成"
}

# 同步前端文件到服务器
sync_frontend() {
    log_info "同步前端文件到服务器..."

    rsync -avz --delete \
        -e "ssh -p ${SERVER_PORT}" \
        --exclude 'node_modules' \
        --exclude '.env' \
        --exclude '.env.local' \
        frontend/dist/ \
        ${SERVER_USER}@${SERVER_HOST}:${REMOTE_FRONTEND_DIR}/

    log_success "前端文件同步完成"
}

# 同步后端文件到服务器
sync_backend() {
    log_info "同步后端文件到服务器..."

    rsync -avz --delete \
        -e "ssh -p ${SERVER_PORT}" \
        --exclude 'venv' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.env' \
        --exclude '*.log' \
        --exclude '.pytest_cache' \
        backend/ \
        ${SERVER_USER}@${SERVER_HOST}:${REMOTE_BACKEND_DIR}/

    log_success "后端文件同步完成"
}

# 在服务器上执行命令
remote_exec() {
    ssh -p ${SERVER_PORT} ${SERVER_USER}@${SERVER_HOST} "$@"
}

# 部署后端到服务器
deploy_backend() {
    log_info "在服务器上部署后端..."

    remote_exec bash << 'ENDSSH'
        set -e

        echo "1. 进入后端目录"
        cd /var/www/everydayai/backend

        echo "2. 创建虚拟环境（如果不存在）"
        if [ ! -d "venv" ]; then
            python3 -m venv venv
        fi

        echo "3. 激活虚拟环境并安装依赖"
        source venv/bin/activate
        pip install -q -r requirements.txt

        echo "4. 检查.env文件"
        if [ ! -f ".env" ]; then
            echo "警告: .env 文件不存在，请手动配置"
        fi

        echo "5. 重启后端服务"
        sudo systemctl restart everydayai-backend

        echo "6. 检查服务状态"
        sudo systemctl status everydayai-backend --no-pager
ENDSSH

    log_success "后端部署完成"
}

# 部署前端到服务器（重载Nginx）
deploy_frontend() {
    log_info "在服务器上部署前端..."

    remote_exec bash << 'ENDSSH'
        set -e

        echo "1. 检查前端文件"
        ls -lh /var/www/everydayai/frontend/

        echo "2. 测试Nginx配置"
        sudo nginx -t

        echo "3. 重载Nginx"
        sudo systemctl reload nginx

        echo "4. 检查Nginx状态"
        sudo systemctl status nginx --no-pager
ENDSSH

    log_success "前端部署完成"
}

# 首次部署 - 服务器初始化
setup_server() {
    log_info "开始首次部署服务器初始化..."

    # 上传初始化脚本和配置文件
    log_info "上传服务器配置文件..."
    scp -P ${SERVER_PORT} deploy/setup-server.sh ${SERVER_USER}@${SERVER_HOST}:/tmp/
    scp -P ${SERVER_PORT} deploy/nginx.conf ${SERVER_USER}@${SERVER_HOST}:/tmp/
    scp -P ${SERVER_PORT} deploy/everydayai-backend.service ${SERVER_USER}@${SERVER_HOST}:/tmp/

    # 在服务器上执行初始化
    log_info "在服务器上执行初始化脚本..."
    remote_exec bash << ENDSSH
        chmod +x /tmp/setup-server.sh
        sudo /tmp/setup-server.sh ${DOMAIN} ${EMAIL} ${BACKEND_PORT}
ENDSSH

    log_success "服务器初始化完成"
}

# 显示部署状态
show_status() {
    log_info "检查部署状态..."

    remote_exec bash << 'ENDSSH'
        echo "========== 服务状态 =========="

        echo -e "\n【后端服务】"
        sudo systemctl status everydayai-backend --no-pager | head -n 10

        echo -e "\n【Nginx服务】"
        sudo systemctl status nginx --no-pager | head -n 10

        echo -e "\n【磁盘使用】"
        df -h /var/www/everydayai

        echo -e "\n【最近日志】"
        echo "后端日志（最后10行）:"
        sudo journalctl -u everydayai-backend -n 10 --no-pager
ENDSSH

    log_success "状态检查完成"
}

# 主函数
main() {
    echo -e "${GREEN}"
    cat << 'EOF'
╔═══════════════════════════════════════════════╗
║   EVERYDAYAIONE 自动部署脚本                 ║
║   前后端分离 + Nginx + Systemd + SSL         ║
╚═══════════════════════════════════════════════╝
EOF
    echo -e "${NC}"

    # 解析命令行参数
    SETUP_MODE=false
    FRONTEND_ONLY=false
    BACKEND_ONLY=false
    SKIP_BUILD=false
    SKIP_TEST=false

    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_help
                exit 0
                ;;
            -s|--setup)
                SETUP_MODE=true
                shift
                ;;
            -f|--frontend-only)
                FRONTEND_ONLY=true
                shift
                ;;
            -b|--backend-only)
                BACKEND_ONLY=true
                shift
                ;;
            --skip-build)
                SKIP_BUILD=true
                shift
                ;;
            --skip-test)
                SKIP_TEST=true
                shift
                ;;
            *)
                log_error "未知选项: $1"
                show_help
                exit 1
                ;;
        esac
    done

    # 检查配置和依赖
    check_config
    check_dependencies
    test_ssh_connection

    # 首次部署模式
    if [ "$SETUP_MODE" = true ]; then
        setup_server
    fi

    # 部署流程
    if [ "$BACKEND_ONLY" != true ]; then
        build_frontend
        sync_frontend
        deploy_frontend
    fi

    if [ "$FRONTEND_ONLY" != true ]; then
        build_backend
        sync_backend
        deploy_backend
    fi

    # 显示状态
    show_status

    # 完成提示
    echo ""
    log_success "========== 部署完成 =========="
    log_info "前端访问地址: https://${DOMAIN}"
    log_info "后端API地址: https://${DOMAIN}/api"
    log_info "查看实时日志: ssh ${SERVER_USER}@${SERVER_HOST} -p ${SERVER_PORT} 'sudo journalctl -u everydayai-backend -f'"
    echo ""
}

# 执行主函数
main "$@"
