"""
Webhook 回调路由

接收各 AI Provider 的任务完成回调，无需用户鉴权。
根据 provider 路径参数分发到对应的回调解析器。
"""

from typing import Dict, Any, Type

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger
from api.deps import Database
from services.adapters.base import (
    ModelProvider,
)
from services.adapters.kie.image_adapter import KieImageAdapter
from services.adapters.kie.video_adapter import KieVideoAdapter
from services.task_completion_service import TaskCompletionService

router = APIRouter(prefix="/webhook", tags=["Webhook 回调"])


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
    """
    统一 webhook 入口，根据 provider 分发到对应解析器

    流程：
    1. 验证 provider 有效性
    2. 解析 payload，提取 task_id
    3. 查询任务记录获取 task_type（image/video）
    4. 调用对应适配器的 parse_callback()
    5. 传递统一结果给 TaskCompletionService
    """
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
            f"Webhook: missing task_id | provider={provider} | error={e}"
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

    # 6. 调用统一处理
    try:
        await service.process_result(external_task_id, result)
    except Exception as e:
        logger.error(
            f"Webhook: process_result failed | provider={provider} | "
            f"task_id={external_task_id} | error={e}"
        )
        # 返回 500 让 Provider 重试
        return JSONResponse(
            status_code=500,
            content={"error": "Internal processing error"},
        )

    logger.info(
        f"Webhook processed | provider={provider} | "
        f"task_id={external_task_id} | type={task_type} | "
        f"status={result.status.value}"
    )

    return JSONResponse(
        status_code=200,
        content={"status": "ok"},
    )
