"""
图像上传响应模型

注：图像生成功能已迁移到统一消息 API (/messages/generate)
类型定义（ImageModel, AspectRatio 等）在 services/adapters/kie/models.py 中
此文件仅保留上传功能的响应 schema
"""

from pydantic import BaseModel, Field


class UploadImageResponse(BaseModel):
    """图片上传响应"""
    url: str = Field(..., description="上传后的图片公开 URL")
