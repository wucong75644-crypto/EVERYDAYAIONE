"""
FastAPI 应用入口

EVERYDAYAI - AI 图片/视频生成平台后端服务
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.routes import audio, auth, conversation, health, image, message, task, video
from core.config import get_settings
from core.exceptions import AppException
from core.limiter import limiter
from core.redis import RedisClient
from services.background_task_worker import BackgroundTaskWorker


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """添加安全响应头中间件"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # 防止点击劫持
        response.headers["X-Frame-Options"] = "DENY"

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
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "  # Vite HMR 需要
                "style-src 'self' 'unsafe-inline'; "  # Vite 样式注入需要
                "img-src 'self' data: https://*.aliyuncs.com https://qcaatwmlzqqnzfjdzlzm.supabase.co; "
                "font-src 'self' data:; "
                "connect-src 'self' https://qcaatwmlzqqnzfjdzlzm.supabase.co https://api.kie.ai ws://localhost:*; "  # 添加 WebSocket 支持
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
                "script-src 'self'; "  # 移除 unsafe-inline 和 unsafe-eval
                "style-src 'self'; "  # 移除 unsafe-inline
                "img-src 'self' data: https://*.aliyuncs.com https://qcaatwmlzqqnzfjdzlzm.supabase.co; "
                "font-src 'self' data:; "
                "connect-src 'self' https://qcaatwmlzqqnzfjdzlzm.supabase.co https://api.kie.ai; "
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
    logger.info(f"Starting EVERYDAYAI API | env={settings.app_env}")

    # 初始化 Redis 连接
    try:
        await RedisClient.get_client()
        logger.info("Redis 连接初始化成功")
    except Exception as e:
        logger.warning(f"Redis 连接失败，限流功能降级 | error={e}")

    # 启动后台任务工作器
    from core.database import get_supabase_client
    import asyncio

    db = get_supabase_client()
    worker = BackgroundTaskWorker(db)
    worker_task = asyncio.create_task(worker.start())
    logger.info("BackgroundTaskWorker started")

    yield

    # 停止后台工作器
    await worker.stop()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    # 关闭 Redis 连接
    await RedisClient.close()
    logger.info("Shutting down EVERYDAYAI API")


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

    # CORS 配置
    allowed_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",  # Vite 默认端口
    ] if settings.app_debug else [
        "https://everydayai.com",
        "https://www.everydayai.com",
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
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

    # 图像生成
    app.include_router(image.router, prefix="/api")

    # 视频生成
    app.include_router(video.router, prefix="/api")

    # 音频上传
    app.include_router(audio.router, prefix="/api")

    # 任务管理
    app.include_router(task.router, prefix="/api")


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
