"""
音频相关的 Schema 定义
"""

from pydantic import BaseModel


class AudioUploadResponse(BaseModel):
    """音频上传响应"""
    audio_url: str
    duration: float  # 音频时长（秒）
    size: int  # 文件大小（字节）


class AudioInfoResponse(BaseModel):
    """音频信息响应"""
    duration: float
    size: int


class AudioDeleteRequest(BaseModel):
    """音频删除请求"""
    audio_url: str
