"""历史用户资产回填与对账。

默认 dry-run；只有显式 --apply 才写入数据库并保存各来源 checkpoint。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Iterable
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.workspace import build_wecom_channel_workspace_owner
from services.assets import (
    AssetIdentityError,
    AssetRefDraft,
    AssetRegistryService,
    ReadyAssetDraft,
    resolve_asset_identity,
)
from scripts.backfill_user_assets_sql import RPC_SQL, SOURCE_QUERIES

DEFAULT_CHECKPOINT = BACKEND / "tmp" / "user_asset_backfill.json"
SOURCE_ORDER = (
    "image_generations",
    "tasks",
    "assistant_messages",
    "user_messages",
    "attachments",
)

@dataclass
class BackfillStats:
    source_rows: int = 0
    projected_refs: int = 0
    unique_assets: int = 0
    assets_created: int = 0
    assets_reused: int = 0
    refs_created: int = 0
    refs_reused: int = 0
    skipped: int = 0
    conflicts: int = 0
    failures: int = 0
    orphan_assets: int = 0
    normalized_workspace_paths: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)
    skipped_reasons: dict[str, int] = field(default_factory=dict)


class PsycopgRpcClient:
    """让既有 AssetRegistryService 复用当前 psycopg 事务。"""

    def __init__(self, conn: psycopg.Connection[Any]):
        self.conn = conn
        self.params: dict[str, Any] | None = None

    def rpc(self, name: str, params: dict[str, Any]) -> "PsycopgRpcClient":
        if name != "register_user_asset":
            raise ValueError("unsupported RPC")
        self.params = params
        return self

    def execute(self) -> SimpleNamespace:
        if self.params is None:
            raise RuntimeError("RPC params missing")
        params = dict(self.params)
        params["p_asset_metadata"] = Jsonb(params["p_asset_metadata"])
        params["p_ref_metadata"] = Jsonb(params["p_ref_metadata"])
        with self.conn.cursor() as cursor:
            cursor.execute(RPC_SQL, params)
            return SimpleNamespace(data=cursor.fetchone()[0])
def decode_content(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [part for part in value if isinstance(part, dict)]
def media_parts(value: Any) -> list[tuple[int, dict[str, Any]]]:
    """提取 ContentPart 或历史 task result 中的持久化媒体。"""
    parts = decode_content(value)
    if isinstance(value, dict):
        parts = (
            [value]
            if value.get("type") in ("image", "video", "file", "image_url")
            else []
        )
        for key, media_type in (("image_urls", "image"), ("video_url", "video")):
            urls = value.get(key)
            urls = [urls] if isinstance(urls, str) else urls
            if isinstance(urls, list):
                parts.extend(
                    {"type": media_type, "url": url}
                    for url in urls if isinstance(url, str)
                )
    extracted: list[tuple[int, dict[str, Any]]] = []
    for index, part in enumerate(parts):
        media_type = part.get("type")
        url = part.get("url")
        if media_type == "image_url":
            nested = part.get("image_url")
            url = nested.get("url") if isinstance(nested, dict) else None
            media_type = "image"
        if media_type in ("image", "video", "file") and isinstance(url, str):
            extracted.append((index, {**part, "type": media_type, "url": url}))
    return extracted
def project_row(
    source: str,
    row: dict[str, Any],
) -> list[tuple[ReadyAssetDraft, AssetRefDraft]]:
    if not row.get("actor_user_id"):
        return []
    if source == "image_generations":
        return [_image_generation_projection(row)] if row.get("url") else []
    if source == "attachments":
        return [_attachment_projection(row)] if row.get("url") else []

    values = row.get("content")
    if source == "tasks":
        values = row.get("result_data") or row.get("result")
    projections: list[tuple[ReadyAssetDraft, AssetRefDraft]] = []
    for index, part in media_parts(values):
        projection = _part_projection(source, row, index, part)
        if projection:
            projections.append(projection)
    return projections
def _image_generation_projection(
    row: dict[str, Any],
) -> tuple[ReadyAssetDraft, AssetRefDraft]:
    asset = _asset(row, {"type": "image", "url": row["url"]}, "user")
    ref = AssetRefDraft(
        ref_key=f"image_generation:{row['id']}",
        actor_user_id=str(row["actor_user_id"]),
        source_type="generated",
        source_kind="image_task",
        ref_kind="image_generation",
        conversation_id=_optional_str(row.get("conversation_id")),
        source_generation_id=str(row["id"]),
        model_id=_optional_str(row.get("model_id")),
        prompt=row.get("prompt"),
    )
    return asset, ref
def _attachment_projection(
    row: dict[str, Any],
) -> tuple[ReadyAssetDraft, AssetRefDraft]:
    asset = _asset(row, {
        "type": _media_type(row.get("mime_type")),
        "url": row["url"],
        "workspace_path": row.get("workspace_path"),
        "name": row.get("name"),
        "mime_type": row.get("mime_type"),
        "size": row.get("size"),
    }, str(row["storage_scope"]))
    ref = AssetRefDraft(
        ref_key=f"wecom:{row['id']}",
        actor_user_id=str(row["actor_user_id"]),
        source_type="upload",
        source_kind="wecom_upload",
        ref_kind="attachment",
        conversation_id=str(row["conversation_id"]),
        source_message_id=str(row["source_message_id"]),
        source_attachment_id=str(row["id"]),
    )
    return asset, ref
def _part_projection(
    source: str,
    row: dict[str, Any],
    index: int,
    part: dict[str, Any],
) -> tuple[ReadyAssetDraft, AssetRefDraft] | None:
    is_generated = source in ("tasks", "assistant_messages")
    if source == "tasks":
        source_kind = "image_task" if row["type"] == "image" else "video_task"
        ref_kind, ref_key = "task", f"task:{row['id']}:{index}"
    else:
        source_kind = (
            part.get("_asset_source_kind") or
            ("media_tool" if is_generated else "web_upload")
        )
        if is_generated and source_kind not in ("media_tool", "ecom_image"):
            return None
        ref_kind, ref_key = "message", f"message:{row['id']}:{index}"
    asset = _asset(row, part, _storage_scope(row))
    params = row.get("request_params") or row.get("generation_params") or {}
    ref = AssetRefDraft(
        ref_key=ref_key,
        actor_user_id=str(row["actor_user_id"]),
        source_type="generated" if is_generated else "upload",
        source_kind=source_kind,
        ref_kind=ref_kind,
        conversation_id=_optional_str(row.get("conversation_id")),
        source_message_id=(
            _optional_str(row.get("assistant_message_id"))
            if source == "tasks" else str(row["id"])
        ),
        source_task_id=str(row["id"]) if source == "tasks" else None,
        content_index=index,
        model_id=_optional_str(
            row.get("model_id") or params.get("model_id") or params.get("model")
        ),
        prompt=params.get("prompt") or part.get("_asset_prompt"),
    )
    return asset, ref
def _asset(
    row: dict[str, Any],
    part: dict[str, Any],
    storage_scope: str,
) -> ReadyAssetDraft:
    url = str(part["url"])
    media_type = str(part["type"])
    name = part.get("name") or PurePosixPath(urlparse(url).path).name
    return ReadyAssetDraft(
        org_id=_optional_str(row.get("org_id")),
        storage_scope=storage_scope,
        storage_owner_key=_storage_owner(row, storage_scope),
        media_type=media_type,
        original_url=part.get("original_url") or url,
        thumbnail_url=part.get("thumbnail_url") or part.get("thumbnail"),
        download_url=part.get("download_url") or url,
        workspace_path=part.get("workspace_path"),
        name=name or f"historical-{row['id']}",
        mime_type=part.get("mime_type") or part.get("mime"),
        size=part.get("size"),
        created_at=str(row["created_at"]),
    )
def _storage_scope(row: dict[str, Any]) -> str:
    return "channel" if row.get("scope_type") == "channel" else "user"
def _storage_owner(row: dict[str, Any], storage_scope: str) -> str:
    if storage_scope == "user":
        return str(row["actor_user_id"])
    return build_wecom_channel_workspace_owner(
        str(row.get("corp_id") or ""),
        str(row.get("external_chat_id") or ""),
    )
def _media_type(mime_type: Any) -> str:
    value = str(mime_type or "")
    if value.startswith("image/"):
        return "image"
    if value.startswith("video/"):
        return "video"
    return "file"
def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None
def load_checkpoint(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("checkpoint must be an object")
    for source, cursor in value.items():
        if (
            source not in SOURCE_ORDER
            or not isinstance(cursor, dict)
            or not isinstance(cursor.get("created_at"), str)
            or not isinstance(cursor.get("id"), str)
        ):
            raise ValueError("checkpoint cursor is invalid")
    return value
def save_checkpoint(
    path: Path,
    checkpoint: dict[str, dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
def fetch_batch(
    conn: psycopg.Connection[Any],
    source: str,
    cursor: dict[str, str] | None,
    batch_size: int,
) -> list[dict[str, Any]]:
    params = {
        "cursor_at": cursor.get("created_at") if cursor else None,
        "cursor_id": cursor.get("id") if cursor else None,
        "batch_size": batch_size,
    }
    with conn.cursor(row_factory=psycopg.rows.dict_row) as db_cursor:
        db_cursor.execute(SOURCE_QUERIES[source], params)
        return list(db_cursor.fetchall())
def process_projection(
    registry: AssetRegistryService,
    projection: tuple[ReadyAssetDraft, AssetRefDraft],
    stats: BackfillStats,
    identities: set[tuple[Any, ...]],
    *,
    apply: bool,
) -> None:
    asset, ref = projection
    try:
        try:
            identity = _resolve_draft_identity(asset)
        except AssetIdentityError as error:
            if str(error) == "ASSET_URL_NOT_PERSISTED":
                stats.skipped += 1
                _record_reason(
                    stats.skipped_reasons,
                    ref.source_kind,
                    str(error),
                )
                return
            if str(error) != "ASSET_WORKSPACE_URL_MISMATCH":
                raise
            asset = replace(asset, workspace_path=None)
            identity = _resolve_draft_identity(asset)
            stats.normalized_workspace_paths += 1
        identities.add((
            asset.org_id, asset.storage_scope, asset.storage_owner_key,
            identity.storage_provider, identity.storage_key,
        ))
        stats.projected_refs += 1
        if not apply:
            return
        with registry.db.conn.transaction():
            result = registry.register_ready_asset(asset, ref)
        stats.assets_created += int(bool(result.get("asset_created")))
        stats.assets_reused += int(not result.get("asset_created"))
        stats.refs_created += int(bool(result.get("ref_created")))
        stats.refs_reused += int(not result.get("ref_created"))
    except Exception as error:
        if "CONFLICT" in str(error):
            stats.conflicts += 1
        else:
            stats.failures += 1
        _record_failure(stats, ref.source_kind, error)


def _resolve_draft_identity(asset: ReadyAssetDraft) -> Any:
    return resolve_asset_identity(
        original_url=asset.original_url,
        workspace_path=asset.workspace_path,
        org_id=asset.org_id,
        storage_scope=asset.storage_scope,
        storage_owner_key=asset.storage_owner_key,
    )


def _record_failure(
    stats: BackfillStats,
    source: str,
    error: Exception,
) -> None:
    code = str(getattr(error, "code", "") or str(error)).strip()
    if not code or len(code) > 80:
        code = type(error).__name__
    _record_reason(stats.failure_reasons, source, code)


def _record_reason(
    reasons: dict[str, int],
    source: str,
    code: str,
) -> None:
    key = f"{source}:{code}"
    reasons[key] = reasons.get(key, 0) + 1


def count_orphans(conn: psycopg.Connection[Any]) -> int:
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) FROM user_assets asset
             WHERE NOT EXISTS (
                 SELECT 1 FROM user_asset_refs ref
                  WHERE ref.asset_id = asset.id
             )
        """)
        return int(cursor.fetchone()[0])


def run(
    conn: psycopg.Connection[Any],
    *,
    apply: bool,
    batch_size: int,
    checkpoint_path: Path,
    limit: int | None = None,
) -> BackfillStats:
    checkpoint = load_checkpoint(checkpoint_path) if apply else {}
    stats = BackfillStats()
    identities: set[tuple[Any, ...]] = set()
    registry = AssetRegistryService(PsycopgRpcClient(conn))

    for source in SOURCE_ORDER:
        while limit is None or stats.source_rows < limit:
            batch_checkpoint = checkpoint.get(source)
            errors_before = stats.conflicts + stats.failures
            rows = fetch_batch(
                conn, source, batch_checkpoint, batch_size,
            )
            if not rows:
                break
            for row in rows:
                if limit is not None and stats.source_rows >= limit:
                    break
                stats.source_rows += 1
                row_errors_before = stats.conflicts + stats.failures
                try:
                    projections = project_row(source, row)
                except Exception as error:
                    stats.failures += 1
                    _record_failure(stats, source, error)
                    projections = []
                if not projections:
                    if stats.conflicts + stats.failures == row_errors_before:
                        stats.skipped += 1
                for projection in projections:
                    process_projection(
                        registry, projection, stats, identities, apply=apply,
                    )
                checkpoint[source] = {
                    "created_at": str(row["created_at"]),
                    "id": str(row["id"]),
                }
            if apply:
                conn.commit()
                if stats.conflicts + stats.failures > errors_before:
                    if batch_checkpoint is None:
                        checkpoint.pop(source, None)
                    else:
                        checkpoint[source] = batch_checkpoint
                    break
                save_checkpoint(checkpoint_path, checkpoint)
            if len(rows) < batch_size:
                break
    stats.unique_assets = len(identities)
    stats.orphan_assets = count_orphans(conn)
    if not apply:
        conn.rollback()
    return stats


def load_env() -> None:
    for path in (BACKEND / ".env", ROOT / ".env"):
        if path.exists():
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value.strip().strip("\"'"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--checkpoint-file", type=Path, default=DEFAULT_CHECKPOINT,
    )
    args = parser.parse_args()
    if args.batch_size <= 0 or (args.limit is not None and args.limit <= 0):
        parser.error("batch-size and limit must be positive")
    load_env()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    with psycopg.connect(database_url) as conn:
        stats = run(
            conn,
            apply=args.apply,
            batch_size=args.batch_size,
            checkpoint_path=args.checkpoint_file,
            limit=args.limit,
        )
    print(json.dumps(asdict(stats), ensure_ascii=False, sort_keys=True))
    if stats.conflicts or stats.failures or stats.orphan_assets:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
