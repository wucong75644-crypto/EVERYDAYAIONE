"""admin_users 批量 ZIP 下载子路由

接收 OSS CDN URL 数组，httpx 流式拉取 → zipstream-ng 打包 → StreamingResponse。
单文件 100MB / 总量 1GB / 最多 500 文件，失败项写入 _errors.txt。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, Database

from .admin_users_helpers import (
    _ascii_zip_name,
    _filename_from_url,
    _log_admin_action,
    _require_super_admin,
)


zip_router = APIRouter()


_ZIP_MAX_FILES = 500
_ZIP_MAX_TOTAL_BYTES = 1 * 1024 ** 3   # 1 GB
_ZIP_PER_FILE_MAX = 100 * 1024 ** 2    # 100 MB


class DownloadZipRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=_ZIP_MAX_FILES)
    filenames: Optional[list[str]] = Field(None, description="可选，与 urls 同长，自定义 ZIP 内文件名")
    zip_name: Optional[str] = Field(None, max_length=120)


async def _fetch_url(client: httpx.AsyncClient, url: str) -> tuple[str, Optional[bytes], Optional[str]]:
    """返回 (suggested_name, content, error)"""
    name = _filename_from_url(url)
    try:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return name, None, f"HTTP {resp.status_code}"
        content = resp.content
        if len(content) > _ZIP_PER_FILE_MAX:
            return name, None, f"单文件超过 {_ZIP_PER_FILE_MAX // (1024**2)}MB"
        return name, content, None
    except httpx.TimeoutException:
        return name, None, "下载超时"
    except Exception as e:
        return name, None, str(e)[:120]


@zip_router.post("/users/{uid}/download_zip", summary="批量下载用户资产 ZIP（超管）")
async def download_user_assets_zip(
    uid: str,
    body: DownloadZipRequest,
    user_id: CurrentUserId,
    db: Database,
):
    """OSS CDN URL 数组 → 流式 ZIP"""
    from zipstream import ZIP_DEFLATED, ZipStream

    _require_super_admin(user_id, db)

    user_check = db.table("users").select("id").eq("id", uid).maybe_single().execute()
    if not user_check or not user_check.data:
        raise HTTPException(status_code=404, detail="用户不存在")

    urls = body.urls
    custom_names = body.filenames or []
    if custom_names and len(custom_names) != len(urls):
        raise HTTPException(status_code=400, detail="filenames 长度必须与 urls 一致")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
    ) as client:
        fetched = await asyncio.gather(*[_fetch_url(client, u) for u in urls])

    zs = ZipStream(compress_type=ZIP_DEFLATED, compress_level=1)
    errors: list[str] = []
    used_names: set[str] = set()
    total_bytes = 0
    added = 0

    for idx, (default_name, content, err) in enumerate(fetched):
        display_url = urls[idx]
        preferred = (custom_names[idx] if custom_names else None) or default_name
        if err or content is None:
            errors.append(f"{preferred} ({display_url}): {err or '空内容'}")
            continue

        if total_bytes + len(content) > _ZIP_MAX_TOTAL_BYTES:
            errors.append(f"{preferred}: 总大小超过 {_ZIP_MAX_TOTAL_BYTES // (1024**3)}GB，停止打包")
            break

        unique = preferred or f"file_{idx}"
        if unique in used_names:
            base, dot, ext = unique.rpartition(".")
            n = 1
            while unique in used_names:
                unique = (f"{base}_{n}.{ext}" if dot else f"{preferred}_{n}")
                n += 1
        used_names.add(unique)

        zs.add(content, arcname=unique)
        total_bytes += len(content)
        added += 1

    if errors:
        zs.add(("\n".join(errors)).encode("utf-8"), arcname="_errors.txt")

    if added == 0 and not errors:
        raise HTTPException(status_code=404, detail="无可下载内容")

    zip_name = body.zip_name or f"user-{uid[:8]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    if not zip_name.lower().endswith(".zip"):
        zip_name = f"{zip_name}.zip"

    logger.info(
        f"Admin ZIP | operator={user_id} | target_user={uid} | "
        f"files={added} | bytes={total_bytes} | errors={len(errors)}"
    )
    _log_admin_action(
        db,
        admin_id=user_id,
        action_type="download_user_assets",
        description=f"下载用户资产 ZIP ({added}/{len(urls)} 文件)",
        target_user_id=uid,
        target_resource_type="user_assets",
        changes_data={"files_count": added, "total_bytes": total_bytes, "errors_count": len(errors)},
    )

    ascii_name = _ascii_zip_name(zip_name)
    encoded_name = quote(zip_name)
    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}',
    }
    return StreamingResponse(zs, media_type="application/zip", headers=headers)
