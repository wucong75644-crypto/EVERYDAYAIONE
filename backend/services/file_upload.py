"""
文件上传公共模块

NAS 文件变动时显式同步到 OSS,生成 CDN URL + workspace_path 双轨。
返回 dict 给 emit_payloads 收集器(沙盒 IO 统一协议,不再走 [FILE] marker)。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Optional

from loguru import logger


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
    try:
        from services.oss_service import get_oss_service
        oss = get_oss_service()
        ws_base = Path(settings.file_workspace_root).resolve()
        rel_path = str(file_path.relative_to(ws_base))
        url = await oss.sync_workspace_file(file_path, rel_path)
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
    return payload
