#!/bin/bash

# 确认部署来源是远端已有的确定提交，且部署目录没有额外工作区变更。
check_release_source() {
    local head_sha
    local remote_refs
    head_sha="$(git rev-parse HEAD)"

    if [ -n "$EXPECTED_SHA" ] && [ "$head_sha" != "$EXPECTED_SHA" ]; then
        log_error "当前 HEAD 与期望发布 SHA 不一致"
        exit 1
    fi
    EXPECTED_SHA="$head_sha"

    remote_refs="$(git ls-remote origin | awk '{print $1}')"
    if ! grep -qx "$EXPECTED_SHA" <<< "$remote_refs"; then
        log_error "发布提交尚未推送到 origin: $EXPECTED_SHA"
        exit 1
    fi

    if [ -n "$(git status --porcelain --untracked-files=all -- frontend backend deploy)" ]; then
        log_error "部署目录含未提交内容；禁止从混合工作区发布"
        git status --short -- frontend backend deploy
        exit 1
    fi

    log_success "发布来源校验通过: $EXPECTED_SHA"
}

# 输出远端服务、磁盘和近期日志状态。
show_status() {
    log_info "检查部署状态..."

    remote_exec bash << 'ENDSSH'
        echo "========== 服务状态 =========="

        echo -e "\n【后端服务】"
        sudo systemctl status everydayai-backend --no-pager | head -n 10

        echo -e "\n【同步服务】"
        if systemctl is-enabled everydayai-sync &>/dev/null; then
            sudo systemctl status everydayai-sync --no-pager | head -n 10
        else
            echo "（未安装）"
        fi

        echo -e "\n【企微与 Actor】"
        sudo systemctl status everydayai-wecom --no-pager | head -n 10
        sudo systemctl status everydayai-conversation-actor --no-pager | head -n 10

        echo -e "\n【Nginx服务】"
        sudo systemctl status nginx --no-pager | head -n 10

        echo -e "\n【磁盘使用】"
        df -h /var/www/everydayai

        echo -e "\n【最近日志】"
        echo "后端日志（最后10行）:"
        sudo journalctl -u everydayai-backend -n 10 --no-pager
        if systemctl is-enabled everydayai-sync &>/dev/null; then
            echo -e "\n同步日志（最后10行）:"
            sudo journalctl -u everydayai-sync -n 10 --no-pager
        fi
ENDSSH

    log_success "状态检查完成"
}

# 从公网入口验证前端和后端健康状态。
verify_public_endpoints() {
    log_info "验证公网访问..."
    curl --fail --silent --show-error "https://${DOMAIN}/" >/dev/null
    curl --fail --silent --show-error "https://${DOMAIN}/api/health" \
        | grep -q '"status":"ok"'
    log_success "公网前端和后端健康检查通过"
}
