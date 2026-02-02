"""
图像生成相关的请求/响应模型
"""

from enum import Enum
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator

from core.url_validation import validate_urls
from .common import TaskStatus


class ImageModel(str, Enum):
    """图像生成模型"""
    NANO_BANANA = "google/nano-banana"
    NANO_BANANA_EDIT = "google/nano-banana-edit"
    NANO_BANANA_PRO = "nano-banana-pro"


class AspectRatio(str, Enum):
    """图像宽高比"""
    SQUARE = "1:1"
    PORTRAIT_9_16 = "9:16"
    LANDSCAPE_16_9 = "16:9"
    PORTRAIT_3_4 = "3:4"
    LANDSCAPE_4_3 = "4:3"
    PORTRAIT_2_3 = "2:3"
    LANDSCAPE_3_2 = "3:2"
    PORTRAIT_4_5 = "4:5"
    LANDSCAPE_5_4 = "5:4"
    ULTRAWIDE = "21:9"
    AUTO = "auto"


class ImageResolution(str, Enum):
    """图像分辨率（仅 nano-banana-pro）"""
    RES_1K = "1K"
    RES_2K = "2K"
    RES_4K = "4K"


class ImageOutputFormat(str, Enum):
    """图像输出格式"""
    PNG = "png"
    JPEG = "jpeg"
    JPG = "jpg"


# ============================================================
# 请求模型
# ============================================================


class GenerateImageRequest(BaseModel):
    """图像生成请求"""
    prompt: str = Field(..., min_length=1, max_length=20000, description="图像描述")
    model: ImageModel = Field(default=ImageModel.NANO_BANANA, description="生成模型")
    size: AspectRatio = Field(default=AspectRatio.SQUARE, description="宽高比")
    output_format: ImageOutputFormat = Field(default=ImageOutputFormat.PNG, description="输出格式")
    resolution: Optional[ImageResolution] = Field(
        default=None,
        description="分辨率（仅 nano-banana-pro 支持）"
    )
    wait_for_result: bool = Field(default=True, description="是否等待结果")
    conversation_id: Optional[str] = Field(default=None, description="对话 ID（用于任务恢复）")
    placeholder_message_id: Optional[str] = Field(default=None, description="前端占位符消息 ID")
    placeholder_created_at: Optional[str] = Field(default=None, description="占位符创建时间（ISO 8601），用于任务恢复时保持消息排序")


class EditImageRequest(BaseModel):
    """图像编辑请求"""
    prompt: str = Field(..., min_length=1, max_length=20000, description="编辑指令")
    image_urls: List[str] = Field(..., min_length=1, max_length=10, description="输入图片 URL")
    size: AspectRatio = Field(default=AspectRatio.SQUARE, description="输出宽高比")
    output_format: ImageOutputFormat = Field(default=ImageOutputFormat.PNG, description="输出格式")
    wait_for_result: bool = Field(default=True, description="是否等待结果")
    conversation_id: Optional[str] = Field(default=None, description="对话 ID（用于任务恢复）")
    placeholder_message_id: Optional[str] = Field(default=None, description="前端占位符消息 ID")
    placeholder_created_at: Optional[str] = Field(default=None, description="占位符创建时间（ISO 8601），用于任务恢复时保持消息排序")

    @field_validator("image_urls")
    @classmethod
    def validate_image_urls(cls, v: List[str]) -> List[str]:
        """验证图片 URL，防止 SSRF 攻击"""
        return validate_urls(v)


# ============================================================
# 响应模型
# ============================================================


class GenerateImageResponse(BaseModel):
    """图像生成响应"""
    task_id: str = Field(..., description="任务 ID")
    status: TaskStatus = Field(..., description="任务状态")
    image_urls: List[str] = Field(default_factory=list, description="生成的图片 URL")
    credits_consumed: int = Field(default=0, description="消耗的积分")
    cost_usd: float = Field(default=0.0, description="消耗的美元成本")
    cost_time_ms: Optional[int] = Field(default=None, description="耗时（毫秒）")


class TaskStatusResponse(BaseModel):
    """任务状态查询响应"""
    task_id: str = Field(..., description="任务 ID")
    status: TaskStatus = Field(..., description="任务状态")
    image_urls: List[str] = Field(default_factory=list, description="生成的图片 URL")
    fail_code: Optional[str] = Field(default=None, description="失败错误码")
    fail_msg: Optional[str] = Field(default=None, description="失败原因")


class ImageModelInfo(BaseModel):
    """图像模型信息"""
    model_id: str
    description: str
    requires_image_input: bool
    supported_sizes: List[str]
    supported_formats: List[str]
    supports_resolution: bool
    credits_per_image: int | dict


class ImageModelsResponse(BaseModel):
    """图像模型列表响应"""
    models: List[ImageModelInfo]


class UploadImageRequest(BaseModel):
    """图片上传请求（base64）"""
    image_data: str = Field(..., description="base64 编码的图片数据（可包含 data URL 前缀）")


class UploadImageResponse(BaseModel):
    """图片上传响应"""
    url: str = Field(..., description="上传后的图片公开 URL")
