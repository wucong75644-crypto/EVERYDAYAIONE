"""多模态消息中的文本和媒体内容部件。"""

from typing import Literal, Optional

from pydantic import BaseModel


class TextPart(BaseModel):
    """文本内容。"""

    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    """图片内容及可选的 Workspace 资产信息。"""

    type: Literal["image"] = "image"
    # 线协议要求字段存在；null 专用于生成中/失败占位，缺失属于非法块。
    url: Optional[str]
    original_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    preview_url: Optional[str] = None
    download_url: Optional[str] = None
    asset_id: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    alt: Optional[str] = None
    failed: Optional[bool] = None
    error: Optional[str] = None
    retry_context: Optional[dict] = None
    name: Optional[str] = None
    workspace_path: Optional[str] = None
    size: Optional[int] = None
    mime_type: Optional[str] = None


class VideoPart(BaseModel):
    """视频内容。"""

    type: Literal["video"] = "video"
    url: str
    duration: Optional[float] = None
    thumbnail: Optional[str] = None


class AudioPart(BaseModel):
    """音频内容。"""

    type: Literal["audio"] = "audio"
    url: str
    duration: Optional[float] = None
    transcript: Optional[str] = None


class FilePart(BaseModel):
    """文件内容及任务级资产身份。"""

    type: Literal["file"] = "file"
    url: str
    name: str
    mime_type: str
    size: Optional[int] = None
    workspace_path: Optional[str] = None
    asset_id: Optional[str] = None
