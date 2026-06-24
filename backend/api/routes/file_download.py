"""
工作区下载路由

- POST /workspace/download_zip: 统一下载端点
  - 单文件 path → 直接流式返回原文件 + attachment(不打 ZIP)
  - 文件夹 / 多文件 → 流式 ZIP 打包
"""

import asyncio
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.parse import quote, unquote, urlparse

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import OrgCtx
from core.exceptions import AppException

from .file_common import get_executor

router = APIRouter()


def _normalize_input_path(p: str) -> str:
    """支持两种输入:
    - 相对路径(如 `下载/AI图片/x.png`) → 原样返回,由 resolve_safe_path 验证
    - OSS CDN URL(含 `/workspace/<object_key>`) → 转成 NAS 绝对路径

    返回的路径会被 executor.resolve_safe_path 二次校验属于当前用户。
    """
    if not p.startswith(("http://", "https://")):
        return p
    try:
        parsed = urlparse(p)
        path_part = unquote(parsed.path.lstrip("/"))
        if not path_part.startswith("workspace/"):
            return p  # 非工作区 OSS URL,原样返回(后续 resolve_safe_path 会报错)
        rel_to_ws = path_part.removeprefix("workspace/")
        from core.config import get_settings
        ws_base = Path(get_settings().file_workspace_root).resolve()
        # 返回 NAS 绝对路径(由 resolve_safe_path 验证用户归属)
        return str(ws_base / rel_to_ws)
    except (ValueError, Exception):
        return p


_ZIP_MAX_FILES = 500
_ZIP_MAX_TOTAL_BYTES = 2 * 1024 ** 3  # 2 GB
_SINGLE_FILE_CHUNK = 64 * 1024


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


def _is_single_file_request(paths: List[str], executor) -> Path | None:
    """判断是否单文件场景:paths 仅 1 个,且解析后是文件(非目录)。

    返回该文件绝对路径,否则 None(走 ZIP)。
    """
    if len(paths) != 1:
        return None
    try:
        abs_path = executor.resolve_safe_path(paths[0])
    except (PermissionError, ValueError):
        return None
    if abs_path.exists() and abs_path.is_file():
        return abs_path
    return None


def _build_content_disposition(filename: str) -> str:
    """RFC 6266 双轨命名:ASCII fallback + UTF-8 percent-encoding(支持中文)。"""
    ascii_name = re.sub(r"[^\x20-\x7e]", "_", filename).replace('"', "_").replace("\\", "_") or "download"
    encoded_name = quote(filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"


async def _stream_single_file(abs_path: Path):
    """单文件分块流式读取(同步 IO 走线程池)。"""
    def _read_chunks():
        with open(abs_path, "rb") as f:
            while True:
                chunk = f.read(_SINGLE_FILE_CHUNK)
                if not chunk:
                    break
                yield chunk
    gen = _read_chunks()
    while True:
        chunk = await asyncio.to_thread(lambda: next(gen, None))
        if chunk is None:
            break
        yield chunk


@router.post(
    "/workspace/download_zip",
    summary="工作区文件统一下载入口(单文件直送 / 多文件 ZIP 自动判断)",
)
async def download_workspace_zip(
    ctx: OrgCtx,
    body: WorkspaceDownloadZipRequest,
):
    """工作区文件下载统一入口,自动判断输出形态。

    - paths 长度=1 且是文件 → 直接流式返回原文件 + attachment(不打 ZIP)
    - paths 长度=1 是文件夹 / paths 长度>1 → 流式 ZIP 打包

    保留端点名 download_zip 向后兼容,语义增强为"通用下载"。
    """
    from zipstream import ZIP_DEFLATED, ZipStream

    executor = get_executor(ctx)

    # 同时支持 workspace 相对路径 和 OSS CDN URL(workspace 图片下载场景)
    # URL → NAS 绝对路径; 用户归属由 resolve_safe_path 校验
    normalized_paths = [_normalize_input_path(p) for p in body.paths]

    # 单文件场景:直接流式返回(绕开 ZIP 打包开销,Content-Type=原文件 mime + attachment)
    single_file = await asyncio.to_thread(_is_single_file_request, normalized_paths, executor)
    if single_file is not None:
        size = single_file.stat().st_size
        mime_type = mimetypes.guess_type(single_file.name)[0] or "application/octet-stream"
        logger.info(
            f"Workspace single download | user={ctx.user_id} | "
            f"file={single_file.name} | size={size} | mime={mime_type}"
        )
        return StreamingResponse(
            _stream_single_file(single_file),
            media_type=mime_type,
            headers={
                "Content-Disposition": _build_content_disposition(single_file.name),
                "Content-Length": str(size),
            },
        )

    # 收集 + 校验（同步阻塞 IO 走线程池）
    try:
        targets, errors = await asyncio.to_thread(_collect_zip_targets, executor, normalized_paths)
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

    return StreamingResponse(
        zs,
        media_type="application/zip",
        headers={"Content-Disposition": _build_content_disposition(archive_name)},
    )
