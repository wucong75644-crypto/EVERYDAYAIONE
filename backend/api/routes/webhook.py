"""
Webhook 回调路由

接收各 AI Provider 的任务完成回调，无需用户鉴权。
根据 provider 路径参数分发到对应的回调解析器。
"""

import asyncio
import secrets
from typing import Dict, Any, Set, Type

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger
from api.deps import Database
from core.config import get_settings
from services.adapters.base import (
    ModelProvider,
)
from services.adapters.kie.image_adapter import KieImageAdapter
from services.adapters.kie.video_adapter import KieVideoAdapter
from services.task_completion_service import TaskCompletionService

router = APIRouter(prefix="/webhook", tags=["Webhook 回调"])

# 保留强引用，避免 fire-and-forget 任务在执行中被回收。
_processing_tasks: Set[asyncio.Task] = set()


def _track_processing_task(
    task: asyncio.Task,
    provider: str,
    external_task_id: str,
) -> None:
    """跟踪 Webhook 后台任务并消费异常。"""
    _processing_tasks.add(task)

    def _on_done(done_task: asyncio.Task) -> None:
        _processing_tasks.discard(done_task)
        if done_task.cancelled():
            logger.warning(
                f"Webhook processing cancelled | provider={provider} | "
                f"task_id={external_task_id}"
            )
            return
        error = done_task.exception()
        if error:
            logger.error(
                f"Webhook background processing failed | provider={provider} | "
                f"task_id={external_task_id} | error={error}",
                exc_info=error,
            )
            return
        if done_task.result() is False:
            logger.warning(
                f"Webhook background processing deferred to polling | "
                f"provider={provider} | task_id={external_task_id}"
            )

    task.add_done_callback(_on_done)


def _is_authorized_callback(request: Request) -> bool:
    """Provider 回调无用户会话，使用专用 Token 验证来源。"""
    expected_token = get_settings().callback_token
    supplied_token = request.query_params.get("token")
    return bool(
        expected_token
        and supplied_token
        and secrets.compare_digest(supplied_token, expected_token)
    )


def _start_processing(
    service: TaskCompletionService,
    provider: str,
    external_task_id: str,
    result: Any,
) -> None:
    """立即启动统一处理，不让 Provider 等待后续持久化。"""
    task = asyncio.create_task(
        service.process_result(external_task_id, result),
        name=f"webhook:{provider}:{external_task_id}",
    )
    _track_processing_task(task, provider, external_task_id)


def _describe_payload_shape(value: Any, depth: int = 0) -> Any:
    """生成不含值的 payload 结构，用于诊断 Provider 协议变化。"""
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            str(key): _describe_payload_shape(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return {
            "type": "list",
            "item": _describe_payload_shape(value[0], depth + 1) if value else "empty",
        }
    return type(value).__name__


# Provider → 适配器类映射（回调解析用）
# 新增 Provider 时，只需在此注册对应的适配器类
CALLBACK_PARSERS: Dict[ModelProvider, Dict[str, Type]] = {
    ModelProvider.KIE: {
        "image": KieImageAdapter,
        "video": KieVideoAdapter,
    },
    # 未来扩展：
    # ModelProvider.GOOGLE: {
    #     "image": GoogleImageAdapter,
    #     "video": GoogleVideoAdapter,
    # },
}


@router.post("/{provider}", summary="Provider 任务完成回调")
async def handle_webhook(
    provider: str,
    request: Request,
    db: Database,
) -> JSONResponse:
    """验证 Provider 回调，解析统一结果并立即启动后台处理。"""
    if not _is_authorized_callback(request):
        logger.warning(f"Webhook: unauthorized callback | provider={provider}")
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized callback"},
        )

    # 1. 验证 provider
    try:
        model_provider = ModelProvider(provider)
    except ValueError:
        logger.warning(f"Webhook: unknown provider | provider={provider}")
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown provider: {provider}"},
        )

    parsers = CALLBACK_PARSERS.get(model_provider)
    if not parsers:
        logger.warning(f"Webhook: no parser registered | provider={provider}")
        return JSONResponse(
            status_code=400,
            content={"error": f"No callback parser for: {provider}"},
        )

    # 2. 解析 payload
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception as e:
        logger.warning(f"Webhook: invalid JSON | provider={provider} | error={e}")
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON payload"},
        )

    # 3. 提取 task_id（使用任意一个 parser，extract_task_id 是 Provider 级别通用的）
    first_parser = next(iter(parsers.values()))
    try:
        external_task_id = first_parser.extract_task_id(payload)
    except ValueError as e:
        logger.warning(
            f"Webhook: missing task_id | provider={provider} | error={e} | "
            f"payload_shape={_describe_payload_shape(payload)}"
        )
        return JSONResponse(
            status_code=400,
            content={"error": str(e)},
        )

    logger.info(
        f"Webhook received | provider={provider} | task_id={external_task_id}"
    )

    # 4. 查询任务获取类型
    service = TaskCompletionService(db)
    task = service.get_task(external_task_id)
    if not task:
        logger.warning(
            f"Webhook: task not found | provider={provider} | "
            f"task_id={external_task_id}"
        )
        return JSONResponse(
            status_code=404,
            content={"error": "Task not found"},
        )

    # 幂等：已完成的任务直接返回 200
    if task["status"] in ("completed", "failed"):
        logger.info(
            f"Webhook: task already {task['status']} | "
            f"task_id={external_task_id}"
        )
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "message": "Already processed"},
        )

    task_type = task["type"]  # "image" / "video"

    # 5. 使用对应类型的解析器
    parser_class = parsers.get(task_type)
    if not parser_class:
        logger.error(
            f"Webhook: no parser for task type | provider={provider} | "
            f"type={task_type} | task_id={external_task_id}"
        )
        return JSONResponse(
            status_code=400,
            content={"error": f"No parser for type: {task_type}"},
        )

    try:
        result = parser_class.parse_callback(payload)
    except (ValueError, KeyError) as e:
        logger.error(
            f"Webhook: parse_callback failed | provider={provider} | "
            f"task_id={external_task_id} | error={e}"
        )
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid callback payload: {e}"},
        )

    _start_processing(service, provider, external_task_id, result)

    logger.info(
        f"Webhook accepted | provider={provider} | "
        f"task_id={external_task_id} | type={task_type} | "
        f"status={result.status.value}"
    )

    return JSONResponse(
        status_code=200,
        content={"status": "ok"},
    )
