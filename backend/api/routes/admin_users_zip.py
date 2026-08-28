"""admin_users 批量 ZIP 下载子路由

接收 OSS CDN URL 数组，httpx 流式拉取 → zipstream-ng 打包 → StreamingResponse。
单文件 100MB / 总量 1GB / 最多 500 文件，失败项写入 _errors.txt。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, Database
from core.config import settings

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


class DownloadAssetsZipRequest(BaseModel):
    asset_ids: list[UUID] = Field(..., min_length=1, max_length=_ZIP_MAX_FILES)


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


def _allowed_asset_hosts() -> set[str]:
    hosts: set[str] = set()
    for value in (settings.oss_cdn_domain,):
        parsed = urlsplit(str(value or "").strip() if "://" in str(value or "") else f"//{value}")
        if parsed.hostname:
            hosts.add(parsed.hostname.lower().rstrip("."))
    if settings.oss_bucket_name and settings.oss_endpoint:
        endpoint = urlsplit(
            settings.oss_endpoint
            if "://" in settings.oss_endpoint
            else f"//{settings.oss_endpoint}"
        )
        if endpoint.hostname:
            hosts.add(f"{settings.oss_bucket_name}.{endpoint.hostname}".lower().rstrip("."))
    return hosts


def _is_allowed_asset_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.hostname.lower().rstrip(".") in _allowed_asset_hosts()
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except (TypeError, ValueError):
        return False


def _resolve_download_assets(
    db: Database,
    uid: str,
    asset_ids: list[str],
) -> list[dict[str, str]]:
    """通过数据库 RPC 解析资产归属，禁止客户端直接提交任意 URL。"""
    try:
        result = db.rpc(
            "resolve_platform_admin_user_assets_download",
            {
                "p_actor_user_id": uid,
                "p_asset_ids": asset_ids,
            },
        ).execute()
    except Exception as error:
        logger.warning(
            "管理员资产 ZIP 授权失败",
            target_user_id=uid,
            asset_count=len(asset_ids),
            error_type=type(error).__name__,
        )
        raise HTTPException(status_code=500, detail="资产下载授权失败") from error

    payload: Any = result.data
    if not isinstance(payload, list):
        raise HTTPException(status_code=500, detail="资产下载授权结果无效")
    asset_map: dict[str, dict[str, str]] = {}
    try:
        for row in payload:
            if not isinstance(row, dict) or set(row) != {"id", "download_url", "name"}:
                raise ValueError("invalid asset row shape")
            asset_id = str(UUID(str(row["id"])))
            url = row["download_url"]
            name = row["name"]
            if (
                asset_id in asset_map
                or not isinstance(url, str)
                or not url
                or not isinstance(name, str)
                or not name
            ):
                raise ValueError("invalid asset row value")
            asset_map[asset_id] = {
                "id": asset_id,
                "download_url": url,
                "name": name,
            }
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=500, detail="资产下载授权结果无效") from error
    if len(payload) != len(asset_ids) or set(asset_map) != set(asset_ids):
        raise HTTPException(status_code=500, detail="资产下载授权结果无效")
    return [asset_map[asset_id] for asset_id in asset_ids]


def _build_asset_zip(
    assets: list[dict[str, str]],
    fetched: list[tuple[str, Optional[bytes], Optional[str]]],
):
    from zipstream import ZIP_DEFLATED, ZipStream

    stream = ZipStream(compress_type=ZIP_DEFLATED, compress_level=1)
    errors: list[str] = []
    used_names: set[str] = set()
    total_bytes = 0
    added = 0
    for idx, (default_name, content, error) in enumerate(fetched):
        preferred = assets[idx].get("name") or default_name
        if error or content is None:
            errors.append(f"{preferred}: {error or '空内容'}")
            continue
        if total_bytes + len(content) > _ZIP_MAX_TOTAL_BYTES:
            errors.append("总大小超过 1GB，停止打包")
            break
        unique = preferred or f"file_{idx}"
        if unique in used_names:
            base, dot, ext = unique.rpartition(".")
            suffix = 1
            while unique in used_names:
                unique = f"{base}_{suffix}.{ext}" if dot else f"{preferred}_{suffix}"
                suffix += 1
        used_names.add(unique)
        stream.add(content, arcname=unique)
        total_bytes += len(content)
        added += 1
    if errors:
        stream.add("\n".join(errors).encode("utf-8"), arcname="_errors.txt")
    return stream, added, total_bytes, len(errors)


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


@zip_router.post(
    "/users/{uid}/assets/download-zip",
    summary="批量下载用户资产 ZIP（超管）",
)
async def download_user_assets_zip_by_id(
    uid: str,
    body: DownloadAssetsZipRequest,
    user_id: CurrentUserId,
    db: Database,
):
    """资产 ID 经归属复验后下载并打包，保留旧 URL 接口兼容性。"""
    _require_super_admin(user_id, db)
    user_check = db.table("users").select("id").eq("id", uid).maybe_single().execute()
    if not user_check or not user_check.data:
        raise HTTPException(status_code=404, detail="用户不存在")

    asset_ids = [str(asset_id) for asset_id in body.asset_ids]
    if len(set(asset_ids)) != len(asset_ids):
        raise HTTPException(status_code=422, detail="asset_ids 不能重复")
    assets = _resolve_download_assets(db, uid, asset_ids)
    urls = [asset["download_url"] for asset in assets]
    if any(not _is_allowed_asset_url(url) for url in urls):
        raise HTTPException(status_code=422, detail="资产下载地址无效")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
    ) as client:
        fetched = await asyncio.gather(*[_fetch_url(client, url) for url in urls])

    stream, added, total_bytes, error_count = _build_asset_zip(assets, fetched)
    if added == 0 and error_count == 0:
        raise HTTPException(status_code=404, detail="无可下载内容")
    zip_name = f"user-{uid[:8]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    _log_admin_action(
        db,
        admin_id=user_id,
        action_type="download_user_assets",
        description=f"下载用户资产 ZIP ({added}/{len(asset_ids)} 文件)",
        target_user_id=uid,
        target_resource_type="user_assets",
        changes_data={
            "files_count": added,
            "total_bytes": total_bytes,
            "errors_count": error_count,
        },
    )
    ascii_name = _ascii_zip_name(zip_name)
    encoded_name = quote(zip_name)
    return StreamingResponse(
        stream,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{encoded_name}"
            ),
        },
    )
