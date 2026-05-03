"""
统一多模态消息模型

遵循 OpenAI GPT-4o 风格的 content: ContentPart[] 数组格式。
支持文本、图片、视频、音频、文件等多种内容类型。
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
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
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class MessageOperation(str, Enum):
    """消息操作类型"""
    SEND = "send"
    RETRY = "retry"
    REGENERATE = "regenerate"
    REGENERATE_SINGLE = "regenerate_single"


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
    """图片内容（url 可为 None 表示占位符/生成中）"""
    type: Literal["image"] = "image"
    url: Optional[str] = None
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
    workspace_path: Optional[str] = None  # 工作区相对路径（有值时 AI 用 file_read 读取）


class ThinkingPart(BaseModel):
    """思考过程内容块（持久化到 content，不再依赖 generation_params）

    对标 Vercel AI SDK 的 reasoning part。
    流式阶段通过 thinking_chunk WS 实时推送，
    完成时作为 content 首元素持久化到 DB。
    """
    type: Literal["thinking"] = "thinking"
    text: str
    duration_ms: Optional[int] = None


class ToolStepPart(BaseModel):
    """工具调用步骤块（折叠式卡片，持久化到 content）

    对标 Vercel AI SDK 的 tool-{toolName} part。
    状态机：running → completed / error。
    通过 content_block_add WS 推送，前端渲染为可折叠步骤卡片。
    """
    type: Literal["tool_step"] = "tool_step"
    tool_name: str
    tool_call_id: str
    status: str = "running"  # "running" | "completed" | "error"
    summary: Optional[str] = None
    code: Optional[str] = None       # code_execute 专用：执行的代码
    output: Optional[str] = None     # code_execute 专用：执行输出
    elapsed_ms: Optional[int] = None


class ToolResultPart(BaseModel):
    """工具结果内容块（独立渲染，不被主 Agent 文本覆盖）

    用于子 Agent（如 erp_agent）返回的结论，作为独立 content block
    直接展示给用户，主 Agent 后续文本追加在此块下方。
    """
    type: Literal["tool_result"] = "tool_result"
    tool_name: str
    text: str
    files: List[Dict[str, Any]] = Field(default_factory=list)


class FormPart(BaseModel):
    """表单内容块（聊天内嵌表单，如定时任务创建/修改）

    前端 FormBlock 组件渲染，用户确认后通过 WS form_submit 提交。
    """
    type: Literal["form"] = "form"
    form_type: str
    form_id: str
    title: str = ""
    description: str = ""
    fields: List[Dict[str, Any]] = Field(default_factory=list)
    submit_text: str = "确认"
    cancel_text: str = "取消"


class ChartPart(BaseModel):
    """交互式图表内容块（ECharts 配置 JSON）

    沙盒 code_execute 生成 .echart.json → 后端读取内容嵌入 block →
    前端 ChartBlock 用 ECharts 渲染交互式图表。
    """
    type: Literal["chart"] = "chart"
    option: Dict[str, Any]           # ECharts option 配置
    title: str = ""                  # 图表标题（用于无障碍和导出）
    chart_type: str = ""             # 类型标识（line/bar/pie，日志用）


ContentPart = Annotated[
    Union[TextPart, ImagePart, VideoPart, AudioPart, FilePart,
          ThinkingPart, ToolStepPart, ToolResultPart, FormPart, ChartPart],
    Field(discriminator="type"),
]


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
    """统一生成参数（用于存储和重新生成）

    DB 中以扁平 JSON 存储（如 {"type":"image", "model":"...", "aspect_ratio":"16:9"}），
    允许额外字段透传给前端用于占位符渲染。
    """
    model_config = {"extra": "allow"}

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

    @field_validator('content', mode='before')
    @classmethod
    def parse_content(cls, v: Any) -> Any:
        """Supabase JSONB 可能返回字符串，自动转 list"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return []
        return v

    @field_validator('generation_params', mode='before')
    @classmethod
    def parse_generation_params(cls, v: Any) -> Any:
        """Supabase JSONB 可能返回字符串，自动转 dict"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return None
        return v

    def get_text_content(self) -> str:
        """获取文本内容（便捷方法）"""
        for part in self.content:
            if isinstance(part, TextPart):
                return part.text
        return ""

    def get_image_urls(self) -> List[str]:
        """获取所有图片 URL（排除占位符）"""
        return [p.url for p in self.content if isinstance(p, ImagePart) and p.url]

    def get_video_urls(self) -> List[str]:
        """获取所有视频 URL"""
        return [p.url for p in self.content if isinstance(p, VideoPart)]


# ============================================================
# 请求模型
# ============================================================


# ❌ MessageCreate 已删除 - 请使用 GenerateRequest

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
    client_task_id: Optional[str] = Field(None, max_length=100)  # 🔥 前端生成的 task_id（用于提前订阅）
    created_at: Optional[datetime] = None
    assistant_message_id: Optional[str] = Field(None, max_length=100)
    placeholder_created_at: Optional[datetime] = None  # 占位符创建时间（确保前后端时间戳一致）

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

    # 后端路由确认的真实生成类型
    generation_type: str = "chat"


# ============================================================
# 响应模型（向后兼容 + 简化版本）
# ============================================================


class MessageResponse(BaseModel):
    """
    消息响应（对外 API）

    使用统一的新格式：content 数组
    """
    id: str
    conversation_id: str
    role: MessageRole

    # 统一内容数组（新格式）
    content: Union[str, List[Dict[str, Any]]]

    # 状态
    status: MessageStatus = MessageStatus.COMPLETED
    is_error: bool = False

    # 生成相关
    task_id: Optional[str] = None
    generation_params: Optional[Dict[str, Any]] = None

    @field_validator('generation_params', mode='before')
    @classmethod
    def parse_generation_params(cls, v: Any) -> Any:
        """Supabase JSONB 可能返回字符串，自动转 dict"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return None
        return v

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

        # 构建 content
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


class MessageSearchResult(BaseModel):
    """消息搜索结果

    专门用于 GET /messages/search 端点，比 MessageListResult 多一个 query
    字段供前端做关键词高亮，少一个 has_more 字段（搜索一次性返回硬上限内的全部）。
    """
    messages: List[MessageResponse]
    total: int
    query: str


class DeleteMessageResponse(BaseModel):
    """删除消息响应"""
    id: str
    conversation_id: str


# ============================================================
# 工具函数
# ============================================================


# ❌ content_parts_from_legacy 已删除 - 请直接使用新格式 ContentPart 数组

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
        if any(kw in text for kw in ['修改', '修正', '编辑', '裁剪', '调整', '改大小', '改尺寸', 'edit', '重绘']):
            return GenerationType.IMAGE

    return GenerationType.CHAT
