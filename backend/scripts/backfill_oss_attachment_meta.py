"""
回填 OSS 旧文件的 Content-Disposition: attachment ObjectMeta。

背景:
  oss_service.sync_workspace_file / _validate_and_upload 自某次提交后,会在上传
  时为 image/video/audio MIME 设置 Content-Disposition: attachment header。
  这样 CDN 缓存的响应也带 attachment,浏览器 a.click 必下载。

  但在那次提交之前上传的旧文件 ObjectMeta 不含 attachment,前端点下载会被
  浏览器渲染而非下载。本脚本用 OSS CopyObject API 原地更新 Meta(同 bucket
  + 同 key + replace metadata 模式),无需重新上传文件内容。

执行:
  cd backend && source venv/bin/activate
  python scripts/backfill_oss_attachment_meta.py --dry-run   # 仅扫描,不修改
  python scripts/backfill_oss_attachment_meta.py --execute    # 实际执行
  python scripts/backfill_oss_attachment_meta.py --execute --prefix workspace/  # 仅 workspace 目录

按 prefix 限定范围(workspace / images / videos),CopyObject 同源同 target 即
in-place 修改 Meta(阿里云 OSS 标准用法)。每个 object 一次 API 调用。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote

# 让 scripts 可直接 import services
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import oss2  # noqa: E402
from loguru import logger  # noqa: E402

from core.config import settings  # noqa: E402


# 跟 oss_service._INLINE_RENDERED_MIME_PREFIXES 保持一致
_INLINE_PREFIXES = ("image/", "video/", "audio/")


def _guess_content_type(key: str) -> str:
    """根据扩展名推断 Content-Type(与 oss_service._guess_content_type 一致)。"""
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    table = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
        "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
        "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
        "pdf": "application/pdf",
    }
    return table.get(ext, "application/octet-stream")


def _backfill_one(bucket: oss2.Bucket, key: str, dry_run: bool) -> bool:
    """对单个 object 检查并(如需)更新 Meta。返回是否需要更新。"""
    try:
        head = bucket.head_object(key)
    except oss2.exceptions.NoSuchKey:
        logger.warning(f"SKIP missing | key={key}")
        return False
    except oss2.exceptions.OssError as e:
        logger.warning(f"SKIP head error | key={key} | {e}")
        return False

    content_type = head.headers.get("content-type") or _guess_content_type(key)
    if not content_type.startswith(_INLINE_PREFIXES):
        # PDF / Office 类不动(它们需要 iframe 预览)
        return False

    cd_existing = head.headers.get("content-disposition") or ""
    if cd_existing.startswith("attachment"):
        # 已有 attachment,跳过
        return False

    filename = key.rsplit("/", 1)[-1]
    encoded = quote(filename)
    new_cd = f"attachment; filename*=UTF-8''{encoded}"

    if dry_run:
        logger.info(f"DRY-RUN would update | key={key} | new_cd={new_cd}")
        return True

    try:
        # CopyObject 同源 + 新 headers + REPLACE 模式 → in-place 改 Meta
        bucket.copy_object(
            source_bucket_name=bucket.bucket_name,
            source_key=key,
            target_key=key,
            headers={
                "Content-Type": content_type,
                "Content-Disposition": new_cd,
                "x-oss-metadata-directive": "REPLACE",
            },
        )
        logger.info(f"UPDATED | key={key}")
        return True
    except oss2.exceptions.OssError as e:
        logger.error(f"FAIL update | key={key} | {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只扫描不修改")
    parser.add_argument("--execute", action="store_true", help="实际执行(显式开关,防误操作)")
    parser.add_argument("--prefix", default="", help="object key 前缀过滤 (空=全 bucket)")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个(0=不限)")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("必须指定 --dry-run 或 --execute")

    auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket_name)

    logger.info(
        f"Backfill start | bucket={settings.oss_bucket_name} | "
        f"prefix={args.prefix or '(all)'} | dry_run={args.dry_run}"
    )

    scanned = 0
    updated = 0
    for obj in oss2.ObjectIteratorV2(bucket, prefix=args.prefix):
        scanned += 1
        if _backfill_one(bucket, obj.key, dry_run=args.dry_run):
            updated += 1
        if args.limit and scanned >= args.limit:
            logger.info(f"Reached --limit {args.limit}, stop")
            break

    logger.info(
        f"Backfill done | scanned={scanned} | updated={updated} | "
        f"dry_run={args.dry_run}"
    )


if __name__ == "__main__":
    main()
