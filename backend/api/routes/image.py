"""
图像上传路由

提供图片上传接口（双写工作区 + OSS）。
注：图像生成功能已迁移到统一消息 API (/messages/generate)
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form
from loguru import logger

from api.deps import Database, OrgCtx, ScopedDB
from core.exceptions import (
    AppException,
    ValidationError,
    PermissionDeniedError,
)
from schemas.image import UploadImageResponse
from services.storage_service import StorageService
from services.user_activity_service import record_user_activity
from services.file_upload import build_workspace_thumbnail_url
from services.assets import register_web_upload_best_effort

router = APIRouter(prefix="/images", tags=["图像"])


# 图片单文件大小上限 (100MB) — 与 /files/upload 对齐
_IMAGE_MAX_FILE_SIZE = 100 * 1024 * 1024

# 图片允许扩展名（不含 svg —— SVG 可嵌入 JS 通过 CDN 上下文执行 XSS）
_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp"})


@router.post("/upload", response_model=UploadImageResponse, summary="上传图片（双写工作区+OSS）")
async def upload_image(
    ctx: OrgCtx,
    db: ScopedDB,
    file: Optional[UploadFile] = File(None),
    image_data: Optional[str] = Form(None),
):
    """上传图片：FormData 方式走双写（工作区+OSS）；base64 方式仅 OSS（保持兼容）。

    返回 url + workspace_path：
      - url 供视觉模型/前端展示
      - workspace_path 供后端 file_path_cache 注册 + AI 历史回看
    base64 路径不落工作区（无原始文件名，仅 OSS）。
    """
    import aiofiles

    from core.config import get_settings
    from core.workspace import resolve_upload_relpath
    from services.file_executor import FileExecutor

    settings = get_settings()
    user_id = ctx.user_id
    org_id = ctx.org_id

    try:
        # ── FormData 路径：双写工作区 + OSS ──
        if file:
            if not settings.file_workspace_enabled:
                # 工作区禁用时退回仅 OSS（兼容兜底）
                storage = StorageService(db)
                content = await file.read()
                url = await storage.upload_image(
                    user_id=user_id,
                    file_data=content,
                    content_type=file.content_type or "image/jpeg",
                    filename=file.filename,
                    org_id=org_id,
                )
                thumbnail_url = build_workspace_thumbnail_url(url)
                register_web_upload_best_effort(
                    db,
                    user_id=user_id,
                    org_id=org_id,
                    url=url,
                    name=file.filename or "image",
                    mime_type=file.content_type or "image/jpeg",
                    size=len(content),
                    thumbnail_url=thumbnail_url,
                )
                return UploadImageResponse(
                    url=url,
                    original_url=url,
                    thumbnail_url=thumbnail_url,
                    preview_url=url,
                    download_url=url,
                )

            filename = file.filename or "image.png"
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext not in _IMAGE_EXTENSIONS:
                raise ValidationError(
                    message=f"不支持的图片格式: .{ext}。支持: {', '.join(sorted(_IMAGE_EXTENSIONS))}"
                )

            executor = FileExecutor(
                workspace_root=settings.file_workspace_root,
                user_id=user_id,
                org_id=org_id,
            )

            unique_name = executor.generate_unique_filename(filename)
            upload_relpath_prefix = resolve_upload_relpath(user_id, org_id)
            upload_path = f"{upload_relpath_prefix}/{unique_name}"
            target = executor.resolve_safe_path(upload_path)
            target.parent.mkdir(parents=True, exist_ok=True)

            total_size = 0
            async with aiofiles.open(target, "wb") as f:
                while chunk := await file.read(1024 * 1024):
                    total_size += len(chunk)
                    if total_size > _IMAGE_MAX_FILE_SIZE:
                        await f.close()
                        target.unlink(missing_ok=True)
                        raise ValidationError(
                            message=f"图片过大: {total_size / 1024 / 1024:.1f}MB，上限 100MB"
                        )
                    await f.write(chunk)

            # 同步 OSS（失败不致命）
            cdn_url = None
            try:
                from services.oss_service import get_oss_service
                oss = get_oss_service()
                rel_path = str(target.relative_to(Path(settings.file_workspace_root).resolve()))
                cdn_url = await oss.sync_workspace_file(target, rel_path)
                thumbnail_url = await oss.sync_workspace_thumbnail(target, rel_path) if cdn_url else None
            except Exception as e:
                logger.warning(f"Image OSS sync failed | file={filename} | error={e}")
                thumbnail_url = None
            if not cdn_url:
                cdn_url = executor.get_cdn_url(upload_path)

            mime_type = file.content_type or f"image/{ext if ext != 'jpg' else 'jpeg'}"
            logger.info(
                f"Image upload | user={user_id} | file={filename} | "
                f"size={total_size} | path={upload_path}"
            )
            record_user_activity(
                db,
                user_id=user_id,
                event_type="file_uploaded",
                org_id=org_id,
                source="web",
                resource_type="workspace_file",
                resource_id=upload_path,
                metadata={"filename": filename, "size": total_size, "kind": "image"},
            )
            register_web_upload_best_effort(
                db,
                user_id=user_id,
                org_id=org_id,
                url=cdn_url or "",
                name=unique_name,
                mime_type=mime_type,
                size=total_size,
                workspace_path=upload_path,
                thumbnail_url=thumbnail_url,
            )
            return UploadImageResponse(
                url=cdn_url or "",
                original_url=cdn_url or "",
                thumbnail_url=thumbnail_url,
                preview_url=cdn_url or "",
                download_url=cdn_url or "",
                name=unique_name,
                workspace_path=upload_path,
                size=total_size,
                mime_type=mime_type,
            )

        # ── base64 路径：仅 OSS（兼容旧版） ──
        if image_data:
            storage = StorageService(db)
            url = await storage.upload_base64_image(
                user_id=user_id,
                base64_data=image_data,
                org_id=org_id,
            )
            record_user_activity(
                db,
                user_id=user_id,
                event_type="file_uploaded",
                org_id=org_id,
                source="web",
                resource_type="image",
                metadata={"kind": "base64_image"},
            )
            thumbnail_url = build_workspace_thumbnail_url(url)
            register_web_upload_best_effort(
                db,
                user_id=user_id,
                org_id=org_id,
                url=url,
                name="image",
                mime_type="image/jpeg",
                thumbnail_url=thumbnail_url,
            )
            return UploadImageResponse(
                url=url,
                original_url=url,
                thumbnail_url=thumbnail_url,
                preview_url=url,
                download_url=url,
            )

        raise ValidationError(message="请提供图片文件或 base64 数据")

    except (ValidationError, PermissionDeniedError, AppException):
        raise
    except PermissionError as e:
        raise ValidationError(message=str(e))
    except Exception as e:
        logger.error(
            f"Upload image failed | user_id={user_id} | "
            f"file={file.filename if file else 'base64'} | error={str(e)}"
        )
        raise AppException(
            code="UPLOAD_IMAGE_ERROR",
            message="图片上传失败",
            status_code=500,
        )
