"""按真实文件内容调和历史企微附件元数据。

默认 dry-run；只有显式 --apply 才在单个数据库事务中更新。
不会删除或重命名 Workspace 文件，也不会输出下载 URL。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from core.config import get_settings
from core.workspace import build_wecom_channel_workspace_owner
from services.assets.file_identity import AssetIdentity, identify_file


@dataclass(frozen=True)
class ReconcilePlan:
    attachment_id: str
    source_message_id: str
    workspace_path: str
    identity: AssetIdentity | None
    status: str
    reason: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--org-id", help="只处理指定组织")
    parser.add_argument("--limit", type=int, help="最多处理 N 条")
    return parser.parse_args()


def fetch_attachments(
    cursor: psycopg.Cursor,
    *,
    org_id: str | None,
    limit: int | None,
    lock: bool,
) -> list[dict[str, Any]]:
    conditions = ["a.channel = 'wecom'"]
    params: list[Any] = []
    if org_id:
        conditions.append("a.org_id = %s")
        params.append(org_id)
    suffix = ""
    if limit:
        suffix += " LIMIT %s"
        params.append(limit)
    if lock:
        suffix += " FOR UPDATE OF a"
    cursor.execute(
        """
        SELECT a.*, b.corp_id, b.external_chat_id
          FROM conversation_attachment_refs a
          LEFT JOIN conversation_channel_bindings b
            ON b.conversation_id = a.conversation_id
           AND b.org_id = a.org_id
           AND b.chat_type = 'group'
         WHERE """ + " AND ".join(conditions)
        + " ORDER BY a.created_at, a.id" + suffix,
        params,
    )
    return list(cursor.fetchall())


def build_plan(
    row: dict[str, Any],
    workspace_root: Path,
) -> ReconcilePlan:
    try:
        target = resolve_asset_path(row, workspace_root)
    except ValueError as error:
        return _failed_plan(row, "unsafe_path", str(error))
    if not target.is_file():
        return _failed_plan(row, "missing", "workspace_file_missing")
    try:
        data = target.read_bytes()
    except OSError as error:
        return _failed_plan(row, "unreadable", type(error).__name__)

    provider_name = row.get("provider_name")
    if (
        row.get("detection_source") == "legacy"
        and str(provider_name or "").lower().endswith(".bin")
    ):
        provider_name = None
    identity = identify_file(
        data,
        stable_id=str(row["source_provider_id"]),
        provider_name=provider_name,
    )
    changed = _identity_changed(row, identity)
    return ReconcilePlan(
        attachment_id=str(row["id"]),
        source_message_id=str(row["source_message_id"]),
        workspace_path=str(row["workspace_path"]),
        identity=identity,
        status="update" if changed else "unchanged",
    )


def resolve_asset_path(
    row: dict[str, Any],
    workspace_root: Path,
) -> Path:
    org_id = str(row["org_id"])
    if row["storage_scope"] == "channel":
        corp_id = str(row.get("corp_id") or "")
        chat_id = str(row.get("external_chat_id") or "")
        if not corp_id or not chat_id:
            raise ValueError("channel_binding_missing")
        owner = build_wecom_channel_workspace_owner(corp_id, chat_id)
    elif row["storage_scope"] == "user":
        owner = str(row["sender_user_id"])
    else:
        raise ValueError("storage_scope_invalid")
    root = (workspace_root / "org" / org_id / owner).resolve()
    target = (root / str(row["workspace_path"])).resolve()
    if not target.is_relative_to(root):
        raise ValueError("workspace_path_outside_scope")
    return target


def apply_plan(
    cursor: psycopg.Cursor,
    plan: ReconcilePlan,
) -> None:
    if plan.status != "update" or plan.identity is None:
        return
    identity = plan.identity
    cursor.execute(
        """
        UPDATE conversation_attachment_refs
           SET provider_name = %s, canonical_name = %s,
               detected_mime_type = %s, detection_source = %s,
               content_sha256 = %s, size = %s,
               original_name = %s, mime_type = %s
         WHERE id = %s
        """,
        (
            identity.provider_name,
            identity.canonical_name,
            identity.detected_mime_type,
            identity.detection_source,
            identity.content_sha256,
            identity.size,
            identity.canonical_name,
            identity.detected_mime_type,
            plan.attachment_id,
        ),
    )
    cursor.execute(
        "SELECT content FROM messages WHERE id = %s FOR UPDATE",
        (plan.source_message_id,),
    )
    message = cursor.fetchone()
    if not message:
        raise RuntimeError("RECONCILE_SOURCE_MESSAGE_MISSING")
    content = update_message_content(
        message["content"], plan.attachment_id, identity,
    )
    cursor.execute(
        "UPDATE messages SET content = %s WHERE id = %s",
        (json.dumps(content, ensure_ascii=False), plan.source_message_id),
    )


def update_message_content(
    raw_content: Any,
    attachment_id: str,
    identity: AssetIdentity,
) -> list[dict[str, Any]]:
    content = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
    if not isinstance(content, list):
        raise RuntimeError("RECONCILE_MESSAGE_CONTENT_INVALID")
    candidates = [
        part for part in content
        if isinstance(part, dict) and part.get("type") == "file"
        and (
            not part.get("asset_id")
            or str(part.get("asset_id")) == attachment_id
        )
    ]
    if len(candidates) != 1:
        raise RuntimeError("RECONCILE_MESSAGE_FILEPART_AMBIGUOUS")
    part = candidates[0]
    part.update({
        "asset_id": attachment_id,
        "name": identity.canonical_name,
        "mime_type": identity.detected_mime_type,
        "size": identity.size,
    })
    return content


def _identity_changed(
    row: dict[str, Any],
    identity: AssetIdentity,
) -> bool:
    expected = (
        identity.provider_name,
        identity.canonical_name,
        identity.detected_mime_type,
        identity.detection_source,
        identity.content_sha256,
        identity.size,
    )
    current = (
        row.get("provider_name"),
        row.get("canonical_name"),
        row.get("detected_mime_type"),
        row.get("detection_source"),
        row.get("content_sha256"),
        row.get("size"),
    )
    return current != expected


def _failed_plan(
    row: dict[str, Any],
    status: str,
    reason: str,
) -> ReconcilePlan:
    return ReconcilePlan(
        attachment_id=str(row["id"]),
        source_message_id=str(row["source_message_id"]),
        workspace_path=str(row["workspace_path"]),
        identity=None,
        status=status,
        reason=reason,
    )


def run(args: argparse.Namespace) -> int:
    load_dotenv(BACKEND_ROOT / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL_NOT_CONFIGURED")
    workspace_root = Path(get_settings().file_workspace_root)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            rows = fetch_attachments(
                cursor,
                org_id=args.org_id,
                limit=args.limit,
                lock=args.apply,
            )
            plans = [build_plan(row, workspace_root) for row in rows]
            if args.apply:
                for plan in plans:
                    apply_plan(cursor, plan)
                connection.commit()
            else:
                connection.rollback()
    print_report(plans, apply=args.apply)
    return 1 if any(plan.status in {
        "unsafe_path", "missing", "unreadable",
    } for plan in plans) else 0


def print_report(plans: list[ReconcilePlan], *, apply: bool) -> None:
    counts = Counter(plan.status for plan in plans)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"{mode} scanned={len(plans)} counts={dict(sorted(counts.items()))}")
    for plan in plans:
        if plan.status in ("update", "missing", "unsafe_path", "unreadable"):
            name = plan.identity.canonical_name if plan.identity else "-"
            print(
                f"{plan.status} attachment_id={plan.attachment_id} "
                f"canonical_name={name} reason={plan.reason or '-'}"
            )


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
