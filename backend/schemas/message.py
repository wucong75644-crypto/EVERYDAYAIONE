"""
消息相关的请求/响应模型
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    """消息角色"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageCreate(BaseModel):
    """创建消息请求"""
    content: str = Field(..., min_length=1, max_length=10000)
    role: MessageRole = MessageRole.USER
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    credits_cost: int = 0


class MessageResponse(BaseModel):
    """消息响应"""
    id: str
    conversation_id: str
    role: MessageRole
    content: str
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    credits_cost: int = 0
    is_error: bool = False
    created_at: datetime


class MessageListResult(BaseModel):
    """消息列表结果"""
    messages: list[MessageResponse]
    total: int
    has_more: bool = False


class SendMessageRequest(BaseModel):
    """发送消息请求（包含模型配置）"""
    content: str = Field(..., min_length=1, max_length=10000)
    model_id: Optional[str] = None
    image_url: Optional[str] = None  # 图片 URL（用于 VQA）
    video_url: Optional[str] = None  # 视频 URL（用于视频 QA）
    thinking_effort: Optional[str] = None  # 推理强度（Gemini 3）: minimal/low/medium/high
    thinking_mode: Optional[str] = None  # 推理模式（Gemini 3 Pro）: default/deep_think
    # 高级设置
    image_size: Optional[str] = "1024x1024"
    image_count: Optional[int] = Field(default=1, ge=1, le=4)


class SendMessageResponse(BaseModel):
    """发送消息响应"""
    user_message: MessageResponse
    assistant_message: Optional[MessageResponse] = None
    credits_consumed: int = 0


class DeleteMessageResponse(BaseModel):
    """删除消息响应"""
    id: str
    conversation_id: str
