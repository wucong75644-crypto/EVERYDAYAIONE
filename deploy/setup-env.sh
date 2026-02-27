#!/bin/bash

###############################################################################
# 环境变量配置助手 - EVERYDAYAIONE
# 用途：帮助在服务器上配置后端环境变量
# 使用方法：在服务器上运行 bash setup-env.sh
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

# 提示输入
prompt_input() {
    local prompt="$1"
    local default="$2"
    local secret="$3"
    local value=""

    if [ "$secret" = "true" ]; then
        read -s -p "${BLUE}${prompt}${NC} [默认: ${default}]: " value
        echo ""
    else
        read -p "${BLUE}${prompt}${NC} [默认: ${default}]: " value
    fi

    if [ -z "$value" ]; then
        echo "$default"
    else
        echo "$value"
    fi
}

# 生成随机密钥
generate_secret() {
    openssl rand -hex 32
}

# 主函数
main() {
    echo -e "${GREEN}"
    cat << 'EOF'
╔═══════════════════════════════════════════════╗
║   EVERYDAYAIONE 环境变量配置助手             ║
║   交互式配置后端.env文件                     ║
╚═══════════════════════════════════════════════╝
EOF
    echo -e "${NC}"

    ENV_FILE="/var/www/everydayai/backend/.env"

    # 检查是否已存在
    if [ -f "$ENV_FILE" ]; then
        log_warning ".env文件已存在: $ENV_FILE"
        read -p "是否覆盖？(y/N): " confirm
        if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
            log_info "取消配置"
            exit 0
        fi
        # 备份旧文件
        cp "$ENV_FILE" "${ENV_FILE}.backup.$(date +%s)"
        log_info "已备份旧文件"
    fi

    log_info "开始配置环境变量..."
    echo ""

    # 基础配置
    log_info "=== 基础配置 ==="
    APP_ENV=$(prompt_input "应用环境 (development/production)" "production")
    APP_DEBUG=$(prompt_input "调试模式 (true/false)" "false")
    APP_HOST=$(prompt_input "监听地址" "0.0.0.0")
    APP_PORT=$(prompt_input "监听端口" "8000")
    echo ""

    # JWT配置
    log_info "=== JWT认证配置 ==="
    JWT_SECRET=$(prompt_input "JWT密钥 (留空自动生成)" "$(generate_secret)" "true")
    JWT_ALGORITHM=$(prompt_input "JWT算法" "HS256")
    JWT_EXPIRE=$(prompt_input "JWT过期时间（分钟）" "1440")
    echo ""

    # Supabase配置
    log_info "=== Supabase数据库配置 ==="
    log_warning "请从Supabase控制台获取以下信息"
    SUPABASE_URL=$(prompt_input "Supabase URL" "https://your-project.supabase.co")
    SUPABASE_ANON_KEY=$(prompt_input "Supabase Anon Key" "" "true")
    SUPABASE_SERVICE_KEY=$(prompt_input "Supabase Service Role Key" "" "true")
    echo ""

    # Redis配置
    log_info "=== Redis配置 ==="
    REDIS_HOST=$(prompt_input "Redis主机" "localhost")
    REDIS_PORT=$(prompt_input "Redis端口" "6379")
    REDIS_PASSWORD=$(prompt_input "Redis密码（可选）" "" "true")
    REDIS_DB=$(prompt_input "Redis数据库编号" "0")
    REDIS_SSL=$(prompt_input "Redis SSL (true/false)" "false")
    echo ""

    # 阿里云短信配置
    log_info "=== 阿里云短信配置 ==="
    read -p "是否配置阿里云短信？(y/N): " configure_sms
    if [ "$configure_sms" = "y" ] || [ "$configure_sms" = "Y" ]; then
        ALIYUN_SMS_ACCESS_KEY_ID=$(prompt_input "AccessKey ID" "" "true")
        ALIYUN_SMS_ACCESS_KEY_SECRET=$(prompt_input "AccessKey Secret" "" "true")
        ALIYUN_SMS_SIGN_NAME=$(prompt_input "短信签名" "")
        ALIYUN_SMS_TEMPLATE_REGISTER=$(prompt_input "注册模板CODE" "SMS_000000000")
        ALIYUN_SMS_TEMPLATE_RESET_PWD=$(prompt_input "重置密码模板CODE" "SMS_000000000")
        ALIYUN_SMS_TEMPLATE_BIND_PHONE=$(prompt_input "绑定手机模板CODE" "SMS_000000000")
    else
        ALIYUN_SMS_ACCESS_KEY_ID=""
        ALIYUN_SMS_ACCESS_KEY_SECRET=""
        ALIYUN_SMS_SIGN_NAME=""
        ALIYUN_SMS_TEMPLATE_REGISTER=""
        ALIYUN_SMS_TEMPLATE_RESET_PWD=""
        ALIYUN_SMS_TEMPLATE_BIND_PHONE=""
    fi
    echo ""

    # 阿里云OSS配置
    log_info "=== 阿里云OSS配置 ==="
    read -p "是否配置阿里云OSS？(y/N): " configure_oss
    if [ "$configure_oss" = "y" ] || [ "$configure_oss" = "Y" ]; then
        OSS_ACCESS_KEY_ID=$(prompt_input "OSS AccessKey ID" "" "true")
        OSS_ACCESS_KEY_SECRET=$(prompt_input "OSS AccessKey Secret" "" "true")
        OSS_BUCKET_NAME=$(prompt_input "OSS Bucket名称" "")
        OSS_ENDPOINT=$(prompt_input "OSS Endpoint" "oss-cn-hangzhou.aliyuncs.com")
        OSS_REGION=$(prompt_input "OSS Region" "cn-hangzhou")
    else
        OSS_ACCESS_KEY_ID=""
        OSS_ACCESS_KEY_SECRET=""
        OSS_BUCKET_NAME=""
        OSS_ENDPOINT=""
        OSS_REGION=""
    fi
    echo ""

    # KIE API配置
    log_info "=== KIE AI API配置 ==="
    KIE_API_KEY=$(prompt_input "KIE API Key" "" "true")
    KIE_BASE_URL=$(prompt_input "KIE Base URL" "https://api.kie.ai/v1")
    echo ""

    # 限流配置
    log_info "=== 限流配置 ==="
    RATE_LIMIT_GLOBAL=$(prompt_input "全局任务限制（每用户）" "15")
    RATE_LIMIT_CONVERSATION=$(prompt_input "会话任务限制（每会话）" "5")
    echo ""

    # 写入.env文件
    log_info "正在生成.env文件..."

    cat > "$ENV_FILE" << EOF
# ===================================
# EVERYDAYAIONE 后端环境变量配置
# 生成时间: $(date)
# ===================================

# 应用基础配置
APP_ENV=$APP_ENV
APP_DEBUG=$APP_DEBUG
APP_HOST=$APP_HOST
APP_PORT=$APP_PORT

# JWT认证配置
JWT_SECRET_KEY=$JWT_SECRET
JWT_ALGORITHM=$JWT_ALGORITHM
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=$JWT_EXPIRE

# Supabase数据库配置
SUPABASE_URL=$SUPABASE_URL
SUPABASE_ANON_KEY=$SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY=$SUPABASE_SERVICE_KEY

# Redis配置
REDIS_HOST=$REDIS_HOST
REDIS_PORT=$REDIS_PORT
REDIS_PASSWORD=$REDIS_PASSWORD
REDIS_DB=$REDIS_DB
REDIS_SSL=$REDIS_SSL

# 阿里云短信配置
ALIYUN_SMS_ACCESS_KEY_ID=$ALIYUN_SMS_ACCESS_KEY_ID
ALIYUN_SMS_ACCESS_KEY_SECRET=$ALIYUN_SMS_ACCESS_KEY_SECRET
ALIYUN_SMS_SIGN_NAME=$ALIYUN_SMS_SIGN_NAME
ALIYUN_SMS_TEMPLATE_REGISTER=$ALIYUN_SMS_TEMPLATE_REGISTER
ALIYUN_SMS_TEMPLATE_RESET_PWD=$ALIYUN_SMS_TEMPLATE_RESET_PWD
ALIYUN_SMS_TEMPLATE_BIND_PHONE=$ALIYUN_SMS_TEMPLATE_BIND_PHONE

# 阿里云OSS配置
OSS_ACCESS_KEY_ID=$OSS_ACCESS_KEY_ID
OSS_ACCESS_KEY_SECRET=$OSS_ACCESS_KEY_SECRET
OSS_BUCKET_NAME=$OSS_BUCKET_NAME
OSS_ENDPOINT=$OSS_ENDPOINT
OSS_REGION=$OSS_REGION

# KIE API配置
KIE_API_KEY=$KIE_API_KEY
KIE_BASE_URL=$KIE_BASE_URL

# 限流配置
RATE_LIMIT_GLOBAL_TASKS=$RATE_LIMIT_GLOBAL
RATE_LIMIT_CONVERSATION_TASKS=$RATE_LIMIT_CONVERSATION
EOF

    # 设置权限
    chmod 600 "$ENV_FILE"

    log_success ".env文件已创建: $ENV_FILE"
    log_info "文件权限已设置为600（仅所有者可读写）"
    echo ""

    log_success "========== 配置完成 =========="
    log_warning "重要提醒："
    log_warning "1. 请检查配置是否正确: cat $ENV_FILE"
    log_warning "2. 如需修改，可直接编辑: vim $ENV_FILE"
    log_warning "3. 修改后需重启服务: sudo systemctl restart everydayai-backend"
    echo ""
}

main "$@"
