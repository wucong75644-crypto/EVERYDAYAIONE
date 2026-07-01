"""
文件路由共享模块

提供 workspace 路由共用的 schema、常量和工厂函数。
"""

from typing import TYPE_CHECKING, List, Optional

from pydantic import BaseModel, Field

from api.deps import OrgCtx
from core.exceptions import AppException

if TYPE_CHECKING:
    from services.file_executor import FileExecutor


# ============================================================
# 共享常量（上传白名单 + 大小上限）
# ============================================================

# workspace 允许的文件扩展名（含图片，所有用户附件统一双写工作区）
WORKSPACE_ALLOWED_EXTENSIONS = frozenset({
    "txt", "csv", "json", "yaml", "yml", "xml", "md", "log", "tsv",
    "py", "js", "ts", "html", "css", "sql",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "zip",
    # 图片：不含 svg —— SVG 可嵌入 JS 通过 CDN 上下文执行 XSS
    "png", "jpg", "jpeg", "gif", "webp", "bmp",
})

# workspace 单文件大小上限 (100MB)
WORKSPACE_MAX_FILE_SIZE = 100 * 1024 * 1024


# ============================================================
# 共享 Schema
# ============================================================


class WorkspaceFileItem(BaseModel):
    """workspace 文件列表项"""
    name: str
    is_dir: bool
    size: int = 0
    modified: str = ""
    cdn_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    mime_type: Optional[str] = None
    workspace_path: Optional[str] = None


class WorkspacePathRequest(BaseModel):
    """单路径请求"""
    path: str = Field(..., description="相对路径", max_length=500)


class WorkspaceSuccessResponse(BaseModel):
    """通用成功响应"""
    success: bool = True


# ============================================================
# 工厂：构建 FileExecutor 实例
# ============================================================


def get_executor(ctx: OrgCtx) -> "FileExecutor":
    """构建 FileExecutor 实例（workspace 路由共用）"""
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
