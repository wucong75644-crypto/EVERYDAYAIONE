"""
工作区批量下载路由

- POST /workspace/download_zip: 流式 ZIP 打包多文件/文件夹
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import OrgCtx
from core.exceptions import AppException

from .file_common import get_executor

router = APIRouter()


_ZIP_MAX_FILES = 500
_ZIP_MAX_TOTAL_BYTES = 2 * 1024 ** 3  # 2 GB


class WorkspaceDownloadZipRequest(BaseModel):
    """批量下载请求"""
    paths: List[str] = Field(..., min_length=1, max_length=_ZIP_MAX_FILES,
                             description="文件或文件夹相对路径列表")


def _collect_zip_targets(
    executor,
    paths: List[str],
) -> tuple[list[tuple[Path, str]], list[str]]:
    """
    收集 ZIP 打包目标：解析 + 校验 + 递归展开 + 统计大小。

    返回 (targets, errors)：
      - targets: [(绝对路径, ZIP 内 arcname)]
      - errors:  [跳过原因] —— 不存在、越权、IO 异常
    """
    targets: list[tuple[Path, str]] = []
    errors: list[str] = []
    total = 0

    for raw in paths:
        try:
            abs_path = executor.resolve_safe_path(raw)
        except (PermissionError, ValueError) as exc:
            errors.append(f"{raw}: 路径越权或不合法 ({exc})")
            continue

        if not abs_path.exists():
            errors.append(f"{raw}: 文件不存在")
            continue

        # 文件：直接加入，arcname 用文件名（避免 ZIP 内出现 NAS 路径前缀）
        if abs_path.is_file():
            targets.append((abs_path, abs_path.name))
            total += abs_path.stat().st_size
            if len(targets) > _ZIP_MAX_FILES:
                raise ValueError("TOO_MANY_FILES")
            if total > _ZIP_MAX_TOTAL_BYTES:
                raise ValueError("TOO_LARGE")
            continue

        # 目录：递归收集所有文件，保留目录结构
        if abs_path.is_dir():
            base = abs_path.name
            for sub in abs_path.rglob("*"):
                if not sub.is_file():
                    continue
                # 与 listdir/search 行为对齐:跳过隐藏文件(如 .meta.json sidecar)
                if sub.name.startswith("."):
                    continue
                try:
                    rel = sub.relative_to(abs_path)
                    arc = f"{base}/{rel.as_posix()}"
                    targets.append((sub, arc))
                    total += sub.stat().st_size
                    if len(targets) > _ZIP_MAX_FILES:
                        raise ValueError("TOO_MANY_FILES")
                    if total > _ZIP_MAX_TOTAL_BYTES:
                        raise ValueError("TOO_LARGE")
                except OSError as e:
                    errors.append(f"{sub}: {e}")

    return targets, errors


def _resolve_archive_name(paths: List[str]) -> str:
    """决定 ZIP 文件名：单文件夹 → 文件夹名.zip / 其他 → workspace-{ts}.zip"""
    if len(paths) == 1:
        first = paths[0].rstrip("/").split("/")[-1]
        if first:
            return f"{first}.zip"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"workspace-{ts}.zip"


def _ascii_fallback(name: str) -> str:
    """
    RFC 6266 兼容的 ASCII filename fallback。

    HTTP header 必须 latin-1 编码，含中文/控制字符的 archive_name 直接塞
    `filename="..."` 会让 uvicorn ASGI 层抛 UnicodeEncodeError → 500。
    非 ASCII 字符走 `filename*=UTF-8''xxx`（quote 编码），filename= 部分
    则取此函数返回的 ASCII 安全版本。

    同时清理 " \\ CR LF —— 防 header 注入。
    """
    safe = re.sub(r"[^\x20-\x7e]", "_", name)
    safe = safe.replace('"', "_").replace("\\", "_")
    if not safe.lower().endswith(".zip"):
        return "download.zip"
    return safe


@router.post(
    "/workspace/download_zip",
    summary="批量下载workspace文件为ZIP",
)
async def download_workspace_zip(
    ctx: OrgCtx,
    body: WorkspaceDownloadZipRequest,
):
    """打包多个文件/文件夹为 ZIP，流式返回。

    - ZIP 内文件名使用 UTF-8（zipstream-ng 默认）
    - 路径全部经 executor.resolve_safe_path 校验
    - 上限：500 文件 / 2GB；超出返回 413
    - 不存在 / 越权的条目写入 _errors.txt 入 ZIP 末尾，不阻塞下载
    """
    from zipstream import ZIP_DEFLATED, ZipStream

    executor = get_executor(ctx)

    # 收集 + 校验（同步阻塞 IO 走线程池）
    try:
        targets, errors = await asyncio.to_thread(_collect_zip_targets, executor, body.paths)
    except ValueError as e:
        if str(e) == "TOO_MANY_FILES":
            raise AppException(code="TOO_MANY_FILES",
                               message=f"文件数超过 {_ZIP_MAX_FILES} 个，请分批下载",
                               status_code=413)
        if str(e) == "TOO_LARGE":
            raise AppException(code="TOO_LARGE",
                               message="总大小超过 2GB，请分批下载",
                               status_code=413)
        raise

    if not targets:
        raise AppException(
            code="FILE_NOT_FOUND",
            message="所选文件均不存在或无法访问",
            status_code=404,
        )

    # 构建流式 ZIP（compress_level=1 取速度优先；图片/视频本身已压缩）
    zs = ZipStream(compress_type=ZIP_DEFLATED, compress_level=1)
    for abs_path, arcname in targets:
        try:
            zs.add_path(str(abs_path), arcname=arcname)
        except OSError as e:
            errors.append(f"{arcname}: {e}")

    # 错误清单作为 _errors.txt 一并打包（仅有错误时）
    if errors:
        zs.add(("\n".join(errors)).encode("utf-8"), arcname="_errors.txt")

    archive_name = _resolve_archive_name(body.paths)
    total_size = sum(p.stat().st_size for p, _ in targets if p.exists())
    logger.info(
        f"Workspace ZIP | user={ctx.user_id} | files={len(targets)} | "
        f"size={total_size} | errors={len(errors)} | name={archive_name}"
    )

    # RFC 6266 双轨命名：filename= 用 ASCII fallback（latin-1 兼容 + 防注入），
    # filename*= 用 RFC 5987 UTF-8 percent-encoding 承载真实中文名
    ascii_name = _ascii_fallback(archive_name)
    encoded_name = quote(archive_name)
    headers = {
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}",
    }
    return StreamingResponse(zs, media_type="application/zip", headers=headers)
