"""
文件上传公共模块

NAS 文件变动时显式同步到 OSS,生成 CDN URL + workspace_path 双轨。
返回 dict 给 emit_payloads 收集器(沙盒 IO 统一协议,不再走 [FILE] marker)。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

if TYPE_CHECKING:
    from services.http_downloader import HttpDownloader


# AI 媒体产物落盘默认子目录
_DEFAULT_IMAGE_SUBDIR = "下载/AI图片"
_DEFAULT_VIDEO_SUBDIR = "下载/AI视频"

# 单次下载总耗时上限(秒)。避免 tenacity 3 次重试 × HttpDownloader 60s read 叠加成数分钟。
_DOWNLOAD_TOTAL_BUDGET_SEC = 45

# 多媒体并发落盘上限(防瞬时打 N 倍 NAS/OSS IO)
_PERSIST_MAX_CONCURRENCY = 5

# 同名冲突重试上限(命名带 datetime+hash,理论几乎不触发)
_FILENAME_COLLISION_RETRY = 100

# 允许落盘的 MIME 白名单(防异常 content-type 写入)
_ALLOWED_IMAGE_MIMES: frozenset[str] = frozenset({
    "image/png", "image/jpeg", "image/jpg",
    "image/webp", "image/gif",
})
_ALLOWED_VIDEO_MIMES: frozenset[str] = frozenset({
    "video/mp4", "video/webm", "video/quicktime",
})

# MIME → 扩展名(白名单内的明确映射,避免 mimetypes.guess_extension 平台差异)
_MIME_EXTENSIONS: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}

_OSS_HOSTS_PATTERN = re.compile(r"cdn\.everydayai\.com\.cn|\.aliyuncs\.com")


def build_workspace_thumbnail_url(url: str, width: int = 360) -> str | None:
    """根据 workspace 原图 URL 计算独立缩略图 URL；不使用 OSS query 处理。"""
    if not _OSS_HOSTS_PATTERN.search(url):
        return None
    parsed = urlparse(url)
    if not parsed.path.startswith("/workspace/"):
        return None
    stem = Path(parsed.path[len("/workspace/"):]).with_suffix("").as_posix()
    thumb_path = f"/workspace-thumbnails/{stem}.w{width}.webp"
    return urlunparse(parsed._replace(path=thumb_path, query="", fragment=""))


def _add_media_asset_urls(payload: dict[str, Any], media_type: str) -> dict[str, Any]:
    """补齐媒体资产 URL 语义：旧 url 兼容，原图/预览/下载显式分开。"""
    url = payload.get("url")
    if not isinstance(url, str) or not url:
        return payload

    payload.setdefault("original_url", url)
    payload.setdefault("preview_url", url)
    payload.setdefault("download_url", url)
    return payload


async def upload_to_payload(
    filename: str,
    size: int,
    output_dir: str,
    user_id: str,
    org_id: Optional[str] = None,
) -> dict[str, Any] | None:
    """同步 NAS 文件到 OSS 拿 CDN URL,产出 emit payload 双轨 dict。

    返回 dict 形如:
        {
            "url": "<oss cdn url>",          # 双轨之 CDN URL
            "workspace_path": "下载/x.xlsx", # 双轨之本地相对路径
            "name": "x.xlsx",
            "mime_type": "application/...",
            "size": 12345,
        }
    上传失败返回 None。调用方自己组装最终 emit_payload (加 kind 字段等)。
    """
    safe_name = Path(filename).name
    mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"

    from core.config import get_settings
    settings = get_settings()
    file_path = Path(output_dir) / safe_name

    # workspace_path(供前端代理预览 + AI file_search 引用)
    workspace_path: str | None = None
    try:
        from services.file_executor import FileExecutor
        ws_base = Path(settings.file_workspace_root).resolve()
        workspace_path = FileExecutor.extract_user_relative_path(
            file_path, ws_base, user_id, org_id,
        )
    except (ValueError, Exception):
        pass

    # 同步到 OSS 获取 CDN URL
    url: str | None = None
    thumbnail_url: str | None = None
    try:
        from services.oss_service import get_oss_service
        oss = get_oss_service()
        ws_base = Path(settings.file_workspace_root).resolve()
        rel_path = str(file_path.relative_to(ws_base))
        url = await oss.sync_workspace_file(file_path, rel_path)
        if url and mime_type.startswith("image/"):
            thumbnail_url = await oss.sync_workspace_thumbnail(file_path, rel_path)
    except Exception as e:
        logger.warning(f"upload_to_payload OSS sync failed | file={safe_name} | error={e}")

    # 兜底:OSS 同步失败时拼 CDN URL(NAS 文件已存在)
    if not url and settings.oss_cdn_domain:
        try:
            from urllib.parse import quote
            ws_base = Path(settings.file_workspace_root).resolve()
            object_key = str(file_path.relative_to(ws_base))
            encoded_key = quote(object_key, safe="/")
            url = f"https://{settings.oss_cdn_domain}/workspace/{encoded_key}"
        except ValueError:
            pass

    if not url:
        logger.error(f"upload_to_payload failed | file={safe_name} | no CDN and no OSS sync")
        return None

    payload: dict[str, Any] = {
        "url": url,
        "name": safe_name,
        "mime_type": mime_type,
        "size": size,
    }
    if workspace_path:
        payload["workspace_path"] = workspace_path
    if thumbnail_url:
        payload["thumbnail_url"] = thumbnail_url
    return payload


# ============================================================
# 远程 URL → 工作区落盘(供 AI 媒体产物使用)
# ============================================================


def _compute_user_root(ws_base: Path, user_id: str, org_id: Optional[str]) -> Path:
    """计算用户工作区根目录。与 file_executor.py:86-92 保持一致。"""
    if org_id:
        return ws_base / "org" / org_id / user_id
    if user_id:
        user_hash = hashlib.md5(user_id.encode()).hexdigest()[:8]
        return ws_base / "personal" / user_hash
    return ws_base


def _normalize_mime(content_type: str) -> str:
    """剥离 charset/参数,返回小写 MIME 主体。"""
    return (content_type or "").lower().split(";")[0].strip()


def _generate_media_filename(
    content_type: str,
    idx: int,
    media_type: str = "image",
) -> str:
    """生成行业标准命名 IMG_<YYYYMMDD>_<HHMMSS>_<6hex>_<3idx>.<ext> / VID_..."""
    prefix = "VID" if media_type == "video" else "IMG"
    ext = _MIME_EXTENSIONS.get(
        _normalize_mime(content_type),
        ".mp4" if media_type == "video" else ".png",
    )
    now = datetime.now()
    date_part = now.strftime("%Y%m%d_%H%M%S")
    short_hash = secrets.token_hex(3)  # 6 hex chars
    return f"{prefix}_{date_part}_{short_hash}_{idx:03d}{ext}"


def _resolve_unique_path(target_dir: Path, filename: str) -> Path:
    """同名冲突时附加 _N 后缀(防御性:命名本身含时间戳+hash,极少触发)。"""
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem, suffix = Path(filename).stem, Path(filename).suffix
    for n in range(1, _FILENAME_COLLISION_RETRY):
        alt = target_dir / f"{stem}_{n}{suffix}"
        if not alt.exists():
            return alt
    # 极端兜底:用 secrets 再加 hash
    return target_dir / f"{stem}_{secrets.token_hex(3)}{suffix}"


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPError)),
    stop=stop_after_attempt(3) | stop_after_delay(_DOWNLOAD_TOTAL_BUDGET_SEC),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
async def _download_with_retry(
    downloader: "HttpDownloader",
    url: str,
    user_id: str,
    media_type: str,
    max_size: int,
) -> tuple[bytes, str]:
    """tenacity 3 次重试 + 总预算 45s,避免单 URL 拖死调用方。"""
    return await downloader.download(
        url=url,
        user_id=user_id,
        media_type=media_type,
        max_size=max_size,
    )


async def _write_meta_sidecar(
    file_path: Path,
    source_url: str,
    content_len: int,
    mime_main: str,
    meta: dict[str, Any],
) -> None:
    """写隐藏 .meta.json sidecar。OSError 仅 log,不阻断主流程。"""
    try:
        record: dict[str, Any] = {
            **meta,
            "source_url": source_url,
            "size": content_len,
            "mime_type": mime_main,
            "filename": file_path.name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        meta_path = file_path.with_name("." + file_path.name + ".meta.json")
        payload = json.dumps(record, ensure_ascii=False, indent=2)
        await asyncio.to_thread(meta_path.write_text, payload, encoding="utf-8")
    except OSError as e:
        logger.warning(
            f"download_url_to_workspace meta write failed | "
            f"file={file_path.name} | error={e}"
        )


async def download_url_to_workspace(
    url: str,
    user_id: str,
    org_id: Optional[str] = None,
    *,
    subdir: Optional[str] = None,
    suggested_name: Optional[str] = None,
    media_type: str = "image",
    idx: int = 1,
    max_size_mb: int = 50,
    meta: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """下载远程 URL → 工作区子目录 → 双轨 emit_payload。

    复用 HttpDownloader(流式+超时) + tenacity(重试 3 次/总 45s) + upload_to_payload(OSS+workspace_path)。
    subdir 默认 image→"下载/AI图片"/video→"下载/AI视频"。suggested_name 为 None 则生成
    `IMG_<YYYYMMDD>_<HHMMSS>_<6hex>_<3idx>.<ext>` (VID_ for video)。
    失败返回 None,调用方应降级用原 url(聊天可见、工作区不可见)。
    """
    from core.config import get_settings
    from services.http_downloader import HttpDownloader

    settings = get_settings()
    ws_base = Path(settings.file_workspace_root).resolve()
    user_root = _compute_user_root(ws_base, user_id, org_id)

    if subdir is None:
        subdir = _DEFAULT_VIDEO_SUBDIR if media_type == "video" else _DEFAULT_IMAGE_SUBDIR

    try:
        target_dir = (user_root / subdir).resolve()
        target_dir.relative_to(user_root)
    except ValueError:
        logger.error(f"download_url_to_workspace path escape | subdir={subdir}")
        return None

    downloader = HttpDownloader()
    try:
        # 1. 下载(含重试 + 总超时预算)
        try:
            content, content_type = await _download_with_retry(
                downloader, url, user_id, media_type, max_size_mb * 1024 * 1024,
            )
        except ValueError as e:
            logger.warning(
                f"download_url_to_workspace skipped | url={url[:80]} | reason={e}"
            )
            return None
        except Exception as e:
            logger.warning(
                f"download_url_to_workspace download failed | "
                f"url={url[:80]} | error={e}"
            )
            return None

        # 2. MIME 白名单
        mime_main = _normalize_mime(content_type)
        allowed = _ALLOWED_IMAGE_MIMES if media_type == "image" else _ALLOWED_VIDEO_MIMES
        if mime_main and mime_main not in allowed:
            logger.warning(
                f"download_url_to_workspace mime rejected | "
                f"url={url[:80]} | mime={mime_main}"
            )
            return None

        # 3. 文件名
        filename = (
            Path(suggested_name).name if suggested_name
            else _generate_media_filename(mime_main or "", idx, media_type)
        )

        # 4. 写盘(off-loop,避免阻塞事件循环)
        try:
            await asyncio.to_thread(
                target_dir.mkdir, parents=True, exist_ok=True,
            )
            file_path = _resolve_unique_path(target_dir, filename)
            filename = file_path.name
            await asyncio.to_thread(file_path.write_bytes, content)
        except OSError as e:
            logger.error(
                f"download_url_to_workspace write failed | "
                f"dir={target_dir} | error={e}"
            )
            return None

        # 5. .meta.json sidecar(可选)
        if meta:
            await _write_meta_sidecar(file_path, url, len(content), mime_main, meta)

        # 6. 双轨 dict
        payload = await upload_to_payload(
            filename=filename, size=len(content),
            output_dir=str(target_dir), user_id=user_id, org_id=org_id,
        )
        if not payload:
            logger.warning(
                f"download_url_to_workspace upload_to_payload returned None | "
                f"file={filename}"
            )
            return None

        payload["kind"] = media_type
        _add_media_asset_urls(payload, media_type)
        logger.info(
            f"download_url_to_workspace ok | user={user_id} | "
            f"path={payload.get('workspace_path')} | size={len(content)} | "
            f"mime={mime_main}"
        )
        return payload
    finally:
        await downloader.close()


async def persist_media_urls_to_workspace(
    urls: list[str],
    user_id: str,
    org_id: Optional[str] = None,
    *,
    media_type: str = "image",
    meta: Optional[dict[str, Any]] = None,
    extra_fields: Optional[dict[str, Any]] = None,
    max_concurrency: int = _PERSIST_MAX_CONCURRENCY,
) -> list[dict[str, Any]]:
    """并发下载多张媒体到工作区,组装 emit_payloads 列表(顺序保持)。

    每张独立 try:成功带 workspace_path + extra_fields;失败降级保留原 url + extra_fields。
    Semaphore 限制并发(默认 5),避免多图瞬时打 N 倍 NAS/OSS IO。

    Args:
        urls: 远程媒体 URL 列表
        user_id / org_id: 用户隔离
        media_type: "image" / "video"
        meta: 写入每张图的 sidecar 元数据(自动追加 index/total)
        extra_fields: 注入到每个 payload(供调用方加 width/height/alt 等)
        max_concurrency: 并发上限

    Returns:
        emit_payloads 列表,长度 = len(urls),顺序与输入一致。
    """
    if not urls:
        return []

    extra = extra_fields or {}
    base_meta = meta or {}
    sem = asyncio.Semaphore(max_concurrency)
    total = len(urls)

    async def _one(idx: int, src_url: str) -> dict[str, Any]:
        async with sem:
            payload = await download_url_to_workspace(
                url=src_url,
                user_id=user_id,
                org_id=org_id,
                media_type=media_type,
                idx=idx,
                meta={**base_meta, "index": idx, "total": total},
            )
        if payload:
            return {**payload, **extra}
        return _add_media_asset_urls({"kind": media_type, "url": src_url, **extra}, media_type)

    return await asyncio.gather(
        *(_one(i, u) for i, u in enumerate(urls, start=1))
    )
