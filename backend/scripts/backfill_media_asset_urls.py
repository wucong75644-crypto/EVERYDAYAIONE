"""Backfill image original_url and thumbnail_url in historical JSON payloads.

Usage:
    python backend/scripts/backfill_media_asset_urls.py --dry-run
    python backend/scripts/backfill_media_asset_urls.py --apply
    python backend/scripts/backfill_media_asset_urls.py --apply --limit 100
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg.types.json import Jsonb

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.file_upload import build_workspace_thumbnail_url  # noqa: E402

PROCESS_KEY = "x-oss-process"
JSON_COLUMNS = {
    "messages": ("content",),
    "tasks": ("result_data", "accumulated_blocks", "result"),
}


@dataclass
class BackfillStats:
    scanned_rows: int = 0
    updated_rows: int = 0
    scanned_images: int = 0
    original_added: int = 0
    original_normalized: int = 0
    thumbnail_added: int = 0
    thumbnail_synced: int = 0


def load_env() -> None:
    """Load backend/.env first, then root .env, without overriding existing env."""
    env_paths = (
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
        BACKEND_DIR / ".env",
        PROJECT_DIR / ".env",
    )
    seen: set[Path] = set()
    for env_path in env_paths:
        env_path = env_path.resolve()
        if env_path in seen:
            continue
        seen.add(env_path)
        if not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def strip_oss_process(url: str) -> str:
    """Remove x-oss-process from an OSS/CDN URL while preserving other params."""
    parts = urlsplit(url)
    if not parts.query or PROCESS_KEY not in parts.query:
        return url
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != PROCESS_KEY]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def is_image_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("type") == "image" or value.get("kind") == "image":
        return True
    if "original_url" in value or "thumbnail_url" in value:
        return True
    mime = value.get("mime_type") or value.get("mimeType") or ""
    return bool((value.get("url") or value.get("image_url")) and str(mime).startswith("image/"))


def iter_image_payloads(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if is_image_payload(value):
            yield value
        for child in value.values():
            yield from iter_image_payloads(child)
    elif isinstance(value, list):
        for item in value:
            yield from iter_image_payloads(item)


def decode_json(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def encode_json_like(original: Any, value: Any) -> Any:
    if isinstance(original, str):
        return json.dumps(value, ensure_ascii=False)
    return value


def backfill_value(
    value: Any,
    stats: BackfillStats,
    thumbnail_resolver: Callable[[str], str | None] | None = None,
) -> tuple[Any, bool]:
    decoded = decode_json(value)
    if not isinstance(decoded, (dict, list)):
        return value, False

    changed = False
    for image in iter_image_payloads(decoded):
        stats.scanned_images += 1
        url = image.get("url") or image.get("image_url")
        original = image.get("original_url")
        if not isinstance(url, str) and not isinstance(original, str):
            continue

        base_original = strip_oss_process(original or url)
        if not original:
            image["original_url"] = base_original
            stats.original_added += 1
            changed = True
        elif original != base_original:
            image["original_url"] = base_original
            stats.original_normalized += 1
            changed = True

        thumbnail_url = image.get("thumbnail_url")
        next_thumbnail = (
            thumbnail_resolver(base_original)
            if thumbnail_resolver
            else build_workspace_thumbnail_url(base_original)
        )
        if next_thumbnail and (
            not isinstance(thumbnail_url, str)
            or PROCESS_KEY in thumbnail_url
        ):
            image["thumbnail_url"] = next_thumbnail
            stats.thumbnail_added += 1
            changed = True

    return encode_json_like(value, decoded), changed


def fetch_rows(conn: psycopg.Connection, table: str, column: str, limit: int | None) -> list[tuple[Any, Any]]:
    sql = f"""
        SELECT id, {column}
        FROM {table}
        WHERE {column} IS NOT NULL
          AND {column}::text ILIKE %s
        ORDER BY id
    """
    params: tuple[Any, ...] = ("%image%",)
    if limit:
        sql += " LIMIT %s"
        params = (*params, limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def process_column(
    conn: psycopg.Connection,
    table: str,
    column: str,
    *,
    apply: bool,
    limit: int | None,
    thumbnail_resolver: Callable[[str], str | None] | None = None,
) -> BackfillStats:
    stats = BackfillStats()
    rows = fetch_rows(conn, table, column, limit)
    with conn.cursor() as cur:
        for row_id, value in rows:
            stats.scanned_rows += 1
            new_value, changed = backfill_value(value, stats, thumbnail_resolver)
            if not changed:
                continue
            stats.updated_rows += 1
            if apply:
                db_value = Jsonb(new_value) if isinstance(new_value, (dict, list)) else new_value
                cur.execute(
                    f"UPDATE {table} SET {column} = %s WHERE id = %s",
                    (db_value, row_id),
                )
    return stats


def build_thumbnail_resolver(stats: BackfillStats) -> Callable[[str], str | None]:
    """构造生产回填用缩略图同步器：从 workspace URL 找 NAS 原图并上传独立缩略图。"""
    from core.config import get_settings
    from services.oss_service import get_oss_service

    settings = get_settings()
    ws_base = Path(settings.file_workspace_root).resolve()
    oss = get_oss_service()
    cache: dict[str, str | None] = {}

    def _resolve(original_url: str) -> str | None:
        if original_url in cache:
            return cache[original_url]
        parsed = urlsplit(original_url)
        if not parsed.path.startswith("/workspace/"):
            cache[original_url] = build_workspace_thumbnail_url(original_url)
            return cache[original_url]
        rel_path = unquote(parsed.path[len("/workspace/"):])
        local_path = (ws_base / rel_path).resolve()
        try:
            local_path.relative_to(ws_base)
        except ValueError:
            cache[original_url] = None
            return None
        if not local_path.exists() or not local_path.is_file():
            cache[original_url] = None
            return None
        thumb_url = asyncio.run(oss.sync_workspace_thumbnail(local_path, rel_path))
        if thumb_url:
            stats.thumbnail_synced += 1
        cache[original_url] = thumb_url
        return thumb_url

    return _resolve


def merge_stats(total: BackfillStats, current: BackfillStats) -> None:
    for field in total.__dataclass_fields__:
        setattr(total, field, getattr(total, field) + getattr(current, field))


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--sync-thumbnails",
        action="store_true",
        help="从 NAS 原图生成并上传 workspace-thumbnails 独立缩略图对象",
    )
    args = parser.parse_args()

    load_env()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not configured")

    grand_total = BackfillStats()
    thumbnail_resolver = (
        build_thumbnail_resolver(grand_total)
        if args.apply and args.sync_thumbnails
        else None
    )
    with psycopg.connect(database_url) as conn:
        for table, columns in JSON_COLUMNS.items():
            for column in columns:
                stats = process_column(
                    conn, table, column,
                    apply=args.apply,
                    limit=args.limit,
                    thumbnail_resolver=thumbnail_resolver,
                )
                merge_stats(grand_total, stats)
                print(f"{table}.{column}: {stats}")
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    print(f"TOTAL: {grand_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
