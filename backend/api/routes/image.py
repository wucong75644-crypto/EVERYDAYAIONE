"""
图像上传路由

提供图片上传接口。
注：图像生成功能已迁移到统一消息 API (/messages/generate)
"""

from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form
from loguru import logger

from api.deps import Database, OrgCtx
from core.exceptions import (
    AppException,
    ValidationError,
    PermissionDeniedError,
)
from schemas.image import UploadImageResponse
from services.storage_service import StorageService

router = APIRouter(prefix="/images", tags=["图像"])


@router.post("/upload", response_model=UploadImageResponse, summary="上传图片")
async def upload_image(
    ctx: OrgCtx,
    db: Database,
    file: Optional[UploadFile] = File(None),
    image_data: Optional[str] = Form(None),
):
    """
    上传图片到存储服务

    支持两种上传方式：
    - FormData 上传文件（推荐，体积更小）
    - base64 编码上传（兼容旧版）

    用于图像编辑功能，先上传本地图片获取 URL。
    """
    try:
        storage = StorageService(db)

        if file:
            # FormData 方式：直接上传文件
            content = await file.read()
            url = await storage.upload_image(
                user_id=ctx.user_id,
                file_data=content,
                content_type=file.content_type or "image/jpeg",
                filename=file.filename,
                org_id=ctx.org_id,
            )
        elif image_data:
            # base64 方式：兼容旧版
            url = await storage.upload_base64_image(
                user_id=ctx.user_id,
                base64_data=image_data,
                org_id=ctx.org_id,
            )
        else:
            raise ValidationError(message="请提供图片文件或 base64 数据")

        return UploadImageResponse(url=url)
    except (
        ValidationError,
        PermissionDeniedError,
        AppException,
    ):
        raise
    except Exception as e:
        logger.error(
            f"Upload image failed | user_id={ctx.user_id} | "
            f"file={file.filename if file else 'base64'} | error={str(e)}"
        )
        raise AppException(
            code="UPLOAD_IMAGE_ERROR",
            message="图片上传失败",
            status_code=500,
        )
