"""
文件上传路由

提供文档文件上传接口：
- /upload: 上传到 OSS（CDN URL，用于聊天多模态内容）
- /workspace/upload: 上传到 workspace（ossfs 目录，供 AI 分析）
- /workspace/list: 列出用户 workspace 文件
"""

from typing import List, Optional

from fastapi import APIRouter, UploadFile, File
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUser, Database
from core.exceptions import AppException, ValidationError
from schemas.file import UploadFileResponse
from services.storage_service import StorageService

router = APIRouter(prefix="/files", tags=["文件"])


# ============================================================
# OSS 上传（聊天多模态用）
# ============================================================


@router.post("/upload", response_model=UploadFileResponse, summary="上传文件到OSS")
async def upload_file(
    current_user: CurrentUser,
    db: Database,
    file: UploadFile = File(...),
):
    """
    上传文档文件到 OSS，获取 CDN URL 后作为多模态内容发送给模型。
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


# ============================================================
# Workspace 上传（AI 文件分析用）
# ============================================================


# workspace 允许的文件扩展名
_WORKSPACE_ALLOWED_EXTENSIONS = frozenset({
    "txt", "csv", "json", "yaml", "yml", "xml", "md", "log", "tsv",
    "py", "js", "ts", "html", "css", "sql",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "zip",
})

# workspace 单文件大小上限 (50MB)
_WORKSPACE_MAX_FILE_SIZE = 50 * 1024 * 1024


class WorkspaceUploadResponse(BaseModel):
    """workspace 文件上传响应"""
    filename: str = Field(..., description="文件名")
    path: str = Field(..., description="workspace 内相对路径")
    size: int = Field(..., description="文件大小（字节）")
    cdn_url: Optional[str] = Field(None, description="CDN 下载 URL")


class WorkspaceFileItem(BaseModel):
    """workspace 文件列表项"""
    name: str
    is_dir: bool
    size: int = 0
    modified: str = ""


class WorkspaceListResponse(BaseModel):
    """workspace 文件列表响应"""
    path: str
    items: List[WorkspaceFileItem]
    total: int


@router.post(
    "/workspace/upload",
    response_model=WorkspaceUploadResponse,
    summary="上传文件到workspace（供AI分析）",
)
async def upload_to_workspace(
    current_user: CurrentUser,
    db: Database,
    file: UploadFile = File(...),
):
    """
    上传文件到用户的 workspace 目录（ossfs 挂载），供 AI 读取分析。

    文件存储路径: workspace/{org_id或personal}/{user_id}/uploads/{filename}
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
    if ext not in _WORKSPACE_ALLOWED_EXTENSIONS:
        raise ValidationError(
            message=f"不支持的文件类型: .{ext}。"
            f"支持: {', '.join(sorted(_WORKSPACE_ALLOWED_EXTENSIONS))}"
        )

    # 读取文件内容
    content = await file.read()
    if len(content) > _WORKSPACE_MAX_FILE_SIZE:
        raise ValidationError(
            message=f"文件过大: {len(content) / 1024 / 1024:.1f}MB，上限 50MB"
        )

    user_id = current_user["id"]
    org_id = current_user.get("org_id")

    try:
        executor = FileExecutor(
            workspace_root=settings.file_workspace_root,
            user_id=user_id,
            org_id=org_id,
        )

        # 生成唯一文件名防止覆盖，写入 uploads/ 子目录
        unique_name = executor.generate_unique_filename(filename)
        upload_path = f"uploads/{unique_name}"
        target = executor.resolve_safe_path(upload_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

        # 生成 CDN URL
        cdn_url = executor.get_cdn_url(upload_path)

        logger.info(
            f"Workspace upload | user={user_id} | file={filename} | "
            f"size={len(content)} | path={upload_path}"
        )

        return WorkspaceUploadResponse(
            filename=unique_name,
            path=upload_path,
            size=len(content),
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


@router.get(
    "/workspace/list",
    response_model=WorkspaceListResponse,
    summary="列出workspace文件",
)
async def list_workspace(
    current_user: CurrentUser,
    db: Database,
    path: str = ".",
):
    """列出用户 workspace 目录内容"""
    from core.config import get_settings
    from services.file_executor import FileExecutor

    settings = get_settings()
    if not settings.file_workspace_enabled:
        raise AppException(
            code="FILE_WORKSPACE_DISABLED",
            message="文件操作功能未启用",
            status_code=403,
        )

    user_id = current_user["id"]
    org_id = current_user.get("org_id")

    executor = FileExecutor(
        workspace_root=settings.file_workspace_root,
        user_id=user_id,
        org_id=org_id,
    )

    target = executor.resolve_safe_path(path)
    if not target.exists() or not target.is_dir():
        return WorkspaceListResponse(path=path, items=[], total=0)

    items = []
    for item in sorted(target.iterdir()):
        if item.name.startswith("."):
            continue
        try:
            st = item.stat()
            items.append(WorkspaceFileItem(
                name=item.name,
                is_dir=item.is_dir(),
                size=st.st_size if item.is_file() else 0,
                modified=str(int(st.st_mtime)),
            ))
        except (PermissionError, OSError):
            continue

    return WorkspaceListResponse(path=path, items=items, total=len(items))
