"""
统一多模态消息模型

遵循 OpenAI GPT-4o 风格的 content: ContentPart[] 数组格式。
支持文本、图片、视频、音频、文件等多种内容类型。
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator
import json


# ============================================================
# 枚举类型
# ============================================================


class MessageRole(str, Enum):
    """消息角色"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageStatus(str, Enum):
    """消息状态"""
    PENDING = "pending"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"


class MessageOperation(str, Enum):
    """消息操作类型"""
    SEND = "send"
    RETRY = "retry"
    REGENERATE = "regenerate"


class GenerationType(str, Enum):
    """生成类型"""
    CHAT = "chat"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


# ============================================================
# 内容部件类型（OpenAI 风格）
# ============================================================


class TextPart(BaseModel):
    """文本内容"""
    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    """图片内容"""
    type: Literal["image"] = "image"
    url: str
    width: Optional[int] = None
    height: Optional[int] = None
    alt: Optional[str] = None


class VideoPart(BaseModel):
    """视频内容"""
    type: Literal["video"] = "video"
    url: str
    duration: Optional[float] = None
    thumbnail: Optional[str] = None


class AudioPart(BaseModel):
    """音频内容"""
    type: Literal["audio"] = "audio"
    url: str
    duration: Optional[float] = None
    transcript: Optional[str] = None


class FilePart(BaseModel):
    """文件内容"""
    type: Literal["file"] = "file"
    url: str
    name: str
    mime_type: str
    size: Optional[int] = None


ContentPart = Union[TextPart, ImagePart, VideoPart, AudioPart, FilePart]


# ============================================================
# 生成参数
# ============================================================


class ChatParams(BaseModel):
    """聊天生成参数"""
    model: str = Field(..., max_length=100)
    thinking_effort: Optional[str] = None
    thinking_mode: Optional[str] = None


class ImageParams(BaseModel):
    """图片生成参数"""
    model: str = Field(..., max_length=100)
    aspect_ratio: str = "1:1"
    resolution: Optional[str] = None
    output_format: str = "png"


class VideoParams(BaseModel):
    """视频生成参数"""
    model: str = Field(..., max_length=100)
    aspect_ratio: str = "landscape"
    n_frames: str = "25"
    remove_watermark: bool = True


class GenerationParams(BaseModel):
    """统一生成参数（用于存储和重新生成）"""
    type: Optional[GenerationType] = None
    chat: Optional[ChatParams] = None
    image: Optional[ImageParams] = None
    video: Optional[VideoParams] = None


class MessageError(BaseModel):
    """消息错误信息"""
    code: str
    message: str


# ============================================================
# 统一消息模型
# ============================================================


class Message(BaseModel):
    """
    统一消息模型

    所有类型（text/image/video/audio）使用相同数据结构。
    content 为 ContentPart 数组，支持多模态内容。
    """
    id: str
    conversation_id: str
    role: MessageRole

    # 核心：统一的内容数组
    content: List[ContentPart] = Field(default_factory=list)

    # 状态
    status: MessageStatus = MessageStatus.COMPLETED

    # 生成相关
    task_id: Optional[str] = None
    generation_params: Optional[GenerationParams] = None

    # 计费
    credits_cost: int = 0

    # 错误信息
    error: Optional[MessageError] = None

    # 时间戳
    created_at: datetime
    updated_at: Optional[datetime] = None

    # 客户端请求 ID（用于乐观更新）
    client_request_id: Optional[str] = None

    def get_text_content(self) -> str:
        """获取文本内容（便捷方法）"""
        for part in self.content:
            if isinstance(part, TextPart):
                return part.text
        return ""

    def get_image_urls(self) -> List[str]:
        """获取所有图片 URL"""
        return [p.url for p in self.content if isinstance(p, ImagePart)]

    def get_video_urls(self) -> List[str]:
        """获取所有视频 URL"""
        return [p.url for p in self.content if isinstance(p, VideoPart)]


# ============================================================
# 请求模型
# ============================================================


class MessageCreate(BaseModel):
    """创建消息请求（直接创建，不触发生成）"""
    role: MessageRole = MessageRole.USER
    content: List[ContentPart] = Field(default_factory=list)
    status: MessageStatus = MessageStatus.COMPLETED
    credits_cost: int = 0
    created_at: Optional[datetime] = None
    client_request_id: Optional[str] = Field(None, max_length=100)
    generation_params: Optional[GenerationParams] = None

    @field_validator('content')
    @classmethod
    def validate_content_size(cls, v: List[ContentPart]) -> List[ContentPart]:
        """验证内容大小不超过 100KB"""
        json_str = json.dumps([p.model_dump() for p in v])
        if len(json_str) > 100000:
            raise ValueError('content 大小不能超过 100KB')
        return v


class GenerateRequest(BaseModel):
    """
    统一消息生成请求

    支持三种操作：
    - send: 发送新消息（创建用户消息 + 创建 AI 消息）
    - retry: 重试失败的 AI 消息（不创建用户消息 + 原地更新）
    - regenerate: 重新生成成功的 AI 消息（创建用户消息 + 创建 AI 消息）
    """
    # 操作类型
    operation: MessageOperation = MessageOperation.SEND

    # 用户输入内容（统一格式）
    content: List[ContentPart] = Field(default_factory=list)

    # 生成类型（自动推断或显式指定）
    generation_type: Optional[GenerationType] = None

    # 模型配置
    model: Optional[str] = None

    # 类型特定参数
    params: Optional[Dict[str, Any]] = None

    # 重新生成时的原消息 ID
    original_message_id: Optional[str] = Field(None, max_length=100)

    # 前端预分配 ID（用于乐观更新）
    client_request_id: Optional[str] = Field(None, max_length=100)
    created_at: Optional[datetime] = None
    assistant_message_id: Optional[str] = Field(None, max_length=100)

    def model_post_init(self, __context) -> None:
        """验证操作参数完整性"""
        if self.operation == MessageOperation.SEND:
            if not self.content:
                raise ValueError('send 操作必须提供 content')
        # 注：original_message_id 为可选参数，用于追踪原消息（目前未使用）


class GenerateResponse(BaseModel):
    """统一消息生成响应"""
    # 任务 ID（所有类型都有）
    task_id: str

    # 用户消息（send/regenerate 操作）
    user_message: Optional[Message] = None

    # 助手消息（占位符）
    assistant_message: Message

    # 预估完成时间（毫秒）
    estimated_time_ms: Optional[int] = None

    # 操作类型（回显）
    operation: MessageOperation


# ============================================================
# 响应模型（向后兼容 + 简化版本）
# ============================================================


class MessageResponse(BaseModel):
    """
    消息响应（对外 API）

    同时支持新格式（content 数组）和旧格式（content 字符串 + image_url/video_url）
    """
    id: str
    conversation_id: str
    role: MessageRole

    # 新格式：统一内容数组
    content: Union[str, List[Dict[str, Any]]]

    # 旧格式兼容（从 content 数组提取）
    image_url: Optional[str] = None
    video_url: Optional[str] = None

    # 状态
    status: MessageStatus = MessageStatus.COMPLETED
    is_error: bool = False

    # 生成相关
    task_id: Optional[str] = None
    generation_params: Optional[Dict[str, Any]] = None

    # 计费
    credits_cost: int = 0

    # 时间戳
    created_at: datetime

    # 客户端请求 ID
    client_request_id: Optional[str] = None

    @classmethod
    def from_message(cls, msg: Message) -> "MessageResponse":
        """从 Message 模型转换"""
        # 提取文本内容
        text_content = msg.get_text_content()

        # 提取 URL（兼容旧格式）
        image_urls = msg.get_image_urls()
        video_urls = msg.get_video_urls()

        # 构建 content（支持两种格式）
        # 如果只有文本，返回字符串；否则返回数组
        if len(msg.content) == 1 and isinstance(msg.content[0], TextPart):
            content_value: Union[str, List[Dict[str, Any]]] = text_content
        else:
            content_value = [p.model_dump() for p in msg.content]

        return cls(
            id=msg.id,
            conversation_id=msg.conversation_id,
            role=msg.role,
            content=content_value,
            image_url=image_urls[0] if image_urls else None,
            video_url=video_urls[0] if video_urls else None,
            status=msg.status,
            is_error=msg.error is not None,
            task_id=msg.task_id,
            generation_params=msg.generation_params.model_dump() if msg.generation_params else None,
            credits_cost=msg.credits_cost,
            created_at=msg.created_at,
            client_request_id=msg.client_request_id,
        )


class MessageListResult(BaseModel):
    """消息列表结果"""
    messages: List[MessageResponse]
    total: int
    has_more: bool = False


class DeleteMessageResponse(BaseModel):
    """删除消息响应"""
    id: str
    conversation_id: str


# ============================================================
# 工具函数
# ============================================================


def content_parts_from_legacy(
    text: Optional[str],
    image_url: Optional[str] = None,
    video_url: Optional[str] = None,
) -> List[ContentPart]:
    """
    从旧格式转换为 ContentPart 数组

    用于兼容旧 API 调用。
    """
    parts: List[ContentPart] = []

    if text:
        parts.append(TextPart(text=text))

    if image_url:
        parts.append(ImagePart(url=image_url))

    if video_url:
        parts.append(VideoPart(url=video_url))

    return parts


def infer_generation_type(content: List[ContentPart]) -> GenerationType:
    """
    根据内容推断生成类型

    规则：
    1. 包含 /image 或 "生成图片" 等关键词 -> image
    2. 包含 /video 或 "生成视频" 等关键词 -> video
    3. 包含图片 + "变成视频" -> video
    4. 包含图片 + "编辑/修改" -> image
    5. 其他 -> chat
    """
    text_parts = [p for p in content if isinstance(p, TextPart)]
    image_parts = [p for p in content if isinstance(p, ImagePart)]

    if not text_parts:
        return GenerationType.CHAT

    text = text_parts[0].text.lower()

    # 显式指定
    if text.startswith('/image') or any(kw in text for kw in ['生成图片', '画一', 'generate image']):
        return GenerationType.IMAGE
    if text.startswith('/video') or any(kw in text for kw in ['生成视频', '做个视频', 'generate video']):
        return GenerationType.VIDEO

    # 图片 + 关键词
    if image_parts:
        if any(kw in text for kw in ['变成视频', 'to video', '做成视频']):
            return GenerationType.VIDEO
        if any(kw in text for kw in ['修改', '编辑', 'edit', '重绘']):
            return GenerationType.IMAGE

    return GenerationType.CHAT
