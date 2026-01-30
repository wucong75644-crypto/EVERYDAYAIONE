"""
消息相关的请求/响应模型
"""

from datetime import datetime
from enum import Enum
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator
import json


class MessageRole(str, Enum):
    """消息角色"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


# ============================================================
# 生成参数验证模型
# ============================================================


class ImageGenerationParams(BaseModel):
    """图片生成参数"""
    aspectRatio: str = Field(..., description="宽高比")
    resolution: Optional[str] = Field(None, description="分辨率")
    outputFormat: str = Field(..., description="输出格式")
    model: str = Field(..., max_length=100, description="模型ID")


class VideoGenerationParams(BaseModel):
    """视频生成参数"""
    frames: str = Field(..., description="帧数/时长")
    aspectRatio: str = Field(..., description="宽高比")
    removeWatermark: bool = Field(..., description="是否去水印")
    model: str = Field(..., max_length=100, description="模型ID")


class GenerationParams(BaseModel):
    """生成参数（用于重新生成时继承）"""
    image: Optional[ImageGenerationParams] = None
    video: Optional[VideoGenerationParams] = None

    @field_validator('image', 'video', mode='after')
    @classmethod
    def validate_not_both(cls, v, info):
        """验证不能同时有 image 和 video"""
        values = info.data
        if values.get('image') and values.get('video'):
            raise ValueError('不能同时包含 image 和 video 参数')
        return v


class MessageCreate(BaseModel):
    """创建消息请求"""
    content: str = Field(..., min_length=1, max_length=10000)
    role: MessageRole = MessageRole.USER
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    credits_cost: int = 0
    is_error: bool = False  # 是否为错误消息
    created_at: Optional[datetime] = None  # 可选时间戳（用于保持消息顺序）
    generation_params: Optional[GenerationParams] = None  # 生成参数（图片/视频生成时保存）
    client_request_id: Optional[str] = Field(None, max_length=100, description="客户端请求ID，用于乐观更新")

    @field_validator('generation_params')
    @classmethod
    def validate_params_size(cls, v: Optional[GenerationParams]) -> Optional[GenerationParams]:
        """验证生成参数大小不超过 10KB"""
        if v:
            json_str = json.dumps(v.model_dump())
            if len(json_str) > 10000:  # 10KB 限制
                raise ValueError('generation_params 大小不能超过 10KB')
        return v


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
    generation_params: Optional[GenerationParams] = None  # 生成参数（用于重新生成时继承）
    client_request_id: Optional[str] = None  # 客户端请求ID（原样返回，用于前端替换临时消息）
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
    client_request_id: Optional[str] = Field(None, max_length=100, description="客户端请求ID")
    # 高级设置
    image_size: Optional[str] = "1024x1024"
    image_count: Optional[int] = Field(default=1, ge=1, le=4)

    @field_validator('image_url', 'video_url', mode='before')
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        """验证 URL 格式"""
        if v is None or v == '':
            return None
        if not v.startswith(('http://', 'https://')):
            raise ValueError('URL 必须以 http:// 或 https:// 开头')
        return v


class SendMessageResponse(BaseModel):
    """发送消息响应"""
    user_message: MessageResponse
    assistant_message: Optional[MessageResponse] = None
    credits_consumed: int = 0


class DeleteMessageResponse(BaseModel):
    """删除消息响应"""
    id: str
    conversation_id: str
