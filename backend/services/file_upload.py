"""
文件上传公共模块

从 sandbox/functions.py 提取的 auto_upload 逻辑，
供 code_execute（沙盒）和文件类工具共用。

NAS 文件变动时显式同步到 OSS，生成 CDN URL。
返回 [FILE] 标签供前端展示。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional

from loguru import logger


async def auto_upload(
    filename: str,
    size: int,
    output_dir: str,
    user_id: str,
    org_id: Optional[str] = None,
) -> str:
    """生成文件的下载 URL 并返回 [FILE] 标签。

    NAS 文件显式同步到 OSS 获取 CDN URL，
    兜底直接拼 CDN URL。

    Args:
        filename: 文件名（不含路径）
        size: 文件大小（字节）
        output_dir: 文件所在目录（绝对路径）
        user_id: 用户 ID（兜底上传用）
        org_id: 企业 ID（兜底上传用）

    Returns:
        包含 [FILE] 标签的字符串
    """
    safe_name = Path(filename).name
    mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"

    from core.config import get_settings
    settings = get_settings()
    file_path = Path(output_dir) / safe_name

    # workspace_path 后缀（供前端代理预览）
    ws_path_suffix = ""
    try:
        from services.file_executor import FileExecutor
        ws_base = Path(settings.file_workspace_root).resolve()
        ws_path = FileExecutor.extract_user_relative_path(
            file_path, ws_base, user_id, org_id,
        )
        ws_path_suffix = f"|{ws_path}"
    except (ValueError, Exception):
        pass

    # 同步到 OSS 获取 CDN URL
    try:
        from services.oss_service import get_oss_service
        oss = get_oss_service()
        ws_base = Path(settings.file_workspace_root).resolve()
        rel_path = str(file_path.relative_to(ws_base))
        url = await oss.sync_workspace_file(file_path, rel_path)
        if url:
            return (
                f"✅ 文件已生成: {safe_name}\n"
                f"[FILE]{url}|{safe_name}|{mime_type}|{size}{ws_path_suffix}[/FILE]"
            )
    except Exception as e:
        logger.warning(f"auto_upload OSS sync failed | file={safe_name} | error={e}")

    # 兜底：OSS 同步失败时拼 CDN URL（NAS 文件已存在）
    if settings.oss_cdn_domain:
        try:
            from urllib.parse import quote
            ws_base = Path(settings.file_workspace_root).resolve()
            object_key = str(file_path.relative_to(ws_base))
            encoded_key = quote(object_key, safe="/")
            url = f"https://{settings.oss_cdn_domain}/workspace/{encoded_key}"
            return (
                f"✅ 文件已生成: {safe_name}\n"
                f"[FILE]{url}|{safe_name}|{mime_type}|{size}{ws_path_suffix}[/FILE]"
            )
        except ValueError:
            pass

    logger.error(f"auto_upload failed | file={safe_name} | no CDN and no OSS sync")
    return f"❌ 文件处理失败: {safe_name}"
