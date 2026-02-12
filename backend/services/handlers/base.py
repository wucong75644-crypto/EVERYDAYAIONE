"""
Handler 基类

统一的消息处理器抽象接口。
所有类型（chat/image/video/audio）的 Handler 都继承此类。

积分处理：
- Chat: 完成后按实际 token 扣除
- Image/Video: 开始前预扣，完成后确认，失败后退回
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from supabase import Client

from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    MessageRole,
    MessageStatus,
    TextPart,
)
from core.exceptions import InsufficientCreditsError


@dataclass
class TaskMetadata:
    """
    任务元数据（与业务参数分离）

    这些字段在数据库中有专门的列，不应混入 request_params
    """
    client_task_id: Optional[str] = None
    placeholder_created_at: Optional[datetime] = None


class BaseHandler(ABC):
    """
    统一的消息处理器基类

    职责：
    1. 创建助手消息占位符
    2. 启动生成任务（同步/异步）
    3. 处理完成/错误回调
    4. 推送 WebSocket 消息
    """

    def __init__(self, db: Client):
        self.db = db

    @property
    @abstractmethod
    def handler_type(self) -> GenerationType:
        """Handler 类型"""
        pass

    @abstractmethod
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
        启动处理任务

        Args:
            message_id: 助手消息 ID（占位符）
            conversation_id: 对话 ID
            user_id: 用户 ID
            content: 用户输入内容
            params: 业务参数（纯净，不包含元数据）
            metadata: 任务元数据（client_task_id、placeholder_created_at）

        Returns:
            task_id: 任务 ID（通常是 metadata.client_task_id 或生成的新 ID）
        """
        pass

    @abstractmethod
    async def on_complete(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int = 0,
    ) -> Message:
        """
        完成回调

        Args:
            task_id: 任务 ID
            result: 生成结果（ContentPart 数组）
            credits_consumed: 消耗积分

        Returns:
            更新后的消息
        """
        pass

    @abstractmethod
    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """
        错误回调

        Args:
            task_id: 任务 ID
            error_code: 错误代码
            error_message: 错误信息

        Returns:
            更新后的消息
        """
        pass

    # ========================================
    # 辅助方法
    # ========================================

    def _serialize_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        序列化业务参数用于 JSON 存储

        功能：
        1. 转换 datetime 为 ISO 字符串
        2. 转换 Pydantic 模型为字典
        3. 过滤 None 值
        4. 保留基础类型（str/int/float/bool/list/dict）

        Args:
            params: 业务参数字典（不包含元数据）

        Returns:
            序列化后的参数字典
        """
        serialized = {}

        for key, value in params.items():
            # 跳过 None 值
            if value is None:
                continue

            # 处理特殊类型
            if isinstance(value, datetime):
                serialized[key] = value.isoformat()
            elif hasattr(value, "model_dump"):  # Pydantic 模型
                serialized[key] = value.model_dump()
            elif isinstance(value, (list, dict, str, int, float, bool)):
                serialized[key] = value
            else:
                # 其他类型尝试转字符串
                logger.warning(
                    f"Unknown param type: {key}={type(value).__name__}, converting to str"
                )
                serialized[key] = str(value)

        return serialized

    def _build_task_data(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        task_type: str,
        status: str,
        model_id: str,
        request_params: Dict[str, Any],
        metadata: TaskMetadata,
        credits_locked: int = 0,
        transaction_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        构建标准 task_data 结构（所有 Handler 共用）

        Args:
            task_id: 外部任务 ID
            message_id: 占位符消息 ID
            conversation_id: 对话 ID
            user_id: 用户 ID
            task_type: 任务类型（chat/image/video）
            status: 初始状态（running/pending）
            model_id: 模型 ID
            request_params: 业务参数（已序列化）
            metadata: 任务元数据
            credits_locked: 锁定积分（仅 image/video）
            transaction_id: 积分事务 ID（仅 image/video）

        Returns:
            标准 task_data 字典
        """
        task_data = {
            "external_task_id": task_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "type": task_type,
            "status": status,
            "model_id": model_id,
            "placeholder_message_id": message_id,
            "request_params": request_params,
            # 元数据字段
            "client_task_id": metadata.client_task_id,
            "placeholder_created_at": (
                metadata.placeholder_created_at.isoformat()
                if metadata.placeholder_created_at
                else None
            ),
        }

        # 可选字段（仅 image/video）
        if credits_locked > 0:
            task_data["credits_locked"] = credits_locked
        if transaction_id:
            task_data["credit_transaction_id"] = transaction_id

        return task_data

    def _build_callback_url(self, provider_value: str) -> Optional[str]:
        """
        构建回调 URL，未配置则返回 None

        URL 格式：{base_url}/api/webhook/{provider}
        不同 Provider 走不同的回调路由。

        Args:
            provider_value: Provider 枚举值（如 "kie"、"google"）

        Returns:
            完整回调 URL，或 None（退回纯轮询模式）
        """
        from core.config import get_settings

        base_url = get_settings().callback_base_url
        if not base_url:
            return None
        # 去掉末尾斜杠
        return f"{base_url.rstrip('/')}/api/webhook/{provider_value}"

    def _extract_text_content(self, content: List[ContentPart]) -> str:
        """从 ContentPart 数组提取文本"""
        for part in content:
            if isinstance(part, TextPart):
                return part.text
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text", "")
        return ""

    def _extract_image_url(self, content: List[ContentPart]) -> Optional[str]:
        """从 ContentPart 数组提取图片 URL"""
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image":
                return part.get("url")
        return None

    async def _get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务信息"""
        result = (
            self.db.table("tasks")
            .select("*")
            .eq("external_task_id", task_id)
            .maybe_single()
            .execute()
        )
        return result.data if result.data else None

    async def _complete_task(self, task_id: str) -> None:
        """
        标记任务完成

        两种调用路径：
        1. Chat 任务直接调用：需要更新 version + started_at + status
        2. Image/Video 通过 process_result 调用：只更新 status（version 已由 process_result 更新）

        判断依据：started_at 是否已设置
        - 已设置 → process_result 路径 → 只更新 status
        - 未设置 → Chat 直接路径 → 更新 version + started_at + status
        """
        # 先获取当前任务
        task_result = self.db.table("tasks").select("version, started_at, status").eq("external_task_id", task_id).execute()
        if not task_result.data:
            logger.error(f"Task not found for completion | task_id={task_id}")
            return

        task = task_result.data[0]

        # 幂等性检查：如果已经是终态，跳过
        if task.get('status') in ['completed', 'failed', 'cancelled']:
            logger.debug(f"Task already in terminal state | task_id={task_id} | status={task['status']}")
            return

        current_version = task.get("version", 1)

        # 路径1：started_at 已设置 → Image/Video 通过 process_result 调用
        # process_result 已经更新了 version，这里只更新 status 和 completed_at
        if task.get("started_at"):
            self.db.table("tasks").update({
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
            }).eq("external_task_id", task_id).execute()

            logger.debug(
                f"Task completed (process_result path) | "
                f"task_id={task_id} | version={current_version} (unchanged)"
            )

        # 路径2：started_at 未设置 → Chat 直接调用
        # 需要更新 version + started_at + status
        else:
            update_data = {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "version": current_version + 1,
                "started_at": datetime.utcnow().isoformat(),
            }

            # 执行更新（带乐观锁检查）
            result = self.db.table("tasks").update(update_data).eq("external_task_id", task_id).eq("version", current_version).execute()

            if not result.data:
                logger.warning(
                    f"Task completion lock failed (concurrent update) | "
                    f"task_id={task_id} | version={current_version}"
                )
            else:
                logger.debug(
                    f"Task completed (chat path) | "
                    f"task_id={task_id} | version={current_version}→{current_version + 1}"
                )

    async def _fail_task(self, task_id: str, error_message: str) -> None:
        """
        标记任务失败

        同 _complete_task 逻辑：根据 started_at 判断调用路径
        """
        # 先获取当前任务
        task_result = self.db.table("tasks").select("version, started_at, status").eq("external_task_id", task_id).execute()
        if not task_result.data:
            logger.error(f"Task not found for failure | task_id={task_id}")
            return

        task = task_result.data[0]

        # 幂等性检查
        if task.get('status') in ['completed', 'failed', 'cancelled']:
            logger.debug(f"Task already in terminal state | task_id={task_id} | status={task['status']}")
            return

        current_version = task.get("version", 1)

        # 路径1：started_at 已设置 → process_result 路径
        if task.get("started_at"):
            self.db.table("tasks").update({
                "status": "failed",
                "error_message": error_message,
                "completed_at": datetime.utcnow().isoformat(),
            }).eq("external_task_id", task_id).execute()

            logger.debug(
                f"Task failed (process_result path) | "
                f"task_id={task_id} | version={current_version} (unchanged)"
            )

        # 路径2：started_at 未设置 → Chat 路径
        else:
            update_data = {
                "status": "failed",
                "error_message": error_message,
                "completed_at": datetime.utcnow().isoformat(),
                "version": current_version + 1,
                "started_at": datetime.utcnow().isoformat(),
            }

            result = self.db.table("tasks").update(update_data).eq("external_task_id", task_id).eq("version", current_version).execute()

            if not result.data:
                logger.warning(
                    f"Task failure lock failed (concurrent update) | "
                    f"task_id={task_id} | version={current_version}"
                )
            else:
                logger.debug(
                    f"Task failed (chat path) | "
                    f"task_id={task_id} | version={current_version}→{current_version + 1}"
                )

    # ========================================
    # 积分相关方法
    # ========================================

    async def _get_user_balance(self, user_id: str) -> int:
        """获取用户积分余额"""
        result = self.db.table("users").select("credits").eq("id", user_id).single().execute()
        if not result.data:
            return 0
        return result.data.get("credits", 0)

    async def _check_balance(self, user_id: str, required: int) -> int:
        """
        检查余额是否足够

        Args:
            user_id: 用户 ID
            required: 需要的积分数

        Returns:
            当前余额

        Raises:
            InsufficientCreditsError: 余额不足
        """
        balance = await self._get_user_balance(user_id)
        if balance < required:
            logger.warning(
                f"Insufficient credits | user_id={user_id} | "
                f"required={required} | current={balance}"
            )
            raise InsufficientCreditsError(required=required, current=balance)
        return balance

    async def _lock_credits(
        self,
        task_id: str,
        user_id: str,
        amount: int,
        reason: str = "",
    ) -> str:
        """
        预扣积分（锁定）

        Args:
            task_id: 任务 ID（幂等键）
            user_id: 用户 ID
            amount: 锁定数量
            reason: 锁定原因

        Returns:
            transaction_id

        Raises:
            InsufficientCreditsError: 余额不足
        """
        transaction_id = str(uuid4())

        # 1. 检查余额
        current_credits = await self._get_user_balance(user_id)
        if current_credits < amount:
            raise InsufficientCreditsError(required=amount, current=current_credits)

        # 2. 原子扣除（使用乐观锁）
        new_balance = current_credits - amount
        update_result = self.db.table("users").update({
            "credits": new_balance,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", user_id).eq("credits", current_credits).execute()

        if not update_result.data:
            # 乐观锁冲突，重试一次
            logger.warning(f"Credit lock optimistic lock conflict | user_id={user_id}")
            current_credits = await self._get_user_balance(user_id)
            if current_credits < amount:
                raise InsufficientCreditsError(required=amount, current=current_credits)

            new_balance = current_credits - amount
            update_result = self.db.table("users").update({
                "credits": new_balance,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", user_id).eq("credits", current_credits).execute()

            if not update_result.data:
                raise InsufficientCreditsError(required=amount, current=current_credits)

        # 3. 记录事务
        self.db.table("credit_transactions").insert({
            "id": transaction_id,
            "task_id": task_id,
            "user_id": user_id,
            "amount": amount,
            "type": "lock",
            "status": "pending",
            "reason": reason
        }).execute()

        logger.info(
            f"Credits locked | transaction_id={transaction_id} | "
            f"task_id={task_id} | user_id={user_id} | amount={amount}"
        )

        return transaction_id

    async def _confirm_deduct(self, transaction_id: str) -> None:
        """
        确认扣除（任务成功时调用）

        Args:
            transaction_id: 事务 ID
        """
        self.db.table("credit_transactions").update({
            "status": "confirmed",
            "confirmed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", transaction_id).execute()

        logger.info(f"Credits confirmed | transaction_id={transaction_id}")

    async def _refund_credits(self, transaction_id: str) -> None:
        """
        退回积分（任务失败时调用）

        Args:
            transaction_id: 事务 ID
        """
        # 1. 获取事务信息
        tx_result = self.db.table("credit_transactions").select("*").eq(
            "id", transaction_id
        ).maybe_single().execute()

        if not tx_result.data:
            logger.warning(f"Refund failed: transaction not found | id={transaction_id}")
            return

        tx = tx_result.data
        if tx["status"] != "pending":
            logger.warning(
                f"Refund failed: status not pending | "
                f"id={transaction_id} | status={tx['status']}"
            )
            return

        # 2. 退回积分（原子增加）
        self.db.rpc(
            'refund_credits',
            {
                'p_user_id': tx["user_id"],
                'p_amount': tx["amount"]
            }
        ).execute()

        # 3. 更新事务状态
        self.db.table("credit_transactions").update({
            "status": "refunded",
            "confirmed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", transaction_id).execute()

        logger.info(
            f"Credits refunded | transaction_id={transaction_id} | "
            f"user_id={tx['user_id']} | amount={tx['amount']}"
        )

    async def _deduct_directly(
        self,
        user_id: str,
        amount: int,
        reason: str,
        change_type: str,
    ) -> int:
        """
        直接扣除积分（Chat 完成后使用）

        Args:
            user_id: 用户 ID
            amount: 扣除数量
            reason: 扣除原因
            change_type: 变更类型

        Returns:
            新余额

        Raises:
            InsufficientCreditsError: 余额不足
        """
        try:
            result = self.db.rpc(
                'deduct_credits_atomic',
                {
                    'p_user_id': user_id,
                    'p_amount': amount,
                    'p_reason': reason,
                    'p_change_type': change_type
                }
            ).execute()

            if not result.data or result.data.get('success') is False:
                current = await self._get_user_balance(user_id)
                raise InsufficientCreditsError(required=amount, current=current)

            new_balance = result.data.get('new_balance', 0)
            logger.info(
                f"Credits deducted | user_id={user_id} | amount={amount} | "
                f"new_balance={new_balance} | reason={reason}"
            )
            return new_balance

        except Exception as e:
            if "InsufficientCreditsError" in str(type(e)):
                raise
            logger.error(f"Credit deduction failed | user_id={user_id} | error={e}")
            # 扣除失败时不阻塞任务完成，只记录日志
            return -1

    # ========================================
    # 通用消息处理方法（减少子类重复代码）
    # ========================================

    @abstractmethod
    def _convert_content_parts_to_dicts(self, result: List[ContentPart]) -> List[Dict[str, Any]]:
        """
        转换 ContentPart 为字典（子类实现）

        Args:
            result: ContentPart 列表

        Returns:
            字典列表
        """
        pass

    @abstractmethod
    async def _handle_credits_on_complete(
        self,
        task: Dict[str, Any],
        credits_consumed: int,
    ) -> int:
        """
        完成时的积分处理（子类实现）

        Args:
            task: 任务数据
            credits_consumed: 消耗的积分

        Returns:
            实际扣除的积分数
        """
        pass

    @abstractmethod
    async def _handle_credits_on_error(self, task: Dict[str, Any]) -> None:
        """
        错误时的积分处理（子类实现）

        Args:
            task: 任务数据
        """
        pass

    async def _upsert_assistant_message(
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

        Returns:
            (Message 对象, 原始字典数据)
        """
        # 1. 构建消息数据
        message_data = {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": MessageRole.ASSISTANT.value,
            "content": content_dicts,
            "status": status.value,
            "credits_cost": credits_cost,
            "task_id": client_task_id,
            "generation_params": {"type": generation_type, "model": model_id},
        }

        if is_error:
            message_data["is_error"] = True
            message_data["error"] = error_dict

        # 2. Upsert 到数据库
        upsert_result = self.db.table("messages").upsert(
            message_data, on_conflict="id"
        ).execute()

        if not upsert_result.data:
            logger.error(f"Failed to upsert message | message_id={message_id}")
            raise Exception("创建/更新消息失败")

        msg_data = upsert_result.data[0]

        # 3. 构建 Message 对象
        message = Message(
            id=msg_data["id"],
            conversation_id=msg_data["conversation_id"],
            role=MessageRole(msg_data["role"]),
            content=content_dicts,
            status=status,
            is_error=is_error,
            error=error_dict,
            created_at=datetime.fromisoformat(
                msg_data["created_at"].replace("Z", "+00:00")
            ),
        )

        return message, msg_data

    async def _handle_complete_common(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int,
    ) -> Message:
        """
        通用的完成处理流程

        Args:
            task_id: 任务 ID
            result: 生成结果
            credits_consumed: 消耗积分

        Returns:
            完成后的消息
        """
        # 1. 获取任务信息
        task = await self._get_task(task_id)
        if not task:
            logger.error(f"Task not found | task_id={task_id}")
            raise Exception("任务不存在")

        message_id = task["placeholder_message_id"]
        conversation_id = task["conversation_id"]
        model_id = task.get("model_id", "unknown")
        client_task_id = task.get("client_task_id") or task_id

        # 2. 处理积分（子类实现）
        actual_credits = await self._handle_credits_on_complete(task, credits_consumed)

        # 3. 转换 ContentPart 为字典（子类实现）
        content_dicts = self._convert_content_parts_to_dicts(result)

        # 4. Upsert 消息到数据库
        message, msg_data = await self._upsert_assistant_message(
            message_id=message_id,
            conversation_id=conversation_id,
            content_dicts=content_dicts,
            status=MessageStatus.COMPLETED,
            credits_cost=actual_credits,
            client_task_id=client_task_id,
            generation_type=self.handler_type.value,
            model_id=model_id,
        )

        # 5. 推送 WebSocket 完成消息
        from schemas.websocket import build_message_done
        from services.websocket_manager import ws_manager

        done_msg = build_message_done(
            task_id=client_task_id,
            conversation_id=conversation_id,
            message=msg_data,
            credits_consumed=actual_credits,
        )

        # Chat 使用 send_to_task_subscribers，Media 使用 send_to_task_or_user
        if self.handler_type == GenerationType.CHAT:
            await ws_manager.send_to_task_subscribers(client_task_id, done_msg)
        else:
            user_id = task["user_id"]
            await ws_manager.send_to_task_or_user(client_task_id, user_id, done_msg)

        # 6. 更新任务状态
        await self._complete_task(task_id)

        # 7. 记录日志
        logger.info(
            f"{self.handler_type.value.capitalize()} completed | "
            f"task_id={task_id} | message_id={message_id} | credits={actual_credits}"
        )

        return message

    async def _handle_error_common(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """
        通用的错误处理流程

        Args:
            task_id: 任务 ID
            error_code: 错误代码
            error_message: 错误消息

        Returns:
            错误消息
        """
        # 1. 获取任务信息
        task = await self._get_task(task_id)
        if not task:
            logger.error(f"Task not found | task_id={task_id}")
            raise Exception("任务不存在")

        message_id = task["placeholder_message_id"]
        conversation_id = task["conversation_id"]
        model_id = task.get("model_id", "unknown")
        client_task_id = task.get("client_task_id") or task_id

        # 2. 处理积分退回（子类实现）
        await self._handle_credits_on_error(task)

        # 3. Upsert 错误消息到数据库
        message, msg_data = await self._upsert_assistant_message(
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
        )

        # 4. 推送 WebSocket 错误消息
        from schemas.websocket import build_message_error
        from services.websocket_manager import ws_manager

        error_msg = build_message_error(
            task_id=client_task_id,
            conversation_id=conversation_id,
            message_id=message_id,
            error_code=error_code,
            error_message=error_message,
        )

        # Chat 使用 send_to_task_subscribers，Media 使用 send_to_task_or_user
        if self.handler_type == GenerationType.CHAT:
            await ws_manager.send_to_task_subscribers(client_task_id, error_msg)
        else:
            user_id = task["user_id"]
            await ws_manager.send_to_task_or_user(client_task_id, user_id, error_msg)

        # 5. 更新任务状态
        await self._fail_task(task_id, error_message)

        # 6. 记录日志
        logger.error(
            f"{self.handler_type.value.capitalize()} failed | "
            f"task_id={task_id} | error_code={error_code} | error={error_message}"
        )

        return message
