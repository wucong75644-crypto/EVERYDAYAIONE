"""
文件上传路由

提供文档文件上传接口：
- /upload: 上传到 OSS（CDN URL，用于聊天多模态内容）
- /workspace/upload: 上传到 workspace（ossfs 目录，供 AI 分析）
- /workspace/list: 列出用户 workspace 文件
- /workspace/search: 递归搜索 workspace 文件（关键词匹配文件名）
- /workspace/delete: 删除文件或空目录
- /workspace/mkdir: 新建文件夹
- /workspace/rename: 重命名
- /workspace/move: 移动文件
"""

import mimetypes
from typing import List, Optional

from fastapi import APIRouter, Form, UploadFile, File
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUser, Database, OrgCtx, ScopedDB
from core.exceptions import AppException, ValidationError
from schemas.file import UploadFileResponse
from services.storage_service import StorageService

router = APIRouter(prefix="/files", tags=["文件"])


# ============================================================
# OSS 上传（聊天多模态用）
# ============================================================


@router.post("/upload", response_model=UploadFileResponse, summary="上传文件到OSS")
async def upload_file(
    ctx: OrgCtx,
    db: ScopedDB,
    file: UploadFile = File(...),
):
    """
    上传文档文件到 OSS，获取 CDN URL 后作为多模态内容发送给模型。
    """
    try:
        storage = StorageService(db)
        content = await file.read()

        result = await storage.upload_file(
            user_id=ctx.user_id,
            file_data=content,
            content_type=file.content_type or "application/octet-stream",
            filename=file.filename,
            org_id=ctx.org_id,
        )

        return UploadFileResponse(**result)

    except ValueError as e:
        raise ValidationError(message=str(e))
    except (ValidationError, AppException):
        raise
    except Exception as e:
        logger.error(
            f"Upload file failed | user_id={ctx.user_id} | "
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

# workspace 单文件大小上限 (100MB)
_WORKSPACE_MAX_FILE_SIZE = 100 * 1024 * 1024


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
    cdn_url: Optional[str] = None
    mime_type: Optional[str] = None
    workspace_path: Optional[str] = None


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
    if ext not in _WORKSPACE_ALLOWED_EXTENSIONS:
        raise ValidationError(
            message=f"不支持的文件类型: .{ext}。"
            f"支持: {', '.join(sorted(_WORKSPACE_ALLOWED_EXTENSIONS))}"
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
                if total_size > _WORKSPACE_MAX_FILE_SIZE:
                    await f.close()
                    target.unlink(missing_ok=True)
                    raise ValidationError(
                        message=f"文件过大: {total_size / 1024 / 1024:.1f}MB，上限 100MB"
                    )
                await f.write(chunk)

        # 生成 CDN URL
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


@router.get(
    "/workspace/list",
    response_model=WorkspaceListResponse,
    summary="列出workspace文件",
)
async def list_workspace(
    ctx: OrgCtx,
    db: ScopedDB,
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

    user_id = ctx.user_id
    org_id = ctx.org_id

    executor = FileExecutor(
        workspace_root=settings.file_workspace_root,
        user_id=user_id,
        org_id=org_id,
    )

    target = executor.resolve_safe_path(path)
    if not target.exists() or not target.is_dir():
        return WorkspaceListResponse(path=path, items=[], total=0)

    # 拼接相对路径前缀（用于 CDN URL 计算）
    path_prefix = path.strip("/").strip("\\")

    items = []
    for item in sorted(target.iterdir()):
        if item.name.startswith(".") or item.name == "staging":
            continue
        try:
            st = item.stat()
            is_file = item.is_file()

            # 文件：生成 CDN URL 和 MIME 类型
            cdn_url = None
            mime_type = None
            if is_file:
                rel_path = f"{path_prefix}/{item.name}" if path_prefix and path_prefix != "." else item.name
                cdn_url = executor.get_cdn_url(rel_path)
                mime_type = mimetypes.guess_type(item.name)[0]

            items.append(WorkspaceFileItem(
                name=item.name,
                is_dir=item.is_dir(),
                size=st.st_size if is_file else 0,
                modified=str(int(st.st_mtime)),
                cdn_url=cdn_url,
                mime_type=mime_type,
            ))
        except (PermissionError, OSError):
            continue

    return WorkspaceListResponse(path=path, items=items, total=len(items))


# ============================================================
# Workspace 文件搜索（递归关键词匹配文件名）
# ============================================================


class WorkspaceSearchResponse(BaseModel):
    """workspace 文件搜索响应"""
    items: List[WorkspaceFileItem]
    total: int


@router.get(
    "/workspace/search",
    response_model=WorkspaceSearchResponse,
    summary="搜索workspace文件",
)
async def search_workspace(
    ctx: OrgCtx,
    q: str = "",
    limit: int = 20,
):
    """递归搜索用户 workspace 目录，按文件名关键词匹配"""
    if not q.strip():
        return WorkspaceSearchResponse(items=[], total=0)

    executor = _get_executor(ctx)
    root = executor.resolve_safe_path(".")
    if not root.exists() or not root.is_dir():
        return WorkspaceSearchResponse(items=[], total=0)

    keyword = q.strip().lower()
    results: list[WorkspaceFileItem] = []

    # 递归遍历，跳过隐藏文件和 staging 目录
    for item in root.rglob("*"):
        if len(results) >= limit:
            break
        # 跳过目录、隐藏文件、staging
        if item.is_dir():
            continue
        if any(part.startswith(".") or part == "staging" for part in item.relative_to(root).parts):
            continue
        if keyword not in item.name.lower():
            continue

        try:
            st = item.stat()
            rel_path = str(item.relative_to(root))
            cdn_url = executor.get_cdn_url(rel_path)
            mime_type = mimetypes.guess_type(item.name)[0]
            results.append(WorkspaceFileItem(
                name=item.name,
                is_dir=False,
                size=st.st_size,
                modified=str(int(st.st_mtime)),
                cdn_url=cdn_url,
                mime_type=mime_type,
                workspace_path=rel_path,
            ))
        except (PermissionError, OSError):
            continue

    return WorkspaceSearchResponse(items=results, total=len(results))


# ============================================================
# Workspace 文件管理（删除/新建文件夹/重命名/移动）
# ============================================================


class WorkspacePathRequest(BaseModel):
    """单路径请求"""
    path: str = Field(..., description="相对路径", max_length=500)


class WorkspaceMkdirResponse(BaseModel):
    """新建文件夹响应"""
    success: bool = True
    path: str


class WorkspaceRenameRequest(BaseModel):
    """重命名请求"""
    old_path: str = Field(..., description="原路径", max_length=500)
    new_path: str = Field(..., description="新路径", max_length=500)


class WorkspaceMoveRequest(BaseModel):
    """移动请求"""
    src_path: str = Field(..., description="源文件路径", max_length=500)
    dest_dir: str = Field(..., description="目标目录", max_length=500)


class WorkspaceMoveResponse(BaseModel):
    """移动响应"""
    success: bool = True
    new_path: str


class WorkspaceSuccessResponse(BaseModel):
    """通用成功响应"""
    success: bool = True


def _get_executor(ctx: OrgCtx) -> "FileExecutor":
    """构建 FileExecutor 实例（复用逻辑提取）"""
    from core.config import get_settings
    from services.file_executor import FileExecutor

    settings = get_settings()
    if not settings.file_workspace_enabled:
        raise AppException(
            code="FILE_WORKSPACE_DISABLED",
            message="文件操作功能未启用",
            status_code=403,
        )
    return FileExecutor(
        workspace_root=settings.file_workspace_root,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
    )


@router.post(
    "/workspace/delete",
    response_model=WorkspaceSuccessResponse,
    summary="删除workspace文件或空目录",
)
async def delete_workspace_item(
    ctx: OrgCtx,
    db: ScopedDB,
    body: WorkspacePathRequest,
):
    """删除文件或空目录。非空目录需先清空内容。"""
    executor = _get_executor(ctx)
    try:
        result = await executor.file_delete(body.path)
        if "不存在" in result or "不为空" in result or "无法删除" in result:
            raise ValidationError(message=result)
        return WorkspaceSuccessResponse()
    except PermissionError as e:
        raise ValidationError(message=str(e))


@router.post(
    "/workspace/mkdir",
    response_model=WorkspaceMkdirResponse,
    summary="新建workspace文件夹",
)
async def mkdir_workspace(
    ctx: OrgCtx,
    db: ScopedDB,
    body: WorkspacePathRequest,
):
    """创建文件夹（含中间路径）。"""
    executor = _get_executor(ctx)
    try:
        result = await executor.file_mkdir(body.path)
        if "已存在" in result and "文件" in result:
            raise AppException(
                code="CONFLICT",
                message=result,
                status_code=409,
            )
        return WorkspaceMkdirResponse(path=body.path)
    except PermissionError as e:
        raise ValidationError(message=str(e))


@router.post(
    "/workspace/rename",
    response_model=WorkspaceSuccessResponse,
    summary="重命名workspace文件或目录",
)
async def rename_workspace_item(
    ctx: OrgCtx,
    db: ScopedDB,
    body: WorkspaceRenameRequest,
):
    """重命名文件或目录（同目录下，跨目录请用 move）。"""
    executor = _get_executor(ctx)
    try:
        result = await executor.file_rename(body.old_path, body.new_path)
        if "不存在" in result:
            raise ValidationError(message=result)
        if "已存在" in result:
            raise AppException(
                code="CONFLICT",
                message=result,
                status_code=409,
            )
        if "不允许跨目录" in result:
            raise ValidationError(message=result)
        return WorkspaceSuccessResponse()
    except PermissionError as e:
        raise ValidationError(message=str(e))


@router.post(
    "/workspace/move",
    response_model=WorkspaceMoveResponse,
    summary="移动workspace文件",
)
async def move_workspace_item(
    ctx: OrgCtx,
    db: ScopedDB,
    body: WorkspaceMoveRequest,
):
    """移动文件到指定目录。"""
    executor = _get_executor(ctx)
    try:
        result = await executor.file_move(body.src_path, body.dest_dir)
        if "不存在" in result:
            raise ValidationError(message=result)
        if "同名文件" in result:
            raise AppException(
                code="CONFLICT",
                message=result,
                status_code=409,
            )
        # 从结果中提取新路径
        new_path = result.split("→")[-1].strip() if "→" in result else f"{body.dest_dir}/{body.src_path.split('/')[-1]}"
        return WorkspaceMoveResponse(new_path=new_path)
    except PermissionError as e:
        raise ValidationError(message=str(e))
