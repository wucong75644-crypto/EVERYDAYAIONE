"""
MessageMixin - 消息处理

提供消息 upsert 和完成/错误处理的通用流程：
- 消息 upsert 到数据库
- WebSocket 推送
- 完成/错误处理通用流程
"""

import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime

from loguru import logger

from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    MessageRole,
    MessageStatus,
)


async def _release_task_limit(task: Dict[str, Any], conversation_id: str) -> None:
    """释放任务限制槽位（任务完成/失败时调用）"""
    from services.task_limit_service import release_task_slot
    await release_task_slot(task)


class MessageMixin:
    """
    消息处理 Mixin

    提供消息相关的通用处理流程：
    - 助手消息 upsert（统一格式）
    - 完成处理（积分 + 消息 + WebSocket + 任务状态）
    - 错误处理（退回积分 + 错误消息 + WebSocket + 任务状态）
    """

    def _upsert_assistant_message(
        self,
        message_id: str,
        conversation_id: str,
        content_dicts: List[Dict[str, Any]],
        status: MessageStatus,
        credits_cost: int,
        client_task_id: str,
        generation_type: str,
        model_id: str,
        is_error: bool = False,
        error_dict: Optional[Dict[str, str]] = None,
        extra_generation_params: Optional[Dict[str, Any]] = None,
    ) -> tuple[Message, Dict[str, Any]]:
        """
        通用的助手消息 upsert 方法

        Args:
            message_id: 消息 ID
            conversation_id: 对话 ID
            content_dicts: 内容字典列表
            status: 消息状态
            credits_cost: 积分消耗
            client_task_id: 客户端任务 ID
            generation_type: 生成类型（chat/image/video）
            model_id: 模型 ID
            is_error: 是否为错误消息
            error_dict: 错误详情（is_error=True 时提供）
            extra_generation_params: 额外的生成参数（如 aspect_ratio），合并到 generation_params

        Returns:
            (Message 对象, 原始字典数据)
        """
        # 1. 构建消息数据
        gen_params: Dict[str, Any] = {"type": generation_type, "model": model_id}
        if extra_generation_params:
            gen_params.update(extra_generation_params)

        message_data = {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": MessageRole.ASSISTANT.value,
            "content": content_dicts,
            "status": status.value,
            "credits_cost": credits_cost,
            "task_id": client_task_id,
            "generation_params": gen_params,
        }

        if is_error:
            message_data["is_error"] = True

        # 2. Upsert 到数据库
        upsert_result = self.db.table("messages").upsert(
            message_data, on_conflict="id"
        ).execute()

        if not upsert_result or not upsert_result.data:
            logger.error(f"Failed to upsert message | message_id={message_id}")
            raise Exception("创建/更新消息失败")

        msg_data = upsert_result.data[0]

        # 3. 构建 Message 对象（注意：Message 类没有 is_error 字段）
        from schemas.message import MessageError

        message = Message(
            id=msg_data["id"],
            conversation_id=msg_data["conversation_id"],
            role=MessageRole(msg_data["role"]),
            content=content_dicts,
            status=status,
            error=MessageError(**error_dict) if error_dict else None,
            created_at=datetime.fromisoformat(
                msg_data["created_at"].replace("Z", "+00:00")
            ),
        )

        return message, msg_data

    def _get_task_context(self, task_id: str) -> Dict[str, Any]:
        """获取任务基本信息，不存在则抛异常"""
        task = self._get_task(task_id)
        if not task:
            logger.error(f"Task not found | task_id={task_id}")
            raise Exception("任务不存在")
        return task

    def _check_idempotency(
        self, task: Dict[str, Any], task_id: str
    ) -> Optional[Message]:
        """幂等性检查：任务已终态则返回已有消息，否则返回 None"""
        if task.get('status') not in ('completed', 'failed', 'cancelled'):
            return None

        message_id = task["placeholder_message_id"]
        logger.warning(
            f"Task already in terminal state, skipping duplicate processing | "
            f"task_id={task_id} | status={task['status']}"
        )
        try:
            existing_msg = (
                self.db.table("messages").select("*")
                .eq("id", message_id).maybe_single().execute()
            )
        except Exception as e:
            logger.error(
                f"Failed to fetch existing message | task_id={task_id} | error={e}"
            )
            raise Exception(f"无法读取已有消息: {e}")

        if existing_msg and existing_msg.data:
            from schemas.message import MessageError
            error_obj = None
            if existing_msg.data.get("is_error"):
                error_obj = MessageError(code="UNKNOWN", message="任务失败")
            return Message(
                id=existing_msg.data["id"],
                conversation_id=existing_msg.data["conversation_id"],
                role=MessageRole(existing_msg.data["role"]),
                content=existing_msg.data["content"],
                status=MessageStatus(existing_msg.data.get("status", "completed")),
                error=error_obj,
                created_at=datetime.fromisoformat(
                    existing_msg.data["created_at"].replace("Z", "+00:00")
                ),
            )

        # 数据不一致：任务已终态但消息不存在，允许继续重建
        logger.critical(
            f"Data inconsistency: task terminal but message missing | "
            f"task_id={task_id} | message_id={message_id}"
        )
        return None

    @staticmethod
    def _calc_task_elapsed_ms(task: Dict[str, Any]) -> Optional[int]:
        """从 task.created_at 计算任务耗时（毫秒），失败返回 None"""
        task_created = task.get("created_at")
        if not task_created:
            return None
        try:
            if isinstance(task_created, str):
                created_dt = datetime.fromisoformat(
                    task_created.replace("Z", "+00:00")
                )
            else:
                created_dt = task_created
            elapsed = datetime.now(created_dt.tzinfo) - created_dt
            return int(elapsed.total_seconds() * 1000)
        except Exception:
            return None

    def _extract_extra_gen_params(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """从 request_params 提取前端渲染所需参数"""
        request_params = task.get("request_params") or {}
        if isinstance(request_params, str):
            import json
            request_params = json.loads(request_params)
        extra = {}
        if request_params.get("aspect_ratio"):
            extra["aspect_ratio"] = request_params["aspect_ratio"]
        return extra

    async def _push_ws_message(
        self, client_task_id: str, user_id: str, ws_msg: Dict[str, Any]
    ) -> None:
        """推送 WebSocket 消息（Chat 走 task 订阅，Media 走 user 投递）"""
        from services.websocket_manager import ws_manager

        await ws_manager.send_to_task_or_user(client_task_id, user_id, ws_msg)

    async def _handle_complete_common(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int,
        tool_digest: Optional[dict] = None,
    ) -> Message:
        """通用完成处理：积分扣除 + 消息 upsert + WS 推送 + 任务状态更新"""
        task = self._get_task_context(task_id)
        message_id = task["placeholder_message_id"]
        conversation_id = task["conversation_id"]
        model_id = task.get("model_id", "unknown")
        client_task_id = task.get("client_task_id") or task_id

        # 幂等性检查
        existing = self._check_idempotency(task, task_id)
        if existing:
            return existing

        # 处理积分
        actual_credits = await self._handle_credits_on_complete(task, credits_consumed)

        # Upsert 消息
        content_dicts = self._convert_content_parts_to_dicts(result)
        extra_gen_params = self._extract_extra_gen_params(task)
        # thinking 已作为 ThinkingPart 持久化到 content，不再写 generation_params
        # 工具执行摘要持久化（跨轮上下文补全）
        if tool_digest:
            extra_gen_params = extra_gen_params or {}
            extra_gen_params["tool_digest"] = tool_digest

        message, msg_data = self._upsert_assistant_message(
            message_id=message_id,
            conversation_id=conversation_id,
            content_dicts=content_dicts,
            status=MessageStatus.COMPLETED,
            credits_cost=actual_credits,
            client_task_id=client_task_id,
            generation_type=self.handler_type.value,
            model_id=model_id,
            extra_generation_params=extra_gen_params,
        )

        # WebSocket 推送
        from schemas.websocket import build_message_done

        done_msg = build_message_done(
            task_id=client_task_id,
            conversation_id=conversation_id,
            message=msg_data,
            credits_consumed=actual_credits,
        )
        await self._push_ws_message(client_task_id, task["user_id"], done_msg)

        # 更新任务状态 + 对话预览（复用已查询的 task 数据，省去重复 SELECT）
        self._complete_task(task_id, task=task)
        preview_text = content_dicts[0].get("text", "")[:50] if content_dicts else ""
        try:
            self.db.table("conversations").update({
                "last_message_preview": preview_text,
            }).eq("id", conversation_id).execute()
        except Exception as e:
            logger.warning(
                f"Failed to update conversation preview | "
                f"conversation_id={conversation_id} | error={e}"
            )

        logger.info(
            f"{self.handler_type.value.capitalize()} completed | "
            f"task_id={task_id} | message_id={message_id} | credits={actual_credits}"
        )

        # 企微同步：如果对话来源是企微，将 AI 回复推送到企微
        asyncio.create_task(
            self._maybe_fanout_to_wecom(conversation_id, content_dicts, task)
        )

        # 知识库指标（Image/Video）
        handler_type = self.handler_type.value
        if handler_type in ("image", "video"):
            request_params = task.get("request_params") or {}
            if isinstance(request_params, str):
                import json
                request_params = json.loads(request_params)

            asyncio.create_task(
                self._record_knowledge_metric(
                    task_type=handler_type, model_id=model_id,
                    status="success", user_id=task.get("user_id"), org_id=task.get("org_id"),
                    params=request_params,
                    cost_time_ms=self._calc_task_elapsed_ms(task),
                    retried=bool(request_params.get("_retried")),
                    retry_from_model=request_params.get("_retry_from_model"),
                )
            )

        # 释放任务限制槽位
        await _release_task_limit(task, conversation_id)

        # 异步生成建议问题（仅 chat 类型，fire-and-forget）
        if self.handler_type.value == "chat":
            asyncio.create_task(
                self._generate_suggestions(
                    conversation_id=conversation_id,
                    user_id=task["user_id"],
                    user_query=self._extract_user_query(task),
                    ai_reply=self._extract_text_from_content(content_dicts),
                )
            )

        return message

    async def _maybe_fanout_to_wecom(
        self,
        conversation_id: str,
        content_dicts: list,
        task: dict,
    ) -> None:
        """如果对话来源是企微（source=wecom），将 AI 回复推送到企微。

        仅限 Web 端触发的生成（企微端自己的回复已在 wecom_message_service 推过）。
        fire-and-forget，失败不影响主流程。
        """
        try:
            conv = (
                self.db.table("conversations")
                .select("source, org_id")
                .eq("id", conversation_id)
                .maybe_single()
                .execute()
            )
            if not conv or not conv.data:
                return
            if conv.data.get("source") != "wecom":
                return

            # 提取文本内容
            text_parts = []
            for part in content_dicts:
                if part.get("type") == "text" and part.get("text"):
                    text_parts.append(part["text"])
            if not text_parts:
                return

            text = "\n".join(text_parts)
            org_id = conv.data.get("org_id")
            user_id = task.get("user_id")
            if not org_id or not user_id:
                return

            from services.message_gateway import MessageGateway
            gateway = MessageGateway(self.db)
            await gateway.fanout_to_wecom(user_id, org_id, text)
        except Exception as e:
            logger.warning(
                f"_maybe_fanout_to_wecom failed | "
                f"conversation_id={conversation_id} | error={e}"
            )

    @staticmethod
    def _extract_user_query(task: dict) -> str:
        """从 task 的 request_params 中提取用户原始问题文本"""
        rp = task.get("request_params") or {}
        if isinstance(rp, str):
            import json
            rp = json.loads(rp)
        return rp.get("content", "")[:200]

    @staticmethod
    def _extract_text_from_content(content_dicts: list) -> str:
        """从 content_dicts 中提取所有文本内容"""
        parts = []
        for part in content_dicts:
            if part.get("type") == "text" and part.get("text"):
                parts.append(part["text"])
        return "\n".join(parts)

    async def _generate_suggestions(
        self,
        conversation_id: str,
        user_id: str,
        user_query: str,
        ai_reply: str,
    ) -> None:
        """异步生成建议问题并推送前端（fire-and-forget）"""
        try:
            if not user_query or not ai_reply:
                return

            from services.suggestion_generator import generate_suggestions
            suggestions = await generate_suggestions(user_query, ai_reply)
            if not suggestions:
                return

            from schemas.websocket_builders import build_suggestions_ready
            from services.websocket_manager import ws_manager

            ws_msg = build_suggestions_ready(conversation_id, suggestions)
            await ws_manager.send_to_user(user_id, ws_msg)

            logger.debug(
                f"Suggestions sent | conversation_id={conversation_id} | "
                f"count={len(suggestions)}"
            )
        except Exception as e:
            logger.warning(
                f"_generate_suggestions failed | "
                f"conversation_id={conversation_id} | error={e}"
            )

    async def _handle_error_common(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """
        通用错误处理：积分退回 + 错误消息 upsert + WS 推送 + 任务状态更新。

        内部有完整保护：即使 upsert/WS 失败，也确保任务最终进入终态。
        """
        task = self._get_task_context(task_id)
        message_id = task["placeholder_message_id"]
        conversation_id = task["conversation_id"]
        model_id = task.get("model_id", "unknown")
        client_task_id = task.get("client_task_id") or task_id

        # 幂等性检查
        existing = self._check_idempotency(task, task_id)
        if existing:
            return existing

        # 积分退回（失败不阻塞后续流程）
        try:
            await self._handle_credits_on_error(task)
        except Exception as credit_err:
            logger.critical(
                f"Credits refund failed in error handler | "
                f"task_id={task_id} | error={credit_err}"
            )

        # Upsert 错误消息（失败时仍要确保任务进入终态）
        message = None
        extra_gen_params = self._extract_extra_gen_params(task)
        try:
            message, msg_data = self._upsert_assistant_message(
                message_id=message_id,
                conversation_id=conversation_id,
                content_dicts=[{"type": "text", "text": error_message}],
                status=MessageStatus.FAILED,
                credits_cost=0,
                client_task_id=client_task_id,
                generation_type=self.handler_type.value,
                model_id=model_id,
                is_error=True,
                error_dict={"code": error_code, "message": error_message},
                extra_generation_params=extra_gen_params,
            )
        except Exception as upsert_err:
            logger.critical(
                f"Error message upsert failed | task_id={task_id} | "
                f"error={upsert_err}"
            )

        # WebSocket 推送（失败不阻塞）
        try:
            from schemas.websocket import build_message_error

            error_msg = build_message_error(
                task_id=client_task_id,
                conversation_id=conversation_id,
                message_id=message_id,
                error_code=error_code,
                error_message=error_message,
            )
            await self._push_ws_message(client_task_id, task["user_id"], error_msg)
        except Exception as ws_err:
            logger.warning(f"Error WS push failed | task_id={task_id} | error={ws_err}")

        # 确保任务进入终态（最关键的一步）
        try:
            self._fail_task(task_id, error_message, task=task)
        except Exception as fail_err:
            logger.critical(
                f"_fail_task failed, task may be stuck | "
                f"task_id={task_id} | error={fail_err}"
            )

        logger.error(
            f"{self.handler_type.value.capitalize()} failed | "
            f"task_id={task_id} | error_code={error_code} | error={error_message}"
        )

        # 知识库指标（Image/Video）—— fire-and-forget
        handler_type = self.handler_type.value
        if handler_type in ("image", "video"):
            request_params = task.get("request_params") or {}
            if isinstance(request_params, str):
                import json
                request_params = json.loads(request_params)

            asyncio.create_task(
                self._record_knowledge_metric(
                    task_type=handler_type, model_id=model_id,
                    status="failed", error_code=error_code,
                    user_id=task.get("user_id"), org_id=task.get("org_id"),
                    cost_time_ms=self._calc_task_elapsed_ms(task),
                    params=request_params,
                    retried=bool(request_params.get("_retried")),
                    retry_from_model=request_params.get("_retry_from_model"),
                )
            )
            asyncio.create_task(
                self._extract_failure_knowledge(
                    task_type=handler_type, model_id=model_id,
                    error_message=error_message,
                )
            )

        # 释放任务限制槽位
        await _release_task_limit(task, conversation_id)

        return message
