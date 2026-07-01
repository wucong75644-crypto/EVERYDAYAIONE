"""
工作区浏览路由

- GET /workspace/list: 列出用户 workspace 文件
- GET /workspace/search: 递归搜索 workspace 文件（关键词匹配文件名）
- GET /workspace/preview: 预览 workspace 文件（代理，绕过 CDN CORS）
"""

import asyncio
import mimetypes
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from api.deps import OrgCtx, ScopedDB
from core.exceptions import AppException
from services.file_upload import build_workspace_thumbnail_url

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
            thumbnail_url = None
            mime_type = None
            if is_file:
                rel_path = f"{path_prefix}/{item.name}" if path_prefix and path_prefix != "." else item.name
                cdn_url = executor.get_cdn_url(rel_path)
                mime_type = mimetypes.guess_type(item.name)[0]
                if cdn_url and (mime_type or "").startswith("image/"):
                    thumbnail_url = build_workspace_thumbnail_url(cdn_url)

            items.append(WorkspaceFileItem(
                name=item.name,
                is_dir=item.is_dir(),
                size=st.st_size if is_file else 0,
                modified=str(int(st.st_mtime)),
                cdn_url=cdn_url,
                thumbnail_url=thumbnail_url,
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
            cdn_url = cdn_url_fn(rel_path)
            mime_type = mimetypes.guess_type(item.name)[0]
            results.append({
                "name": item.name,
                "size": st.st_size,
                "modified": str(int(st.st_mtime)),
                "cdn_url": cdn_url,
                "thumbnail_url": (
                    build_workspace_thumbnail_url(cdn_url)
                    if cdn_url and (mime_type or "").startswith("image/")
                    else None
                ),
                "mime_type": mime_type,
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
    summary="代理读取 workspace 文件(iframe 预览 / 强制下载二合一)",
)
async def preview_workspace_file(
    ctx: OrgCtx,
    path: Optional[str] = Query(None, description="workspace 内相对路径", max_length=500),
    url: Optional[str] = Query(None, description="OSS CDN URL(workspace/ 前缀);path 二选一", max_length=2048),
    disposition: str = Query("inline", regex="^(inline|attachment)$",
                              description="inline=iframe 渲染(默认); attachment=触发下载"),
):
    """代理读取 workspace 文件。绕过 CDN CORS;支持 iframe 内嵌预览 / 强制下载两种模式。

    输入二选一:
      - path: workspace 相对路径(工作区面板已知)
      - url:  OSS CDN URL(聊天图片只有 url 的场景)
    都经过 resolve_safe_path 验证属于当前用户。

    disposition:
      - inline:    PDF/图片在 iframe / object 内嵌渲染
      - attachment: 浏览器触发下载(用于 PDF 等浏览器默认内嵌渲染的类型)
    """
    if not path and not url:
        raise AppException(
            code="MISSING_PATH",
            message="path 或 url 必须提供一个",
            status_code=400,
        )

    executor = get_executor(ctx)

    # 从 OSS CDN URL 反推 NAS 路径(由 resolve_safe_path 验证归属)
    if url and not path:
        from urllib.parse import unquote, urlparse
        parsed = urlparse(url)
        path_part = unquote(parsed.path.lstrip("/"))
        if not path_part.startswith("workspace/"):
            raise AppException(
                code="INVALID_URL",
                message="仅支持 workspace/ 前缀的 OSS URL",
                status_code=400,
            )
        from core.config import get_settings
        ws_base = Path(get_settings().file_workspace_root).resolve()
        path = str(ws_base / path_part[len("workspace/"):])

    target = executor.resolve_safe_path(path)

    if not target.exists() or not target.is_file():
        raise AppException(
            code="FILE_NOT_FOUND",
            message=f"文件不存在: {path}",
            status_code=404,
        )

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    logger.info(
        f"Workspace preview | user={ctx.user_id} | "
        f"file={target.name} | disposition={disposition}"
    )
    return FileResponse(
        path=str(target),
        media_type=media_type,
        filename=target.name,
        content_disposition_type=disposition,
    )
