"""
视频生成相关的请求/响应模型
"""

from enum import Enum
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator

from core.url_validation import validate_url, validate_urls
from .common import TaskStatus


class VideoModel(str, Enum):
    """视频生成模型"""
    SORA_2_TEXT_TO_VIDEO = "sora-2-text-to-video"
    SORA_2_IMAGE_TO_VIDEO = "sora-2-image-to-video"
    SORA_2_PRO_STORYBOARD = "sora-2-pro-storyboard"


class VideoFrames(str, Enum):
    """视频时长（帧数转秒数）"""
    FRAMES_10 = "10"  # 10秒
    FRAMES_15 = "15"  # 15秒
    FRAMES_25 = "25"  # 25秒（仅 sora-2-pro-storyboard）


class VideoAspectRatio(str, Enum):
    """视频宽高比"""
    PORTRAIT = "portrait"  # 竖屏
    LANDSCAPE = "landscape"  # 横屏


# ============================================================
# 请求模型
# ============================================================


class GenerateTextToVideoRequest(BaseModel):
    """文本生成视频请求"""
    prompt: str = Field(..., min_length=1, max_length=10000, description="视频描述")
    model: VideoModel = Field(default=VideoModel.SORA_2_TEXT_TO_VIDEO, description="生成模型")
    n_frames: VideoFrames = Field(default=VideoFrames.FRAMES_10, description="视频时长")
    aspect_ratio: VideoAspectRatio = Field(default=VideoAspectRatio.LANDSCAPE, description="宽高比")
    remove_watermark: bool = Field(default=True, description="是否去水印")
    wait_for_result: bool = Field(default=False, description="是否等待结果")
    conversation_id: Optional[str] = Field(default=None, description="对话 ID（用于任务恢复）")
    placeholder_message_id: Optional[str] = Field(default=None, description="前端占位符消息 ID")
    placeholder_created_at: Optional[str] = Field(default=None, description="占位符创建时间（ISO 8601），用于任务恢复时保持消息排序")


class GenerateImageToVideoRequest(BaseModel):
    """图片生成视频请求"""
    prompt: str = Field(..., min_length=1, max_length=10000, description="视频描述")
    image_url: str = Field(..., description="首帧图片 URL")
    model: VideoModel = Field(default=VideoModel.SORA_2_IMAGE_TO_VIDEO, description="生成模型")
    n_frames: VideoFrames = Field(default=VideoFrames.FRAMES_10, description="视频时长")
    aspect_ratio: VideoAspectRatio = Field(default=VideoAspectRatio.LANDSCAPE, description="宽高比")
    remove_watermark: bool = Field(default=True, description="是否去水印")
    wait_for_result: bool = Field(default=False, description="是否等待结果")
    conversation_id: Optional[str] = Field(default=None, description="对话 ID（用于任务恢复）")
    placeholder_message_id: Optional[str] = Field(default=None, description="前端占位符消息 ID")
    placeholder_created_at: Optional[str] = Field(default=None, description="占位符创建时间（ISO 8601），用于任务恢复时保持消息排序")

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, v: str) -> str:
        """验证图片 URL，防止 SSRF 攻击"""
        return validate_url(v)


class GenerateStoryboardVideoRequest(BaseModel):
    """故事板视频生成请求"""
    n_frames: VideoFrames = Field(default=VideoFrames.FRAMES_15, description="视频时长")
    storyboard_images: Optional[List[str]] = Field(default=None, description="故事板图片列表")
    aspect_ratio: VideoAspectRatio = Field(default=VideoAspectRatio.LANDSCAPE, description="宽高比")
    wait_for_result: bool = Field(default=False, description="是否等待结果")
    conversation_id: Optional[str] = Field(default=None, description="对话 ID（用于任务恢复）")
    placeholder_message_id: Optional[str] = Field(default=None, description="前端占位符消息 ID")
    placeholder_created_at: Optional[str] = Field(default=None, description="占位符创建时间（ISO 8601），用于任务恢复时保持消息排序")

    @field_validator("storyboard_images")
    @classmethod
    def validate_storyboard_images(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """验证故事板图片 URL，防止 SSRF 攻击"""
        if v is None:
            return v
        return validate_urls(v)


# ============================================================
# 响应模型
# ============================================================


class GenerateVideoResponse(BaseModel):
    """视频生成响应"""
    task_id: str = Field(..., description="任务 ID")
    status: TaskStatus = Field(..., description="任务状态")
    video_url: Optional[str] = Field(default=None, description="生成的视频 URL")
    duration_seconds: int = Field(default=0, description="视频时长（秒）")
    credits_consumed: int = Field(default=0, description="消耗的积分")
    cost_usd: float = Field(default=0.0, description="消耗的美元成本")
    cost_time_ms: Optional[int] = Field(default=None, description="耗时（毫秒）")


class TaskStatusResponse(BaseModel):
    """任务状态查询响应"""
    task_id: str = Field(..., description="任务 ID")
    status: TaskStatus = Field(..., description="任务状态")
    video_url: Optional[str] = Field(default=None, description="生成的视频 URL")
    fail_code: Optional[str] = Field(default=None, description="失败代码")
    fail_msg: Optional[str] = Field(default=None, description="失败信息")


class VideoModelInfo(BaseModel):
    """视频模型信息"""
    model_id: str = Field(..., description="模型 ID")
    description: str = Field(..., description="模型描述")
    requires_image_input: bool = Field(..., description="是否需要图片输入")
    requires_prompt: bool = Field(..., description="是否需要文本描述")
    supported_frames: List[str] = Field(..., description="支持的时长")
    supports_watermark_removal: bool = Field(..., description="是否支持去水印")
    credits_per_second: int = Field(..., description="每秒消耗积分")


class VideoModelsResponse(BaseModel):
    """可用模型列表响应"""
    models: List[VideoModelInfo] = Field(..., description="模型列表")
