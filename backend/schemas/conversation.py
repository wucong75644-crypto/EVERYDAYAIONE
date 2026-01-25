"""
对话相关的请求/响应模型
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    """创建对话请求"""
    title: Optional[str] = Field(default="新对话", max_length=200)
    model_id: Optional[str] = None


class ConversationUpdate(BaseModel):
    """更新对话请求"""
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    model_id: Optional[str] = None


class ConversationResponse(BaseModel):
    """对话响应"""
    id: str
    title: str
    model_id: Optional[str] = None
    message_count: int = 0
    credits_consumed: int = 0
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    """对话列表响应"""
    id: str
    title: str
    last_message: Optional[str] = None
    model_id: Optional[str] = None
    updated_at: datetime


class ConversationListResult(BaseModel):
    """对话列表结果"""
    conversations: list[ConversationListResponse]
    total: int
