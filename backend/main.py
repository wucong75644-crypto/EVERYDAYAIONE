"""
FastAPI 应用入口

EVERYDAYAI - AI 图片/视频生成平台后端服务
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.routes import (
    admin_users, audio, auth, conversation, detail_project, ecom_requirement, error_monitor, file, health, image, image_ecom,
    kuaimai_external, memory, message, models, org, org_members_assignments,
    runtime_admin,
    pdd, qimen, scheduled_tasks, subscription, task, webhook, wecom, wecom_auth,
    wecom_chat_targets, ws,
)
from core.config import get_settings
from core.exceptions import AppException
from core.local_db import RowNotFoundError
from core.limiter import limiter
from core.redis import RedisClient
from core.logging_config import setup_logging
from services.websocket_manager import ws_manager

# ============================================================
# 应用初始化：日志和错误监控
# ============================================================

# 1. 配置日志（文件 + 控制台）
setup_logging()


# 1.5 时间事实层 sanity check（设计文档：docs/document/TECH_ERP时间准确性架构.md §11.3）
def _time_arch_sanity_check() -> None:
    """启动时校验时区/tzdata 配置，失败 fail-fast。

    可设 SKIP_TIME_SANITY_CHECK=1 跳过（仅灾难恢复用）。
    """
    if os.environ.get("SKIP_TIME_SANITY_CHECK") == "1":
        logger.warning("[time-arch] SKIP_TIME_SANITY_CHECK=1，跳过时区校验")
        return

    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            tz = ZoneInfo("Asia/Shanghai")
        except ZoneInfoNotFoundError as e:
            raise RuntimeError(
                "tzdata 不可用，无法加载 Asia/Shanghai。"
                "请确保容器/服务器安装了 tzdata 包，"
                "或运行 pip install tzdata。"
                f"原始错误: {e}"
            )

        from datetime import datetime
        now_local = datetime.now(tz)
        process_tz = os.environ.get("TZ", "(unset)")
        logger.info(
            f"[time-arch] sanity check ok | "
            f"now={now_local.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
            f"TZ_env={process_tz} | tzdata=Asia/Shanghai"
        )

        # 检查 chinese-calendar 库覆盖年份
        from utils.holiday import check_coverage_at_startup
        check_coverage_at_startup()

        # 工具域注册完整性校验
        # org_id 传非 None 值触发 ERP 工具加载，确保校验覆盖全量工具
        from config.chat_tools import get_chat_tools
        from config.tool_domains import validate_registry
        _all_names = {t["function"]["name"] for t in get_chat_tools(org_id="__startup_check__")}
        _missing = validate_registry(_all_names)
        if _missing:
            logger.warning(
                f"[tool-domains] 未注册域的工具（默认拒绝访问）: {_missing}"
            )
        else:
            logger.info("[tool-domains] 所有工具域注册完整")
    except Exception as e:
        logger.error(f"[time-arch] sanity check FAILED | {e}")
        raise


_time_arch_sanity_check()

# 2. 配置 Sentry 错误监控（可选）
settings = get_settings()
if settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1,  # 性能监控采样率（10%）
        profiles_sample_rate=0.1,  # 性能分析采样率（10%）
    )
    logger.info(f"Sentry initialized | environment={settings.environment}")
else:
    logger.info("Sentry not configured | using log files only")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """添加安全响应头中间件"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # 防止点击劫持（SAMEORIGIN 允许同域 iframe，企微扫码 SDK 需要）
        response.headers["X-Frame-Options"] = "SAMEORIGIN"

        # 防止 MIME 类型嗅探
        response.headers["X-Content-Type-Options"] = "nosniff"

        # XSS 保护
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # 强制 HTTPS（仅生产环境）
        settings = get_settings()
        if not settings.app_debug:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Content Security Policy
        # 开发环境：保留 unsafe-inline 以支持 Vite HMR（热模块替换）
        # 生产环境：移除 unsafe-eval 和 unsafe-inline，使用严格策略
        # 未来优化：实现 nonce 或 hash 机制进一步提升安全性（见 docs/TECH_DEBT.md）
        if settings.app_debug:
            # 开发环境 CSP：允许内联脚本和样式（Vite 需要）
            csp_policy = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://wwcdn.weixin.qq.com; "  # Vite HMR + 企微 SDK
                "style-src 'self' 'unsafe-inline'; "  # Vite 样式注入需要
                "img-src 'self' data: https://*.aliyuncs.com https://cdn.everydayai.com.cn; "
                "media-src 'self' https://*.aliyuncs.com https://cdn.everydayai.com.cn; "
                "font-src 'self' data:; "
                "connect-src 'self' https://api.kie.ai ws://localhost:*; "
                "frame-src https://login.work.weixin.qq.com; "  # 企微扫码 iframe
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'; "
                "upgrade-insecure-requests;"
            )
        else:
            # 生产环境 CSP：严格策略，禁止 unsafe-eval 和 unsafe-inline
            csp_policy = (
                "default-src 'self'; "
                "script-src 'self' https://wwcdn.weixin.qq.com; "  # 企微扫码 SDK
                "style-src 'self'; "
                "img-src 'self' data: https://*.aliyuncs.com https://cdn.everydayai.com.cn; "
                "media-src 'self' https://*.aliyuncs.com https://cdn.everydayai.com.cn; "
                "font-src 'self' data:; "
                "connect-src 'self' https://api.kie.ai; "
                "frame-src https://login.work.weixin.qq.com; "  # 企微扫码 iframe
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'; "
                "upgrade-insecure-requests;"
            )
        response.headers["Content-Security-Policy"] = csp_policy

        # 推荐策略
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # 权限策略
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期管理

    启动时初始化资源，关闭时清理资源。
    """
    settings = get_settings()
    tool_probe_task: asyncio.Task | None = None
    logger.info(f"Starting EVERYDAYAI API | env={settings.app_env}")

    # 初始化 Redis 连接
    try:
        await RedisClient.get_client()
        logger.info("Redis 连接初始化成功")
    except Exception as e:
        logger.warning(f"Redis 连接失败，限流功能降级 | error={e}")

    tool_probe_task = await _start_tool_confirmation_probe(settings)

    # 启动 WebSocket Redis Pub/Sub 监听（跨 Worker 消息投递）
    await ws_manager.start_redis_listener()

    from services.web_database_runtime import (
        start_web_database_runtime,
        warm_knowledge_base,
    )

    await warm_knowledge_base()
    web_database_runtime = await start_web_database_runtime()

    # 以下后台任务已迁移到 everydayai-sync.service 独立进程，避免与 API 进程争抢
    # DB 连接池 + 简化生命周期管理：
    #   - ErpSyncOrchestrator + erp_healthcheck（原 leader election 已废弃）
    #   - kuaimai_external_sync_loop（每天 10:00 拉 thinktank/viperp）
    #   - oss_purge_loop（每天 03:00 清理 OSS）
    # 企微智能机器人 WS 长连接已拆为独立进程（wecom_ws_runner.py），由 everydayai-wecom.service 管理

    yield

    if tool_probe_task is not None:
        tool_probe_task.cancel()
        try:
            await tool_probe_task
        except asyncio.CancelledError:
            pass

    # 优雅关闭：通知所有 WebSocket 客户端服务即将重启
    from schemas.websocket import build_server_restarting
    await ws_manager.broadcast_all(build_server_restarting())
    await asyncio.sleep(1)  # 给客户端一点时间接收消息

    # 停止 WebSocket Redis Pub/Sub 监听
    await ws_manager.stop_redis_listener()

    await web_database_runtime.stop()

    # 关闭 Redis 连接
    await RedisClient.close()
    logger.info("Shutting down EVERYDAYAI API")


async def _start_tool_confirmation_probe(settings) -> asyncio.Task | None:
    from services.tool_confirmation import tool_confirmation_service

    if not settings.tool_confirmation_v3_enabled:
        tool_confirmation_service.set_available(False)
        return None
    from services.tool_confirmation.capability_probe import (
        probe_tool_confirmation_redis,
    )
    probe = await probe_tool_confirmation_redis()
    tool_confirmation_service.set_available(probe.ready)
    if not probe.ready:
        logger.error("Tool Confirmation V3 remains closed | code={}", probe.code)
    try:
        capability_db = await _tool_confirmation_capability_db()
        await _report_tool_confirmation_capability(capability_db, probe)

        async def probe_loop() -> None:
            while True:
                await asyncio.sleep(30)
                current = await probe_tool_confirmation_redis()
                try:
                    await _report_tool_confirmation_capability(
                        capability_db, current,
                    )
                    tool_confirmation_service.set_available(current.ready)
                except Exception as error:
                    tool_confirmation_service.set_available(False)
                    logger.error(
                        "Tool Confirmation capability refresh failed | type={}",
                        type(error).__name__,
                    )

        return asyncio.create_task(probe_loop())
    except Exception as error:
        tool_confirmation_service.set_available(False)
        logger.error(
            "Tool Confirmation capability fact unavailable | type={}",
            type(error).__name__,
        )
        return None


async def _tool_confirmation_capability_db():
    from core.database import get_async_db
    from core.db_scope import (
        AsyncScopedDatabaseClient,
        DatabaseAccessKind,
        DatabaseScope,
    )
    return AsyncScopedDatabaseClient(
        await get_async_db(),
        DatabaseScope(
            actor_user_id=None, org_id=None,
            access_kind=DatabaseAccessKind.RUNTIME,
            request_id="startup-tool-confirmation-v3-probe",
        ),
    )


async def _report_tool_confirmation_capability(database, probe) -> None:
    await database.rpc(
        "report_agent_runtime_capability", {
            "p_capability_name": "tool_confirmation_v3_redis",
            "p_ready": probe.ready,
            "p_evidence": {"code": probe.code},
        },
    ).execute()


def create_app() -> FastAPI:
    """
    创建 FastAPI 应用实例

    Returns:
        配置好的 FastAPI 应用
    """
    settings = get_settings()

    app = FastAPI(
        title="EVERYDAYAI API",
        description="AI 图片/视频生成平台后端服务",
        version="1.0.0",
        docs_url="/docs" if settings.app_debug else None,
        redoc_url="/redoc" if settings.app_debug else None,
        lifespan=lifespan,
    )

    # CORS 配置（从环境变量读取）
    allowed_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
    )

    # 安全响应头
    app.add_middleware(SecurityHeadersMiddleware)

    # 限流配置
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # 注册异常处理器
    register_exception_handlers(app)

    # 注册路由
    register_routers(app)

    return app


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器"""

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """记录请求校验失败的详细信息"""
        logger.warning(
            f"ValidationError | path={request.url.path} | "
            f"content_type={request.headers.get('content-type', 'N/A')} | "
            f"detail={exc.errors()}"
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数校验失败",
                    "details": exc.errors(),
                }
            },
        )

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        """处理应用自定义异常"""
        logger.warning(
            f"AppException | code={exc.code} | message={exc.message} | "
            f"path={request.url.path} | details={exc.details}"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(RowNotFoundError)
    async def row_not_found_handler(request: Request, exc: RowNotFoundError) -> JSONResponse:
        """single() 查询未找到行 → 404"""
        logger.warning(
            f"Row not found | path={request.url.path} | table={exc.table}"
        )
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "NOT_FOUND",
                    "message": "请求的资源不存在",
                    "details": {},
                }
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """处理未捕获的异常"""
        logger.exception(
            f"Unhandled exception | path={request.url.path} | error={str(exc)}"
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务器内部错误，请稍后重试",
                    "details": {},
                }
            },
        )


def register_routers(app: FastAPI) -> None:
    """注册 API 路由"""

    # 健康检查
    app.include_router(health.router, prefix="/api")

    # 认证
    app.include_router(auth.router, prefix="/api")

    # 对话
    app.include_router(conversation.router, prefix="/api")

    # 消息
    app.include_router(message.router, prefix="/api")
    app.include_router(message.message_router, prefix="/api")  # 独立消息操作

    # 图像上传（生成功能已迁移到 /messages/generate）
    app.include_router(image.router, prefix="/api")

    # 主图详情页草稿
    app.include_router(detail_project.router, prefix="/api")

    # 电商图模式（提示词增强 + 单张重试）
    app.include_router(image_ecom.router, prefix="/api")

    # 电商图 AI 帮写（三套通用创作简报）
    app.include_router(ecom_requirement.router, prefix="/api")

    # 文件上传（PDF 等文档）
    app.include_router(file.router, prefix="/api")

    # 音频上传
    app.include_router(audio.router, prefix="/api")

    # 记忆
    app.include_router(memory.router, prefix="/api")

    # 任务管理
    app.include_router(task.router, prefix="/api")

    # Webhook 回调（无需用户鉴权，Provider 直接调用）
    app.include_router(webhook.router, prefix="/api")

    # 企业微信回调（无需用户鉴权）
    app.include_router(wecom.router, prefix="/api")

    # 企微 OAuth 扫码登录
    app.include_router(wecom_auth.router, prefix="/api")

    # 拼多多开放平台回调（无需用户鉴权）
    app.include_router(pdd.router, prefix="/api")

    # 奇门网关回调（无需用户鉴权，通过签名验证）
    app.include_router(qimen.router, prefix="/api")

    # 企业管理
    app.include_router(org.router, prefix="/api")

    # 模型 + 订阅
    app.include_router(models.router, prefix="/api")
    app.include_router(subscription.router, prefix="/api")

    # 定时任务
    app.include_router(scheduled_tasks.router, prefix="/api")

    # 组织成员任职管理（权限模型 V1）
    app.include_router(org_members_assignments.router, prefix="/api")

    # 企微聊天目标管理（群聊面板）
    app.include_router(wecom_chat_targets.router, prefix="/api")

    # WebSocket
    app.include_router(ws.router, prefix="/api")

    # 系统错误监控
    app.include_router(error_monitor.router, prefix="/api")

    # 管理员用户管理面板（仅 super_admin）
    app.include_router(admin_users.router, prefix="/api")
    app.include_router(runtime_admin.router, prefix="/api")

    # 快麦 Web 数据接入（智库 + viperp）
    app.include_router(kuaimai_external.router, prefix="/api")


# 创建应用实例
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
    )
