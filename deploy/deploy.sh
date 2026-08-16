#!/bin/bash

###############################################################################
# 自动部署脚本 - EVERYDAYAIONE
# 用途：将前后端代码部署到阿里云ECS服务器
# 使用方法：./deploy/deploy.sh [选项]
###############################################################################

set -euo pipefail  # 任一门禁失败都停止部署

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

source deploy/deploy-helpers.sh
PYTHON_BIN="${PYTHON_BIN:-}"

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
    --expected-sha SHA     只部署指定且已推送的 Git 提交
    --runtime-flags-off-install  仅安装 flags-off Runtime 单元，不迁移或启停服务
    --runtime-control-plane-flags-off-update --expected-unit-manifest PATH  reviewed 更新三控制面 unit
示例:
    $0 -s                   首次部署（包含服务器初始化）
    $0                      正常部署（前后端都部署）
    $0 -f                   仅部署前端
    $0 -b                   仅部署后端
    $0 --runtime-flags-off-install  安装四个关闭状态的 Runtime 单元
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
RUN_MIGRATIONS=false
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

    if [ -z "$PYTHON_BIN" ]; then
        PYTHON_BIN="$(command -v python3.12 || command -v python3 || true)"
    fi
    if [ -z "$PYTHON_BIN" ]; then
        missing_deps+=("python3.12")
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
        npm run test:run
    fi

    # 构建
    log_info "执行前端构建..."
    rm -rf dist
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
        "$PYTHON_BIN" -m venv venv
    fi

    # 激活虚拟环境
    source venv/bin/activate

    # 安装依赖
    log_info "检查后端依赖..."
    pip install -q -r requirements.txt

    # 运行测试（可选）
    if [ "$SKIP_TEST" != true ]; then
        log_info "运行后端测试..."
        venv/bin/python -m pytest
    fi

    # 语法检查
    log_info "Python语法检查..."
    "$PYTHON_BIN" -m py_compile main.py || {
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

    # 先上传所有带哈希资源，确保新版入口发布时依赖已经就绪。
    rsync -az \
        -e "ssh -p ${SERVER_PORT}" \
        frontend/dist/assets/ \
        ${SERVER_USER}@${SERVER_HOST}:${REMOTE_FRONTEND_DIR}/assets/

    # 最后发布入口文件；assets 排除在删除范围外，在线旧页面仍可加载旧 chunk。
    rsync -az --delete \
        -e "ssh -p ${SERVER_PORT}" \
        --exclude 'assets/' \
        --exclude '.DS_Store' \
        frontend/dist/ \
        ${SERVER_USER}@${SERVER_HOST}:${REMOTE_FRONTEND_DIR}/

    # 仅清理超过保留窗口的旧资源，避免目录无限增长。
    remote_exec find "${REMOTE_FRONTEND_DIR}/assets" -type f -mtime +14 -delete

    log_success "前端文件同步完成"
}

# 同步后端文件到服务器
sync_backend() {
    log_info "同步后端文件到服务器..."

    rsync -az --delete \
        -e "ssh -p ${SERVER_PORT}" \
        --exclude 'venv' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.env*' \
        --exclude '*.log' \
        --exclude '*.db' \
        --exclude '*.sqlite' \
        --exclude '*.sqlite3' \
        --exclude '.pytest_cache' \
        --exclude '.coverage' \
        --exclude 'coverage/' \
        --exclude 'tmp/' \
        --exclude 'outputs/' \
        --exclude 'external/mediacrawler' \
        backend/ \
        ${SERVER_USER}@${SERVER_HOST}:${REMOTE_BACKEND_DIR}/

    # 同步部署配置（sandbox.cfg 等）
    rsync -az \
        -e "ssh -p ${SERVER_PORT}" \
        --exclude '.env*' \
        --exclude 'config.env*' \
        --exclude '__pycache__' \
        deploy/ \
        ${SERVER_USER}@${SERVER_HOST}:${REMOTE_BACKEND_DIR}/../deploy/

    log_success "后端文件同步完成"
}

# 在服务器上执行命令
remote_exec() {
    ssh -p ${SERVER_PORT} ${SERVER_USER}@${SERVER_HOST} "$@"
}

# 部署后端到服务器
deploy_backend() {
    log_info "在服务器上部署后端..."

    remote_exec "RUN_MIGRATIONS=${RUN_MIGRATIONS:-false} RECONCILE_FAILED_MIGRATION=${RECONCILE_FAILED_MIGRATION:-} ACKNOWLEDGE_MIGRATION_ROLLBACK=${ACKNOWLEDGE_MIGRATION_ROLLBACK:-false} bash -s" << 'ENDSSH'
        set -e
        cd /var/www/everydayai/backend
        if [ ! -d "venv" ]; then
            python3 -m venv venv
        fi
        source venv/bin/activate
        pip install -q -r requirements.txt
        if [ ! -f ".env" ]; then
            echo "❌ .env 文件不存在"
            exit 1
        fi
        sudo bash ../deploy/provision-runtime-users.sh
        sudo bash ../deploy/ensure-runtime-stream-env.sh \
            /var/www/everydayai/backend /etc/everydayai/agent-runtime-worker.env
        bash ../deploy/install-service-units.sh /var/www/everydayai/backend
        bash ../deploy/run-migrations.sh
        set -a
        source .env.runtime
        source .env.worker-client
        set +a
        ./venv/bin/python scripts/verify_runtime_generation_capabilities.py
        services=(
            everydayai-backend
            everydayai-sync
            everydayai-wecom
            everydayai-conversation-actor
            everydayai-agent-runtime
            everydayai-agent-projection
            everydayai-agent-authorization
        )
        for service in "${services[@]}"; do
            if ! systemctl list-unit-files "${service}.service" --no-legend \
                | grep -q "^${service}.service"; then
                echo "❌ 缺少必需服务: ${service}.service"
                exit 1
            fi
            sudo systemctl restart "$service"
            sudo systemctl is-active --quiet "$service" || {
                sudo journalctl -u "$service" -n 50 --no-pager
                exit 1
            }
        done
        runtime_health_sockets=(
            /run/everydayai-agent-runtime/health.sock
            /run/everydayai-agent-projection/health.sock
            /run/everydayai-agent-authorization/health.sock
        )
        for socket_path in "${runtime_health_sockets[@]}"; do
            for attempt in $(seq 1 20); do
                if [ -S "$socket_path" ]; then
                    break
                fi
                if [ "$attempt" -eq 20 ]; then
                    echo "❌ Runtime health socket 未就绪: $socket_path"
                    sudo systemctl --no-pager --full status \
                        "$(basename "${socket_path%/health.sock}")" || true
                    exit 1
                fi
                sleep 1
            done
        done
        sandbox_env=/etc/everydayai/sandbox-worker.env
        if [ -f "$sandbox_env" ]; then
            sudo systemctl restart everydayai-sandbox-worker
            sudo systemctl is-active --quiet everydayai-sandbox-worker || {
                sudo journalctl -u everydayai-sandbox-worker -n 50 --no-pager
                exit 1
            }
            for attempt in $(seq 1 20); do
                if [ -S /run/everydayai-sandbox-worker/health.sock ]; then
                    break
                fi
                if [ "$attempt" -eq 20 ]; then
                    echo "❌ Sandbox health socket 未就绪"
                    sudo journalctl -u everydayai-sandbox-worker -n 80 --no-pager
                    exit 1
                fi
                sleep 1
            done
        else
            if systemctl is-active --quiet everydayai-sandbox-worker; then
                echo "❌ Sandbox 环境文件缺失但服务仍在运行: $sandbox_env"
                exit 1
            fi
            sudo systemctl reset-failed everydayai-sandbox-worker || true
            echo "ℹ️ Sandbox 环境文件未配置，保持 Sandbox 停用"
        fi
        for attempt in $(seq 1 20); do
            if curl --fail --silent http://127.0.0.1:8000/api/health \
                | grep -q '"status":"ok"'; then
                break
            fi
            if [ "$attempt" -eq 20 ]; then
                echo "❌ 后端 readiness 超时"
                sudo journalctl -u everydayai-backend -n 80 --no-pager
                exit 1
            fi
            sleep 2
        done
        sudo journalctl -u everydayai-backend --since '30 seconds ago' \
            --no-pager | grep -q -E \
            'ErpSyncOrchestrator started|kuaimai_external_sync_loop started' && {
                echo "❌ backend 仍在启动 ERP 同步"
                exit 1
            }
        echo "✅ 后端与 Runtime 控制面服务和 readiness 检查通过"
ENDSSH

    log_success "后端部署完成"
}

# 部署前端到服务器（重载Nginx）
deploy_frontend() {
    log_info "在服务器上部署前端..."

    remote_exec bash << 'ENDSSH'
        set -e

        test -f /var/www/everydayai/frontend/index.html
        sudo nginx -t
        sudo systemctl reload nginx
        sudo systemctl is-active --quiet nginx
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
    scp -P ${SERVER_PORT} deploy/everydayai-sync.service ${SERVER_USER}@${SERVER_HOST}:/tmp/

    # 在服务器上执行初始化
    log_info "在服务器上执行初始化脚本..."
    remote_exec bash << ENDSSH
        chmod +x /tmp/setup-server.sh
        sudo /tmp/setup-server.sh ${DOMAIN} ${EMAIL} ${BACKEND_PORT}
ENDSSH

    log_success "服务器初始化完成"
}

# 主函数
main() {
    # 解析命令行参数
    SETUP_MODE=false
    FRONTEND_ONLY=false
    BACKEND_ONLY=false
    SKIP_BUILD=false
    SKIP_TEST=false
    EXPECTED_SHA=""

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
            --expected-sha)
                if [ $# -lt 2 ]; then
                    log_error "--expected-sha 缺少值"
                    exit 1
                fi
                EXPECTED_SHA="$2"
                shift 2
                ;;
            *)
                log_error "未知选项: $1"
                show_help
                exit 1
                ;;
        esac
    done

    if [ "$FRONTEND_ONLY" = true ] && [ "$BACKEND_ONLY" = true ]; then
        log_error "不能同时选择仅前端和仅后端"
        exit 1
    fi

    init_deploy_log
    run_stage "发布来源校验" check_release_source
    EXPECTED_SHA="$(git rev-parse HEAD)"
    run_stage "部署配置校验" check_config
    source deploy/config.env
    run_stage "本地依赖校验" check_dependencies
    PYTHON_BIN="$(command -v python3.12 || command -v python3)"
    run_stage "SSH 连接校验" test_ssh_connection

    # 首次部署模式
    if [ "$SETUP_MODE" = true ]; then
        run_stage "服务器初始化" setup_server
    fi

    # 部署流程
    if [ "$BACKEND_ONLY" != true ]; then
        run_stage "前端测试与构建" build_frontend
        run_stage "前端文件同步" sync_frontend
        run_stage "前端服务发布" deploy_frontend
    fi

    if [ "$FRONTEND_ONLY" != true ]; then
        run_stage "后端测试与检查" build_backend
        run_stage "后端文件同步" sync_backend
        run_stage "后端迁移与服务发布" deploy_backend
    fi

    run_stage "公网只读健康检查" verify_public_endpoints

    local deploy_scope="frontend+backend"
    [ "$FRONTEND_ONLY" = true ] && deploy_scope="frontend"
    [ "$BACKEND_ONLY" = true ] && deploy_scope="backend"
    printf 'DEPLOY_RESULT sha=%s scope=%s technical=passed automatic_validation=passed business_acceptance=pending_user log=%s\n' \
        "$EXPECTED_SHA" "$deploy_scope" "$DEPLOY_LOG_FILE"
}
if [[ " $* " == *" --runtime-flags-off-install "* ]] \
    || [[ " $* " == *" --runtime-control-plane-flags-off-update "* ]]; then
    exec bash deploy/runtime-flags-off-install.sh "$@"
fi
main "$@"
