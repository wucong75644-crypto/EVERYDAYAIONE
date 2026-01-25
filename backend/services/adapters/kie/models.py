"""
KIE API 数据模型定义

定义所有 KIE API 的请求和响应数据结构
"""

from enum import Enum
from typing import Optional, List, Any, Dict, Union
from pydantic import BaseModel, Field
from decimal import Decimal


# ============================================================
# 通用枚举定义
# ============================================================

class KieModelType(str, Enum):
    """KIE 模型类型"""
    CHAT = "chat"
    IMAGE = "image"
    VIDEO = "video"


class TaskState(str, Enum):
    """异步任务状态"""
    WAITING = "waiting"
    SUCCESS = "success"
    FAIL = "fail"


class AspectRatio(str, Enum):
    """图像/视频宽高比"""
    # 图像模型使用
    RATIO_1_1 = "1:1"
    RATIO_2_3 = "2:3"
    RATIO_3_2 = "3:2"
    RATIO_3_4 = "3:4"
    RATIO_4_3 = "4:3"
    RATIO_4_5 = "4:5"
    RATIO_5_4 = "5:4"
    RATIO_9_16 = "9:16"
    RATIO_16_9 = "16:9"
    RATIO_21_9 = "21:9"
    AUTO = "auto"
    # 视频模型使用
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class ImageResolution(str, Enum):
    """图像分辨率 (仅 nano-banana-pro)"""
    RES_1K = "1K"
    RES_2K = "2K"
    RES_4K = "4K"


class ImageOutputFormat(str, Enum):
    """图像输出格式"""
    PNG = "png"
    JPEG = "jpeg"
    JPG = "jpg"


class VideoFrames(str, Enum):
    """视频时长"""
    FRAMES_10 = "10"
    FRAMES_15 = "15"
    FRAMES_25 = "25"  # 仅 sora-2-pro-storyboard


class ReasoningEffort(str, Enum):
    """推理力度 (Gemini 3 系列)"""
    MINIMAL = "minimal"  # 极快响应
    LOW = "low"  # 标准速度（默认）
    MEDIUM = "medium"  # 深度思考
    HIGH = "high"  # 最强推理


class ThinkingMode(str, Enum):
    """推理模式 (Gemini 3 Pro 专属)"""
    DEFAULT = "default"  # 标准模式
    DEEP_THINK = "deep_think"  # Deep Think 超脑模式（PhD 级推理）


class MessageRole(str, Enum):
    """消息角色"""
    DEVELOPER = "developer"
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


# ============================================================
# Chat 模型 - 请求/响应模型
# ============================================================

class ChatContentPart(BaseModel):
    """消息内容部分 (支持多模态)"""
    type: str  # "text" | "image_url"
    text: Optional[str] = None
    image_url: Optional[Dict[str, str]] = None  # {"url": "https://..."}


class ChatMessage(BaseModel):
    """聊天消息"""
    role: MessageRole
    content: Union[str, List[ChatContentPart]]


class FunctionDefinition(BaseModel):
    """函数定义"""
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class ToolDefinition(BaseModel):
    """工具定义"""
    type: str = "function"
    function: FunctionDefinition


class JsonSchema(BaseModel):
    """JSON Schema 定义"""
    name: str
    strict: bool = True
    schema_: Dict[str, Any] = Field(alias="schema")

    class Config:
        populate_by_name = True


class ResponseFormat(BaseModel):
    """响应格式 (结构化输出)"""
    type: str = "json_schema"
    json_schema: JsonSchema


class ChatCompletionRequest(BaseModel):
    """Chat Completions 请求"""
    messages: List[ChatMessage]
    stream: bool = True
    include_thoughts: bool = True
    reasoning_effort: ReasoningEffort = ReasoningEffort.HIGH
    thinking_mode: Optional[ThinkingMode] = None  # Deep Think 模式（Pro 专属）
    tools: Optional[List[ToolDefinition]] = None
    response_format: Optional[ResponseFormat] = None


class ChatCompletionChunkDelta(BaseModel):
    """流式响应增量"""
    role: Optional[str] = None
    content: Optional[str] = None
    reasoning_content: Optional[str] = None


class ChatCompletionChunkChoice(BaseModel):
    """流式响应选择"""
    index: int
    delta: ChatCompletionChunkDelta
    finish_reason: Optional[str] = None


class TokenUsageDetails(BaseModel):
    """Token 使用详情"""
    reasoning_tokens: int = 0
    text_tokens: int = 0
    audio_tokens: int = 0


class TokenUsage(BaseModel):
    """Token 使用统计"""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    completion_tokens_details: Optional[TokenUsageDetails] = None


class ChatCompletionChunk(BaseModel):
    """流式响应块"""
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[ChatCompletionChunkChoice]
    credits_consumed: Optional[float] = None
    usage: Optional[TokenUsage] = None
    system_fingerprint: Optional[str] = None


# ============================================================
# Task 模型 - 通用请求/响应
# ============================================================

class CreateTaskRequest(BaseModel):
    """创建任务请求基类"""
    model: str
    input: Dict[str, Any]
    callBackUrl: Optional[str] = None


class CreateTaskResponse(BaseModel):
    """创建任务响应"""
    code: int
    msg: str
    data: Optional[Dict[str, str]] = None  # {"taskId": "xxx"}

    @property
    def task_id(self) -> Optional[str]:
        return self.data.get("taskId") if self.data else None

    @property
    def is_success(self) -> bool:
        return self.code == 200


class TaskResultJson(BaseModel):
    """任务结果 JSON"""
    resultUrls: Optional[List[str]] = None  # 图像/视频 URL
    resultObject: Optional[Dict[str, Any]] = None  # 文本结果


class QueryTaskResponse(BaseModel):
    """查询任务响应"""
    code: int
    msg: str
    data: Optional[Dict[str, Any]] = None

    @property
    def is_success(self) -> bool:
        return self.code == 200

    @property
    def task_id(self) -> Optional[str]:
        return self.data.get("taskId") if self.data else None

    @property
    def model(self) -> Optional[str]:
        return self.data.get("model") if self.data else None

    @property
    def state(self) -> Optional[TaskState]:
        if self.data and self.data.get("state"):
            return TaskState(self.data["state"])
        return None

    @property
    def result_urls(self) -> List[str]:
        if self.data and self.data.get("resultJson"):
            import json
            result = json.loads(self.data["resultJson"])
            return result.get("resultUrls", [])
        return []

    @property
    def fail_code(self) -> Optional[str]:
        return self.data.get("failCode") if self.data else None

    @property
    def fail_msg(self) -> Optional[str]:
        return self.data.get("failMsg") if self.data else None

    @property
    def cost_time(self) -> Optional[int]:
        return self.data.get("costTime") if self.data else None


# ============================================================
# 图像模型 - Input 参数模型
# ============================================================

class NanoBananaInput(BaseModel):
    """nano-banana (基础文生图) 输入参数"""
    prompt: str = Field(..., max_length=20000)
    output_format: ImageOutputFormat = ImageOutputFormat.PNG
    image_size: AspectRatio = AspectRatio.RATIO_1_1


class NanoBananaEditInput(BaseModel):
    """nano-banana-edit (图像编辑) 输入参数"""
    prompt: str = Field(..., max_length=20000)
    image_urls: List[str] = Field(..., max_length=10)  # 必填，最多10张
    output_format: ImageOutputFormat = ImageOutputFormat.PNG
    image_size: AspectRatio = AspectRatio.RATIO_1_1


class NanoBananaProInput(BaseModel):
    """nano-banana-pro (高级文生图) 输入参数"""
    prompt: str = Field(..., max_length=20000)
    image_input: List[str] = Field(default_factory=list, max_length=8)  # 可选，最多8张
    aspect_ratio: AspectRatio = AspectRatio.RATIO_1_1
    resolution: ImageResolution = ImageResolution.RES_1K
    output_format: ImageOutputFormat = ImageOutputFormat.PNG


# ============================================================
# 视频模型 - Input 参数模型
# ============================================================

class Sora2TextToVideoInput(BaseModel):
    """sora-2-text-to-video 输入参数"""
    prompt: str = Field(..., max_length=10000)
    aspect_ratio: AspectRatio = AspectRatio.LANDSCAPE
    n_frames: VideoFrames = VideoFrames.FRAMES_10
    remove_watermark: bool = True


class Sora2ImageToVideoInput(BaseModel):
    """sora-2-image-to-video 输入参数"""
    prompt: str = Field(..., max_length=10000)
    image_urls: List[str] = Field(...)  # 必填
    aspect_ratio: AspectRatio = AspectRatio.LANDSCAPE
    n_frames: VideoFrames = VideoFrames.FRAMES_10
    remove_watermark: bool = True


class Sora2ProStoryboardInput(BaseModel):
    """sora-2-pro-storyboard 输入参数"""
    n_frames: VideoFrames = VideoFrames.FRAMES_15  # 必填，支持10/15/25
    image_urls: List[str] = Field(default_factory=list)  # 可选
    aspect_ratio: AspectRatio = AspectRatio.LANDSCAPE


# ============================================================
# 成本计算模型
# ============================================================

class CostEstimate(BaseModel):
    """成本估算"""
    model: str
    estimated_cost_usd: Decimal
    estimated_credits: int
    breakdown: Dict[str, Any] = Field(default_factory=dict)


class UsageRecord(BaseModel):
    """使用记录"""
    model: str
    model_type: KieModelType
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    image_count: Optional[int] = None
    video_seconds: Optional[int] = None
    cost_usd: Decimal
    credits_consumed: int
