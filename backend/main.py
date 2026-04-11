"""
FastAPI 应用入口

EVERYDAYAI - AI 图片/视频生成平台后端服务
"""

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
    audio, auth, conversation, file, health, image, memory, message,
    models, org, pdd, qimen, subscription, task, webhook, wecom, wecom_auth, ws,
)
from core.config import get_settings
from core.exceptions import AppException
from core.limiter import limiter
from core.redis import RedisClient
from core.logging_config import setup_logging
from services.background_task_worker import BackgroundTaskWorker
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
    logger.info(f"Starting EVERYDAYAI API | env={settings.app_env}")

    # 初始化 Redis 连接
    try:
        await RedisClient.get_client()
        logger.info("Redis 连接初始化成功")
    except Exception as e:
        logger.warning(f"Redis 连接失败，限流功能降级 | error={e}")

    # 启动 WebSocket Redis Pub/Sub 监听（跨 Worker 消息投递）
    await ws_manager.start_redis_listener()

    # 预热 Mem0 记忆服务连接（避免首次请求慢）
    try:
        from services.memory_config import _get_mem0
        mem0 = await _get_mem0()
        if mem0:
            logger.info("Mem0 connection pre-warmed successfully")
        else:
            logger.info("Mem0 not configured, memory feature disabled")
    except Exception as e:
        logger.warning(f"Mem0 pre-warm failed (non-critical) | error={e}")

    # 预热知识库连接 + 导入种子知识（用锁避免多 worker 重复加载）
    try:
        from services.knowledge_config import _get_pg_pool, is_kb_available
        pool = await _get_pg_pool()
        if pool and is_kb_available():
            lock_token = await RedisClient.acquire_lock("seed_knowledge_load", timeout=60)
            if lock_token:
                try:
                    from services.knowledge_service import load_seed_knowledge
                    imported = await load_seed_knowledge()
                    logger.info(f"Knowledge base ready | seed_imported={imported}")
                finally:
                    await RedisClient.release_lock("seed_knowledge_load", lock_token)
            else:
                logger.info("Knowledge base seed loading skipped (another worker is loading)")
        else:
            logger.info("Knowledge base not configured or disabled")
    except Exception as e:
        logger.warning(f"Knowledge base init failed (non-critical) | error={e}")

    # 启动后台任务工作器
    from core.database import get_db
    import asyncio

    db = get_db()

    # OrgScopedDB schema 反射：扫描含 org_id 的复合唯一索引所属的表，
    # 让 upsert on_conflict 自动追加 ",org_id" 仅对真正有此索引的表生效，
    # 避免对 messages/tasks 等 PK 仅 id 的表生成无效 ON CONFLICT 子句。
    try:
        from core.org_scoped_db import load_composite_org_id_tables
        load_composite_org_id_tables(db)
    except Exception as e:
        logger.error(
            f"OrgScopedDB schema reflection failed (non-critical) | error={e}"
        )

    # 恢复孤儿任务：部署重启后，将中断的流式内容从 tasks.accumulated_content 回写到 messages 表
    # 用 Redis 锁确保多 worker 只执行一次
    _recovery_lock = await RedisClient.acquire_lock("orphan_task_recovery", timeout=30)
    if _recovery_lock:
        try:
            from services.task_recovery import recover_orphan_tasks
            recovered = await recover_orphan_tasks(db)
            if recovered > 0:
                logger.info(f"Orphan task recovery completed | recovered={recovered}")
        except Exception as e:
            logger.error(f"Orphan task recovery failed (non-critical) | error={e}")
        finally:
            await RedisClient.release_lock("orphan_task_recovery", _recovery_lock)

    # 启动时清理遗留的 staging 文件（兜底：防止进程崩溃后的孤儿文件）
    try:
        from pathlib import Path
        import time as _time_mod
        staging_root = Path(settings.file_workspace_root) / "staging"
        if staging_root.exists():
            import shutil
            cutoff = _time_mod.time() - 3600  # 超过1小时的视为孤儿
            cleaned = 0
            for child in staging_root.iterdir():
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    cleaned += 1
            if cleaned:
                logger.info(f"Staging orphan cleanup | removed={cleaned} dirs")
    except Exception as e:
        logger.debug(f"Staging cleanup skipped | error={e}")

    worker = BackgroundTaskWorker(db)
    worker_task = asyncio.create_task(worker.start())
    logger.info("BackgroundTaskWorker started")

    # 启动 ERP 同步工作器（仅在第一个 worker 中启动，避免多 worker 重复抢锁浪费 API 配额）
    import os
    _worker_pid = os.getpid()
    _parent_pid = os.getppid()
    # uvicorn 多 worker 模式：通过 Redis 原子操作竞选，第一个写入的 worker 获胜
    from core.redis import get_redis
    _redis = await get_redis()
    _elected = False
    if _redis:
        _elected = await _redis.set(
            "erp_sync_elected_worker", str(_worker_pid),
            nx=True, ex=300,  # 5分钟过期自动重新竞选
        )
    else:
        _elected = True  # Redis 不可用时默认启动

    if _elected:
        from core.database import get_async_db, close_async_db
        async_db = await get_async_db()
        from services.kuaimai.erp_sync_orchestrator import ErpSyncOrchestrator
        erp_orchestrator = ErpSyncOrchestrator(async_db)
        erp_orchestrator_task = asyncio.create_task(erp_orchestrator.start())
        logger.info(f"ErpSyncOrchestrator started | elected_worker={_worker_pid}")

        # ERP 同步健康检查（5 分钟扫一次 erp_sync_state，error_count>=10 推企微告警）
        # 防止类似 2026-04-10 的 token 雪崩 7 小时无人察觉
        from services.kuaimai.erp_sync_healthcheck import healthcheck_loop
        erp_healthcheck_task = asyncio.create_task(healthcheck_loop(async_db))
        logger.info(f"ErpSyncHealthcheck started | elected_worker={_worker_pid}")
    else:
        erp_orchestrator = None
        erp_orchestrator_task = None
        erp_healthcheck_task = None
        logger.info(f"ErpSyncOrchestrator skipped (another worker elected) | pid={_worker_pid}")

    # 企微智能机器人 WS 长连接已拆为独立进程（wecom_ws_runner.py）
    # 由 systemd everydayai-wecom.service 管理，避免多 worker 竞争

    yield

    # 优雅关闭：通知所有 WebSocket 客户端服务即将重启
    from schemas.websocket import build_server_restarting
    await ws_manager.broadcast_all(build_server_restarting())
    await asyncio.sleep(1)  # 给客户端一点时间接收消息

    # 停止 WebSocket Redis Pub/Sub 监听
    await ws_manager.stop_redis_listener()

    # 停止后台工作器
    await worker.stop()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    # 停止 ERP 同步编排器
    if erp_orchestrator is not None:
        await erp_orchestrator.stop()
        erp_orchestrator_task.cancel()
        try:
            await erp_orchestrator_task
        except asyncio.CancelledError:
            pass

    # 停止 ERP 健康检查后台任务
    if erp_healthcheck_task is not None:
        erp_healthcheck_task.cancel()
        try:
            await erp_healthcheck_task
        except asyncio.CancelledError:
            pass

    # 关闭异步数据库连接池
    try:
        from core.database import close_async_db
        await close_async_db()
    except Exception:
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

    # CORS 配置（从环境变量读取）
    allowed_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]

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

    # 图像上传（生成功能已迁移到 /messages/generate）
    app.include_router(image.router, prefix="/api")

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

    # WebSocket
    app.include_router(ws.router, prefix="/api")


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
