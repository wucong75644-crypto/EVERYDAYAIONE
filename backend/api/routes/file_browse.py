"""
工作区浏览路由

- GET /workspace/list: 列出用户 workspace 文件
- GET /workspace/search: 递归搜索 workspace 文件（关键词匹配文件名）
- GET /workspace/preview: 预览 workspace 文件（代理，绕过 CDN CORS）
"""

import asyncio
import mimetypes
from pathlib import Path
from typing import List

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from api.deps import OrgCtx, ScopedDB
from core.exceptions import AppException

from .file_common import WorkspaceFileItem, get_executor

router = APIRouter()


class WorkspaceListResponse(BaseModel):
    """workspace 文件列表响应"""
    path: str
    items: List[WorkspaceFileItem]
    total: int


class WorkspaceSearchResponse(BaseModel):
    """workspace 文件搜索响应"""
    items: List[WorkspaceFileItem]
    total: int


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


def _search_files_sync(
    root: Path,
    keyword: str,
    limit: int,
    cdn_url_fn,
) -> list[dict]:
    """同步递归搜索文件（由 asyncio.to_thread 调用，不阻塞事件循环）"""
    results: list[dict] = []
    for item in root.rglob("*"):
        if len(results) >= limit:
            break
        if item.is_dir():
            continue
        # 跳过隐藏文件和 staging 目录
        parts = item.relative_to(root).parts
        if any(p.startswith(".") or p == "staging" for p in parts):
            continue
        if keyword not in item.name.lower():
            continue
        try:
            st = item.stat()
            rel_path = str(item.relative_to(root))
            results.append({
                "name": item.name,
                "size": st.st_size,
                "modified": str(int(st.st_mtime)),
                "cdn_url": cdn_url_fn(rel_path),
                "mime_type": mimetypes.guess_type(item.name)[0],
                "workspace_path": rel_path,
            })
        except (PermissionError, OSError):
            continue
    return results


@router.get(
    "/workspace/search",
    response_model=WorkspaceSearchResponse,
    summary="搜索workspace文件",
)
async def search_workspace(
    ctx: OrgCtx,
    q: str = "",
    limit: int = Query(default=20, ge=1, le=100),
):
    """递归搜索用户 workspace 目录，按文件名关键词匹配。
    空关键词时返回最近修改的文件列表。"""
    executor = get_executor(ctx)
    root = executor.resolve_safe_path(".")
    if not root.exists() or not root.is_dir():
        return WorkspaceSearchResponse(items=[], total=0)

    keyword = q.strip().lower()  # 空字符串 → 匹配所有文件

    # 文件系统遍历是同步阻塞操作，放到线程池执行
    raw = await asyncio.to_thread(
        _search_files_sync, root, keyword, limit, executor.get_cdn_url,
    )
    # 空关键词：按修改时间倒序（最近文件优先）
    if not keyword:
        raw.sort(key=lambda x: x["modified"], reverse=True)

    items = [
        WorkspaceFileItem(is_dir=False, **entry) for entry in raw
    ]
    return WorkspaceSearchResponse(items=items, total=len(items))


@router.get(
    "/workspace/preview",
    summary="预览workspace文件（代理，绕过CDN CORS）",
)
async def preview_workspace_file(
    ctx: OrgCtx,
    path: str = Query(..., description="workspace 内相对路径", max_length=500),
):
    """读取 workspace 文件并返回，供前端预览使用。绕过 CDN 的 CORS 限制。"""
    executor = get_executor(ctx)
    target = executor.resolve_safe_path(path)

    if not target.exists() or not target.is_file():
        raise AppException(
            code="FILE_NOT_FOUND",
            message=f"文件不存在: {path}",
            status_code=404,
        )

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    logger.info(f"Workspace preview | user={ctx.user_id} | path={path}")
    # content_disposition_type='inline' — 让浏览器 inline 渲染 PDF/图片等，
    # 而非触发下载。starlette FileResponse 传 filename 时默认 attachment，
    # 这会让 iframe PDF 黑屏 + 自动下载（fallback 场景的 latent bug）
    return FileResponse(
        path=str(target),
        media_type=media_type,
        filename=target.name,
        content_disposition_type="inline",
    )
