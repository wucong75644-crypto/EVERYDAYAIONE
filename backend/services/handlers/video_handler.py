"""
视频生成处理器

处理视频生成任务（异步模式）。
"""

from typing import Any, Dict, List

from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    VideoPart,
)
from services.handlers.base import BaseHandler, TaskMetadata


class VideoHandler(BaseHandler):
    """
    视频生成处理器

    特点：
    - 异步任务模式
    - 支持文生视频和图生视频
    - 通过 WebSocket 推送完成状态
    """

    def __init__(self, db):
        super().__init__(db)

    @property
    def handler_type(self) -> GenerationType:
        return GenerationType.VIDEO

    async def start(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
        metadata: TaskMetadata,
    ) -> str:
        """
        启动视频生成任务

        只接受已由请求入口原子创建的本地任务，再锁积分并调用供应商。
        """
        from services.handlers.video_prepared_submission import (
            resolve_video_submission_settings,
            submit_prepared_video_task,
        )

        settings = resolve_video_submission_settings(self, content, params)
        prepared_task_id = getattr(metadata, "prepared_task_id", None)
        if not prepared_task_id:
            raise RuntimeError("VIDEO_PREPARED_TASK_MISSING")
        self._check_balance(user_id, settings.credits)
        return await submit_prepared_video_task(
            handler=self, local_task_id=prepared_task_id, user_id=user_id,
            params=params, settings=settings, client_task_id=metadata.client_task_id,
        )

    # ========================================
    # 基类抽象方法实现
    # ========================================

    def _convert_content_parts_to_dicts(self, result: List[ContentPart]) -> List[Dict[str, Any]]:
        """转换 VideoPart 为字典"""
        content_dicts = []
        for part in result:
            if isinstance(part, VideoPart):
                content_dicts.append({
                    "type": "video",
                    "url": part.url,
                    "duration": part.duration,
                    "thumbnail": part.thumbnail,
                })
            elif isinstance(part, dict):
                content_dicts.append(part)
        return content_dicts

    async def _handle_credits_on_complete(
        self,
        task: Dict[str, Any],
        credits_consumed: int,
    ) -> int:
        """Video 完成时确认积分扣除"""
        transaction_id = task.get("credit_transaction_id")
        if transaction_id:
            self._confirm_deduct(transaction_id)
        # 使用预扣的积分作为实际消耗
        return task.get("credits_locked", credits_consumed)

    async def _handle_credits_on_error(self, task: Dict[str, Any]) -> None:
        """Video 错误时退回积分"""
        transaction_id = task.get("credit_transaction_id")
        if transaction_id:
            self._refund_credits(transaction_id)

    # ========================================
    # 回调方法（调用基类通用流程）
    # ========================================

    async def on_complete(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int = 0,
    ) -> Message:
        """完成回调（调用基类通用流程）

        注意：task_id 是 external_task_id（KIE 返回的），需要查询 client_task_id 用于 WebSocket 推送
        """
        return await self._handle_complete_common(task_id, result, credits_consumed)

    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """错误回调（调用基类通用流程）

        注意：task_id 是 external_task_id（KIE 返回的），需要查询 client_task_id 用于 WebSocket 推送
        """
        return await self._handle_error_common(task_id, error_code, error_message)
