"""
文件上传路由

提供文档文件上传接口（当前仅支持 PDF）。
"""

from fastapi import APIRouter, UploadFile, File
from loguru import logger

from api.deps import CurrentUser, Database
from core.exceptions import AppException, ValidationError
from schemas.file import UploadFileResponse
from services.storage_service import StorageService

router = APIRouter(prefix="/files", tags=["文件"])


@router.post("/upload", response_model=UploadFileResponse, summary="上传文件")
async def upload_file(
    current_user: CurrentUser,
    db: Database,
    file: UploadFile = File(...),
):
    """
    上传文档文件到存储服务（当前仅支持 PDF）

    用于聊天时上传 PDF 文档，获取 CDN URL 后作为多模态内容发送给模型。
    """
    try:
        storage = StorageService(db)
        content = await file.read()

        result = await storage.upload_file(
            user_id=current_user["id"],
            file_data=content,
            content_type=file.content_type or "application/octet-stream",
            filename=file.filename,
        )

        return UploadFileResponse(**result)

    except ValueError as e:
        raise ValidationError(message=str(e))
    except (ValidationError, AppException):
        raise
    except Exception as e:
        logger.error(
            f"Upload file failed | user_id={current_user['id']} | "
            f"file={file.filename} | error={str(e)}"
        )
        raise AppException(
            code="UPLOAD_FILE_ERROR",
            message="文件上传失败",
            status_code=500,
        )
