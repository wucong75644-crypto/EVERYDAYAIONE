"""
图像上传响应模型

注：图像生成功能已迁移到统一消息 API (/messages/generate)
类型定义（ImageModel, AspectRatio 等）在 services/adapters/kie/models.py 中
此文件仅保留上传功能的响应 schema
"""

from typing import Optional

from pydantic import BaseModel, Field


class UploadImageResponse(BaseModel):
    """图片上传响应"""
    url: str = Field(..., description="上传后的图片公开 URL")
    original_url: Optional[str] = Field(None, description="原图 URL（模型输入/下载使用）")
    thumbnail_url: Optional[str] = Field(None, description="缩略图 URL（小图展示使用）")
    preview_url: Optional[str] = Field(None, description="预览 URL（放大查看使用）")
    download_url: Optional[str] = Field(None, description="下载 URL")
    name: Optional[str] = Field(
        None,
        description="工作区文件名（含 UUID 后缀，供 LLM 引用与 file_path_cache 查询）",
    )
    workspace_path: Optional[str] = Field(
        None,
        description="工作区相对路径（如 上传/2026-06/xxx_uuid.png），供 file_path_cache 注册",
    )
    size: Optional[int] = Field(None, description="文件大小（字节）")
    mime_type: Optional[str] = Field(None, description="MIME 类型")
