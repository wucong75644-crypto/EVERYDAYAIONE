"""
文件上传公共模块

从 sandbox/functions.py 提取的 auto_upload 逻辑，
供 code_execute（沙盒）和 data_query（导出模式）共用。

生成 workspace CDN URL（ossfs 自动同步到 OSS），
或兜底上传到 OSS 获取 URL。返回 [FILE] 标签供前端展示。
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

    优先用 CDN URL（文件通过 ossfs 已在 OSS 上），
    兜底读文件上传 OSS。

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

    # 优先：workspace CDN URL（ossfs 同步，零 IO）
    from core.config import get_settings
    settings = get_settings()

    if settings.oss_cdn_domain:
        ws_base = Path(settings.file_workspace_root).resolve()
        file_path = Path(output_dir) / safe_name
        try:
            from urllib.parse import quote
            object_key = str(file_path.relative_to(ws_base))
            encoded_key = quote(object_key, safe="/")
            url = f"https://{settings.oss_cdn_domain}/workspace/{encoded_key}"
            return (
                f"✅ 文件已生成: {safe_name}\n"
                f"[FILE]{url}|{safe_name}|{mime_type}|{size}[/FILE]"
            )
        except ValueError:
            pass

    # 兜底：无 CDN 配置时读文件上传 OSS（限制 100MB 防 OOM）
    try:
        from services.oss_service import get_oss_service
        file_path = Path(output_dir) / safe_name
        if size > 100 * 1024 * 1024:
            logger.warning(f"auto_upload skip | file={safe_name} size={size} exceeds 100MB limit for OSS upload")
            return f"❌ 文件过大（{size // 1024 // 1024}MB），无法上传。请配置 CDN 域名。"
        content = file_path.read_bytes()
        ext = Path(safe_name).suffix.lstrip(".")
        oss = get_oss_service()
        result = oss.upload_bytes(
            content=content, user_id=user_id, ext=ext,
            category="generated", content_type=mime_type, org_id=org_id,
        )
        return (
            f"✅ 文件已生成: {safe_name}\n"
            f"[FILE]{result['url']}|{safe_name}|{mime_type}|{result['size']}[/FILE]"
        )
    except Exception as e:
        logger.error(f"auto_upload failed | file={safe_name} | error={e}")
        return f"❌ 文件处理失败: {safe_name} ({e})"
