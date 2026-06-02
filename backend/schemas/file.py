"""
文件上传响应模型

仅保留文件上传功能的响应 schema
"""

from typing import Optional

from pydantic import BaseModel, Field


class UploadFileResponse(BaseModel):
    """文件上传响应"""
    url: str = Field(..., description="上传后的文件 CDN URL")
    name: str = Field(..., description="文件名")
    mime_type: str = Field(..., description="MIME 类型")
    size: int = Field(..., description="文件大小（字节）")
    workspace_path: Optional[str] = Field(
        None,
        description="工作区相对路径（如 上传/2026-06/xxx_uuid.ext），供 file_path_cache 注册与 AI 工具读取",
    )
