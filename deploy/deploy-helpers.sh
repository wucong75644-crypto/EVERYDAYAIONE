#!/bin/bash

DEPLOY_LOG_DIR="${DEPLOY_LOG_DIR:-}"
DEPLOY_LOG_FILE="${DEPLOY_LOG_FILE:-}"
DEPLOY_STAGE_INDEX=0

init_deploy_log() {
    if [ -z "$DEPLOY_LOG_DIR" ]; then
        DEPLOY_LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/everydayai-deploy.XXXXXX")"
    fi
    DEPLOY_LOG_FILE="${DEPLOY_LOG_FILE:-$DEPLOY_LOG_DIR/deployment.log}"
    : > "$DEPLOY_LOG_FILE"
}

# 成功时只返回阶段摘要；失败时保留完整日志并展开末尾关键内容。
run_stage() {
    local label="$1"
    local stage_log
    local status
    shift

    [ -n "$DEPLOY_LOG_FILE" ] || init_deploy_log
    DEPLOY_STAGE_INDEX=$((DEPLOY_STAGE_INDEX + 1))
    stage_log="$DEPLOY_LOG_DIR/stage-${DEPLOY_STAGE_INDEX}.log"
    log_info "$label..."

    set +e
    (
        set -e
        "$@"
    ) >"$stage_log" 2>&1
    status=$?
    set -e

    if [ "$status" -eq 0 ]; then
        {
            printf '\n===== %s =====\n' "$label"
            cat "$stage_log"
        } >> "$DEPLOY_LOG_FILE"
        log_success "$label"
        return 0
    fi

    {
        printf '\n===== %s (failed: %s) =====\n' "$label" "$status"
        cat "$stage_log"
    } >> "$DEPLOY_LOG_FILE"
    log_error "${label}（退出码: ${status}）"
    tail -n "${DEPLOY_FAILURE_LINES:-80}" "$stage_log" >&2
    log_error "完整日志: $DEPLOY_LOG_FILE"
    return "$status"
}

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

# 从公网入口验证前端和后端健康状态。
verify_public_endpoints() {
    log_info "验证公网访问..."
    curl --fail --silent --show-error "https://${DOMAIN}/" >/dev/null
    curl --fail --silent --show-error "https://${DOMAIN}/api/health" \
        | grep -q '"status":"ok"'
    log_success "公网前端和后端健康检查通过"
}
