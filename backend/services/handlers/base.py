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
from datetime import datetime

from loguru import logger
from supabase import Client

from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    MessageStatus,
    TextPart,
)
from .mixins import TaskMixin, CreditMixin, MessageMixin


@dataclass
class TaskMetadata:
    """
    任务元数据（与业务参数分离）

    这些字段在数据库中有专门的列，不应混入 request_params
    """
    client_task_id: Optional[str] = None
    placeholder_created_at: Optional[datetime] = None


class BaseHandler(TaskMixin, CreditMixin, MessageMixin, ABC):
    """
    统一的消息处理器基类

    职责：
    1. 创建助手消息占位符
    2. 启动生成任务（同步/异步）
    3. 处理完成/错误回调
    4. 推送 WebSocket 消息

    继承自：
    - TaskMixin: 任务状态管理
    - CreditMixin: 积分管理
    - MessageMixin: 消息处理
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
        """从 ContentPart 数组提取第一张图片 URL（单图场景：chat/video）"""
        from schemas.message import ImagePart
        for part in content:
            if isinstance(part, ImagePart):
                return part.url
            if isinstance(part, dict) and part.get("type") == "image":
                return part.get("url")
        return None

    def _extract_image_urls(self, content: List[ContentPart]) -> List[str]:
        """从 ContentPart 数组提取所有图片 URL（多图场景：image editing）"""
        from schemas.message import ImagePart
        urls: List[str] = []
        for part in content:
            if isinstance(part, ImagePart) and part.url:
                urls.append(part.url)
            elif isinstance(part, dict) and part.get("type") == "image":
                url = part.get("url")
                if url:
                    urls.append(url)
        return urls

    # ========================================
    # 任务管理方法 → TaskMixin
    # 积分相关方法 → CreditMixin
    # 消息处理方法 → MessageMixin
    # ========================================

    # ========================================
    # 抽象方法（子类必须实现）
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
