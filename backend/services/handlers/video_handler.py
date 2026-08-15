"""
视频生成处理器

处理视频生成任务（异步模式）。
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List

from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    MessageError,
    MessageRole,
    MessageStatus,
    VideoPart,
    serialize_content_part,
)
from services.handlers.base import BaseHandler, TaskMetadata
from services.worker_media_tasks import WorkerMediaTasks


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
        self.worker_task_context: Dict[str, Any] | None = None

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
                content_dicts.append(serialize_content_part(part))
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
        if self.worker_task_context is not None:
            return await self._on_worker_complete(
                self.worker_task_context, result,
            )
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
        if self.worker_task_context is not None:
            return await self._on_worker_error(
                self.worker_task_context, error_code, error_message,
            )
        return await self._handle_error_common(task_id, error_code, error_message)

    async def _on_worker_complete(
        self,
        task: Dict[str, Any],
        result: List[ContentPart],
    ) -> Message:
        """提交 Worker 视频成功终态，并执行数据库事务之外的副作用。"""
        content = self._convert_content_parts_to_dicts(result)
        snapshot = WorkerMediaTasks(self.db).commit_video_terminal(
            task["external_task_id"],
            task["version"],
            "completed",
            content,
        )
        if not snapshot or not isinstance(snapshot.get("message"), dict):
            raise RuntimeError("VIDEO_WORKER_TERMINAL_COMMIT_FAILED")

        message_data = snapshot["message"]
        message = self._message_from_worker_snapshot(message_data)
        client_task_id = task.get("client_task_id") or task["external_task_id"]

        from schemas.websocket import build_message_done
        done = build_message_done(
            task_id=client_task_id,
            conversation_id=task["conversation_id"],
            message=message_data,
            credits_consumed=task.get("credits_locked", 0),
        )
        await self._push_ws_message(
            client_task_id, task["user_id"], task.get("org_id"), done,
        )
        asyncio.create_task(
            self._maybe_fanout_to_wecom(task["conversation_id"], content, task)
        )
        self._schedule_worker_metric(task, "success")
        await self._release_worker_slot(task)
        return message

    async def _on_worker_error(
        self,
        task: Dict[str, Any],
        error_code: str,
        error_message: str,
    ) -> Message:
        """提交 Worker 视频失败终态，并执行通知与指标副作用。"""
        content = [{"type": "text", "text": error_message}]
        snapshot = WorkerMediaTasks(self.db).commit_video_terminal(
            task["external_task_id"],
            task["version"],
            "failed",
            content,
            error_code,
            error_message,
        )
        if not snapshot or not isinstance(snapshot.get("message"), dict):
            raise RuntimeError("VIDEO_WORKER_TERMINAL_FAIL_FAILED")

        message = self._message_from_worker_snapshot(
            snapshot["message"],
            MessageError(code=error_code, message=error_message),
        )
        client_task_id = task.get("client_task_id") or task["external_task_id"]
        from schemas.websocket import build_message_error
        error_event = build_message_error(
            task_id=client_task_id,
            conversation_id=task["conversation_id"],
            message_id=str(task["placeholder_message_id"]),
            error_code=error_code,
            error_message=error_message,
        )
        await self._push_ws_message(
            client_task_id, task["user_id"], task.get("org_id"), error_event,
        )
        self._schedule_worker_metric(task, "failed", error_code)
        asyncio.create_task(self._extract_failure_knowledge(
            task_type="video",
            model_id=task.get("model_id", "unknown"),
            error_message=error_message,
        ))
        await self._release_worker_slot(task)
        return message

    def _schedule_worker_metric(
        self,
        task: Dict[str, Any],
        status: str,
        error_code: str | None = None,
    ) -> None:
        request_params = task.get("request_params") or {}
        if isinstance(request_params, str):
            import json
            request_params = json.loads(request_params)
        asyncio.create_task(self._record_knowledge_metric(
            task_id=task.get("id"),
            task_type="video",
            model_id=task.get("model_id", "unknown"),
            status=status,
            error_code=error_code,
            user_id=task.get("user_id"),
            org_id=task.get("org_id"),
            cost_time_ms=self._calc_task_elapsed_ms(task),
            params=request_params,
            retried=bool(request_params.get("_retried")),
            retry_from_model=request_params.get("_retry_from_model"),
        ))

    @staticmethod
    def _message_from_worker_snapshot(
        data: Dict[str, Any],
        error: MessageError | None = None,
    ) -> Message:
        created_at = data.get("created_at")
        return Message(
            id=str(data["id"]),
            conversation_id=str(data["conversation_id"]),
            role=MessageRole(data.get("role", "assistant")),
            content=data.get("content") or [],
            status=MessageStatus(data["status"]),
            error=error,
            created_at=(
                datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if isinstance(created_at, str)
                else created_at
            ),
        )

    @staticmethod
    async def _release_worker_slot(task: Dict[str, Any]) -> None:
        from services.task_limit_service import release_task_slot
        await release_task_slot(task)
