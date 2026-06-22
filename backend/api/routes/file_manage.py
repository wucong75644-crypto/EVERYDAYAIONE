"""
工作区文件管理路由

- POST /workspace/delete: 删除文件或空目录
- POST /workspace/mkdir: 新建文件夹
- POST /workspace/rename: 重命名
- POST /workspace/move: 移动文件
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.deps import OrgCtx, ScopedDB
from core.exceptions import AppException, ValidationError

from .file_common import (
    WorkspacePathRequest,
    WorkspaceSuccessResponse,
    get_executor,
)

router = APIRouter()


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
    executor = get_executor(ctx)
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
    executor = get_executor(ctx)
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
    executor = get_executor(ctx)
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
    executor = get_executor(ctx)
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
