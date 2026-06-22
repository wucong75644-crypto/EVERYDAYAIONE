"""
Office 文档转 PDF 预览路由

支持的输入：pptx / ppt / doc（docx 走前端 mammoth.js，不经此处）
转换工具：LibreOffice headless (`soffice --headless --convert-to pdf`)
缓存策略：OSS 上 `workspace-preview-cache/{md5(workspace_path)}_{mtime}.pdf`
        — mtime 变化即缓存失效；OSS lifecycle 自动清理旧版本

返回：直接 stream PDF（带 inline disposition），让前端 iframe / PDF.js 渲染
"""

import asyncio
import hashlib
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import OrgCtx
from core.exceptions import AppException, ValidationError

from .file_common import get_executor

router = APIRouter()

# 支持的输入扩展名 → LibreOffice 都能处理
SUPPORTED_OFFICE_EXTS = {"pptx", "ppt", "doc"}
# 转换超时（秒）— 防大文件卡死
LIBREOFFICE_TIMEOUT = 60
# OSS 缓存前缀
CACHE_KEY_PREFIX = "workspace-preview-cache"


class PreviewRenderRequest(BaseModel):
    """Office 文档转 PDF 请求"""
    workspace_path: str = Field(..., description="待预览文件的 workspace 相对路径", max_length=500)


def _md5_path(workspace_path: str) -> str:
    """workspace_path → 32 字符 md5"""
    return hashlib.md5(workspace_path.encode("utf-8")).hexdigest()


def _cache_key(workspace_path: str, mtime: int) -> str:
    """OSS 缓存 key：md5(path) + mtime 联合作为版本"""
    return f"{CACHE_KEY_PREFIX}/{_md5_path(workspace_path)}_{mtime}.pdf"


def _convert_to_pdf_sync(src: Path) -> bytes:
    """
    LibreOffice headless 转 PDF（同步阻塞，由 asyncio.to_thread 调用）

    Raises:
        TimeoutError: 转换超时（默认 60s）
        RuntimeError: 转换进程失败
    """
    with tempfile.TemporaryDirectory(prefix="lo-convert-") as outdir:
        # libreoffice --headless --convert-to pdf src --outdir outdir
        try:
            subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "pdf", str(src), "--outdir", outdir],
                check=True,
                capture_output=True,
                timeout=LIBREOFFICE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"转换超时（>{LIBREOFFICE_TIMEOUT}s）")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"libreoffice 转换失败: rc={e.returncode}, stderr={e.stderr[:200] if e.stderr else ''}"
            )

        # 输出文件：outdir/{src 文件名去扩展名}.pdf
        out_pdf = Path(outdir) / f"{src.stem}.pdf"
        if not out_pdf.exists():
            raise RuntimeError("libreoffice 未生成 PDF 输出")
        return out_pdf.read_bytes()


@router.post(
    "/workspace/preview/render",
    summary="把 Office 文档（pptx/ppt/doc）转 PDF 用于浏览器预览",
)
async def render_office_to_pdf(ctx: OrgCtx, body: PreviewRenderRequest):
    """
    转换流程：
    1. 解析 workspace 路径 → NAS 绝对路径（resolve_safe_path 防越权）
    2. 计算缓存 key（md5(path) + mtime）
    3. OSS 命中缓存 → 直接 stream
    4. 未命中 → libreoffice 转换 → 上传 OSS 缓存 → stream
    """
    from services.oss_service import get_oss_service

    executor = get_executor(ctx)
    target = executor.resolve_safe_path(body.workspace_path)
    if not target.exists() or not target.is_file():
        raise AppException(
            code="FILE_NOT_FOUND",
            message=f"文件不存在: {body.workspace_path}",
            status_code=404,
        )

    ext = target.name.rsplit(".", 1)[-1].lower() if "." in target.name else ""
    if ext not in SUPPORTED_OFFICE_EXTS:
        raise ValidationError(
            message=f"不支持的转换类型: .{ext}。仅支持: {sorted(SUPPORTED_OFFICE_EXTS)}",
        )

    mtime = int(target.stat().st_mtime)
    cache_key = _cache_key(body.workspace_path, mtime)
    oss = get_oss_service()

    # 1) OSS 缓存命中（直接调 bucket，OSSService 未暴露 get_object 包装）
    try:
        if await asyncio.to_thread(oss.bucket.object_exists, cache_key):
            obj = await asyncio.to_thread(oss.bucket.get_object, cache_key)
            cached_bytes = await asyncio.to_thread(obj.read)
            logger.info(
                f"Office preview cache HIT | user={ctx.user_id} | path={body.workspace_path} | cache_key={cache_key}"
            )
            return _stream_pdf(cached_bytes, target.name)
    except Exception as e:
        logger.debug(f"OSS cache lookup failed: {e}")

    # 2) 转换（同步阻塞 → 线程池）
    logger.info(
        f"Office preview convert START | user={ctx.user_id} | path={body.workspace_path} | size={target.stat().st_size}"
    )
    try:
        pdf_bytes = await asyncio.to_thread(_convert_to_pdf_sync, target)
    except TimeoutError as e:
        raise AppException(code="CONVERT_TIMEOUT", message=str(e), status_code=504)
    except RuntimeError as e:
        logger.error(f"libreoffice convert failed: {e}")
        raise AppException(
            code="CONVERT_FAILED",
            message="文件转换失败，请下载查看",
            status_code=500,
        )

    # 3) 上传 OSS 缓存（失败不致命，直接调 bucket.put_object）
    try:
        await asyncio.to_thread(
            oss.bucket.put_object,
            cache_key,
            pdf_bytes,
            headers={"Content-Type": "application/pdf"},
        )
        logger.info(
            f"Office preview cache STORE | path={body.workspace_path} | cache_key={cache_key} | size={len(pdf_bytes)}"
        )
    except Exception as e:
        logger.warning(f"OSS cache store failed: {e}")

    return _stream_pdf(pdf_bytes, target.name)


def _stream_pdf(pdf_bytes: bytes, source_name: str) -> StreamingResponse:
    """以 inline disposition stream PDF（浏览器 inline 渲染，不触发下载）"""
    # 用源文件名 + .pdf 后缀，便于用户保存时识别
    pdf_name = source_name.rsplit(".", 1)[0] + ".pdf"

    def iter_bytes():
        yield pdf_bytes

    encoded_name = quote(pdf_name)
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
        "Cache-Control": "private, max-age=300",
    }
    return StreamingResponse(iter_bytes(), media_type="application/pdf", headers=headers)
