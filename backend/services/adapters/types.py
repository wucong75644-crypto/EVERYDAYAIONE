"""
适配器类型定义

从 base.py 提取的枚举和数据模型，保持向后兼容。
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any


# ============================================================
# 枚举定义
# ============================================================


class ModelProvider(str, Enum):
    """支持的模型提供商"""
    KIE = "kie"              # KIE AI 平台
    GOOGLE = "google"        # Google 官方 Gemini
    OPENAI = "openai"        # OpenAI（预留）
    ANTHROPIC = "anthropic"  # Claude（预留）


class MediaType(str, Enum):
    """媒体类型枚举"""
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"


class TaskStatus(str, Enum):
    """统一任务状态（前端格式）"""
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


# ============================================================
# 数据模型
# ============================================================


@dataclass
class StreamChunk:
    """
    统一流式响应块（OpenAI 兼容格式）

    用于标准化不同 AI 提供商的流式响应格式。
    """
    content: Optional[str] = None           # 增量文本内容
    finish_reason: Optional[str] = None     # 结束原因
    # Token 使用量（通常在最后一帧返回）
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def has_content(self) -> bool:
        return bool(self.content)

    @property
    def has_usage(self) -> bool:
        return self.prompt_tokens > 0 or self.completion_tokens > 0


@dataclass
class ChatResponse:
    """统一非流式聊天响应"""
    content: str                            # 完整回复内容
    finish_reason: Optional[str] = None     # 结束原因
    prompt_tokens: int = 0                  # 输入 token 数
    completion_tokens: int = 0              # 输出 token 数


@dataclass
class CostEstimate:
    """
    成本估算结果

    与现有 CostEstimate 结构对齐
    """
    model: str                              # 模型名称
    estimated_cost_usd: Decimal             # 预估美元成本
    estimated_credits: int                  # 预估积分消耗
    breakdown: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelConfig:
    """单个模型的配置信息"""
    model_id: str                           # 模型 ID（用户传入）
    provider: ModelProvider                 # 提供商
    provider_model: str                     # 提供商侧的模型名
    display_name: str                       # 显示名称
    # 价格（$/1M tokens）
    input_price: float
    output_price: float
    # 积分价格（积分/1K tokens）
    credits_per_1k_input: float = 1.0
    credits_per_1k_output: float = 1.0
    # 能力标记
    supports_vision: bool = False
    supports_video: bool = False
    supports_tools: bool = False
    max_tokens: int = 8192
    context_window: int = 128000


# ============================================================
# 图片/视频生成结果数据模型
# ============================================================


@dataclass
class ImageGenerateResult:
    """
    统一图片生成结果

    适用于所有 Provider 的图片生成返回
    """
    task_id: str                            # 任务 ID
    status: TaskStatus                      # 任务状态
    image_urls: List[str] = field(default_factory=list)  # 生成的图片 URL
    cost_usd: float = 0.0                   # 美元成本
    credits_consumed: int = 0               # 消耗积分
    cost_time_ms: Optional[int] = None      # 耗时（毫秒）
    fail_code: Optional[str] = None         # 失败码
    fail_msg: Optional[str] = None          # 失败信息

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（兼容现有代码）"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "image_urls": self.image_urls,
            "cost_usd": self.cost_usd,
            "credits_consumed": self.credits_consumed,
            "cost_time_ms": self.cost_time_ms,
            "fail_code": self.fail_code,
            "fail_msg": self.fail_msg,
        }


@dataclass
class VideoGenerateResult:
    """
    统一视频生成结果

    适用于所有 Provider 的视频生成返回
    """
    task_id: str                            # 任务 ID
    status: TaskStatus                      # 任务状态
    video_url: Optional[str] = None         # 生成的视频 URL
    duration_seconds: int = 0               # 视频时长（秒）
    cost_usd: float = 0.0                   # 美元成本
    credits_consumed: int = 0               # 消耗积分
    cost_time_ms: Optional[int] = None      # 耗时（毫秒）
    fail_code: Optional[str] = None         # 失败码
    fail_msg: Optional[str] = None          # 失败信息

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（兼容现有代码）"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "video_url": self.video_url,
            "duration_seconds": self.duration_seconds,
            "cost_usd": self.cost_usd,
            "credits_consumed": self.credits_consumed,
            "cost_time_ms": self.cost_time_ms,
            "fail_code": self.fail_code,
            "fail_msg": self.fail_msg,
        }


# ============================================================
# 多模态统一结构
# ============================================================


@dataclass
class MultimodalPart:
    """
    统一多模态内容部件

    解决不同 Provider 格式差异：
    - KIE (OpenAI): {"type": "image_url", "image_url": {"url": "..."}}
    - Google: {"inline_data": {"mime_type": "...", "data": "..."}}

    适配器内部负责转换为对应格式
    """
    type: MediaType                         # 媒体类型
    url: Optional[str] = None               # URL（http/https/data:base64）
    mime_type: Optional[str] = None         # MIME 类型（image/png, video/mp4）
    data: Optional[str] = None              # Base64 数据（可选，用于直接传数据）

    def to_kie_format(self) -> Dict[str, Any]:
        """转换为 KIE (OpenAI) 格式"""
        return {
            "type": "image_url",
            "image_url": {"url": self.url or f"data:{self.mime_type};base64,{self.data}"}
        }

    def to_google_format(self) -> Dict[str, Any]:
        """转换为 Google 官方格式"""
        if self.data:
            return {
                "inline_data": {
                    "mime_type": self.mime_type or "image/png",
                    "data": self.data
                }
            }
        # URL 需要先下载转 base64，或使用 File API
        return {
            "file_data": {
                "file_uri": self.url,
                "mime_type": self.mime_type
            }
        }


@dataclass
class UnifiedMessage:
    """
    统一消息格式

    调用方传入此格式，适配器内部转换为 Provider 格式
    """
    role: str                               # user / assistant / system
    content: str                            # 文本内容
    parts: List[MultimodalPart] = field(default_factory=list)  # 多模态部件列表
