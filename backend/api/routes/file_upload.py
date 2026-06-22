"""
文件上传路由

- POST /upload: 上传文件双写（落 上传/{YYYY-MM}/ + 同步 OSS）
- POST /workspace/upload: 上传到 workspace 指定子目录（用户主动选目录的场景）
"""

import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, UploadFile, File
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import OrgCtx, ScopedDB
from core.exceptions import AppException, ValidationError
from schemas.file import UploadFileResponse

from .file_common import (
    WORKSPACE_ALLOWED_EXTENSIONS,
    WORKSPACE_MAX_FILE_SIZE,
)

router = APIRouter()


class WorkspaceUploadResponse(BaseModel):
    """workspace 文件上传响应"""
    filename: str = Field(..., description="文件名")
    path: str = Field(..., description="workspace 内相对路径")
    size: int = Field(..., description="文件大小（字节）")
    cdn_url: Optional[str] = Field(None, description="CDN 下载 URL")


@router.post("/upload", response_model=UploadFileResponse, summary="上传文件（双写工作区+OSS）")
async def upload_file(
    ctx: OrgCtx,
    db: ScopedDB,
    file: UploadFile = File(...),
):
    """统一上传入口：落 上传/{YYYY-MM}/ 工作区 + 同步 OSS CDN。

    所有用户附件（图片/文档/数据文件）走这一条路。
    返回 url + workspace_path：
      - url 供视觉模型/前端展示
      - workspace_path 供后端 file_path_cache 注册 + AI 工具读取
    """
    import aiofiles

    from core.config import get_settings
    from core.workspace import resolve_upload_relpath
    from services.file_executor import FileExecutor

    settings = get_settings()
    if not settings.file_workspace_enabled:
        raise AppException(
            code="FILE_WORKSPACE_DISABLED",
            message="文件上传功能未启用",
            status_code=403,
        )

    # 校验文件名和扩展名
    filename = file.filename or "unnamed"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in WORKSPACE_ALLOWED_EXTENSIONS:
        raise ValidationError(
            message=f"不支持的文件类型: .{ext}。"
            f"支持: {', '.join(sorted(WORKSPACE_ALLOWED_EXTENSIONS))}"
        )

    user_id = ctx.user_id
    org_id = ctx.org_id

    try:
        executor = FileExecutor(
            workspace_root=settings.file_workspace_root,
            user_id=user_id,
            org_id=org_id,
        )

        # 唯一文件名防覆盖；落到 上传/{YYYY-MM}/
        unique_name = executor.generate_unique_filename(filename)
        upload_relpath_prefix = resolve_upload_relpath(user_id, org_id)
        upload_path = f"{upload_relpath_prefix}/{unique_name}"
        target = executor.resolve_safe_path(upload_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        # 流式分块写入，固定占 ~1MB 内存
        total_size = 0
        async with aiofiles.open(target, 'wb') as f:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > WORKSPACE_MAX_FILE_SIZE:
                    await f.close()
                    target.unlink(missing_ok=True)
                    raise ValidationError(
                        message=f"文件过大: {total_size / 1024 / 1024:.1f}MB，上限 100MB"
                    )
                await f.write(chunk)

        # 同步到 OSS 并生成 CDN URL（失败不致命，落盘已成功即认上传成功）
        cdn_url = None
        try:
            from services.oss_service import get_oss_service
            oss = get_oss_service()
            rel_path = str(target.relative_to(Path(settings.file_workspace_root).resolve()))
            cdn_url = await oss.sync_workspace_file(target, rel_path)
        except Exception as e:
            logger.warning(f"Upload OSS sync failed | file={filename} | error={e}")
        if not cdn_url:
            cdn_url = executor.get_cdn_url(upload_path)

        mime_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

        logger.info(
            f"Upload | user={user_id} | file={filename} | "
            f"size={total_size} | path={upload_path}"
        )

        return UploadFileResponse(
            url=cdn_url or "",
            name=unique_name,
            mime_type=mime_type,
            size=total_size,
            workspace_path=upload_path,
        )

    except (ValidationError, AppException):
        raise
    except PermissionError as e:
        raise ValidationError(message=str(e))
    except Exception as e:
        logger.error(
            f"Upload failed | user={user_id} | "
            f"file={filename} | error={e}"
        )
        raise AppException(
            code="UPLOAD_FILE_ERROR",
            message="文件上传失败",
            status_code=500,
        )


@router.post(
    "/workspace/upload",
    response_model=WorkspaceUploadResponse,
    summary="上传文件到workspace（供AI分析）",
)
async def upload_to_workspace(
    ctx: OrgCtx,
    db: ScopedDB,
    file: UploadFile = File(...),
    target_dir: str = Form(default="."),
):
    """
    上传文件到用户的 workspace 目录（ossfs 挂载），供 AI 读取分析。

    Args:
        file: 上传的文件
        target_dir: 目标目录（相对于用户 workspace 根目录，默认 "." 即根目录）
    """
    from core.config import get_settings
    from services.file_executor import FileExecutor

    settings = get_settings()
    if not settings.file_workspace_enabled:
        raise AppException(
            code="FILE_WORKSPACE_DISABLED",
            message="文件操作功能未启用",
            status_code=403,
        )

    # 校验文件名和扩展名
    filename = file.filename or "unnamed"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in WORKSPACE_ALLOWED_EXTENSIONS:
        raise ValidationError(
            message=f"不支持的文件类型: .{ext}。"
            f"支持: {', '.join(sorted(WORKSPACE_ALLOWED_EXTENSIONS))}"
        )

    import aiofiles

    user_id = ctx.user_id
    org_id = ctx.org_id

    try:
        executor = FileExecutor(
            workspace_root=settings.file_workspace_root,
            user_id=user_id,
            org_id=org_id,
        )

        # 生成唯一文件名防止覆盖，写入目标目录
        unique_name = executor.generate_unique_filename(filename)
        # target_dir="." 时放根目录，否则放指定子目录
        clean_dir = target_dir.strip("/").strip("\\")
        upload_path = f"{clean_dir}/{unique_name}" if clean_dir and clean_dir != "." else unique_name
        target = executor.resolve_safe_path(upload_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        # 流式分块写入，固定占 ~1MB 内存
        total_size = 0
        async with aiofiles.open(target, 'wb') as f:
            while chunk := await file.read(1024 * 1024):  # 每次读 1MB
                total_size += len(chunk)
                if total_size > WORKSPACE_MAX_FILE_SIZE:
                    await f.close()
                    target.unlink(missing_ok=True)
                    raise ValidationError(
                        message=f"文件过大: {total_size / 1024 / 1024:.1f}MB，上限 100MB"
                    )
                await f.write(chunk)

        # 同步到 OSS 并生成 CDN URL
        cdn_url = None
        try:
            from services.oss_service import get_oss_service
            oss = get_oss_service()
            rel_path = str(target.relative_to(Path(settings.file_workspace_root).resolve()))
            cdn_url = await oss.sync_workspace_file(target, rel_path)
        except Exception as e:
            logger.warning(f"Workspace OSS sync failed | file={filename} | error={e}")
        if not cdn_url:
            cdn_url = executor.get_cdn_url(upload_path)

        logger.info(
            f"Workspace upload | user={user_id} | file={filename} | "
            f"size={total_size} | path={upload_path}"
        )

        return WorkspaceUploadResponse(
            filename=unique_name,
            path=upload_path,
            size=total_size,
            cdn_url=cdn_url,
        )

    except PermissionError as e:
        raise ValidationError(message=str(e))
    except Exception as e:
        logger.error(
            f"Workspace upload failed | user={user_id} | "
            f"file={filename} | error={e}"
        )
        raise AppException(
            code="WORKSPACE_UPLOAD_ERROR",
            message="文件上传失败",
            status_code=500,
        )
