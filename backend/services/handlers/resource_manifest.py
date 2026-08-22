"""任务级不可变资源清单。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ResourceAsset:
    asset_id: str
    name: str
    workspace_path: str
    mime_type: str
    size: int | None
    url: str
    attachment_set_id: str | None = None


@dataclass(frozen=True)
class ResourceManifest:
    task_id: str
    input_message_id: str
    assets: tuple[ResourceAsset, ...]
    source: str

    @property
    def allowed_paths(self) -> frozenset[str]:
        return frozenset(asset.workspace_path for asset in self.assets)


def build_resource_manifest(
    db: Any,
    *,
    task_id: str,
    input_message_id: str,
    conversation_id: str,
    turn_id: str,
    org_id: str | None,
    input_content: Any,
    current_text: str = "",
) -> ResourceManifest:
    """优先读取 task_attachment_refs；Web/旧任务回退到固定输入消息。"""
    ref_query = (
        db.table("task_attachment_refs")
        .select("attachment_id,attachment_set_id,turn_id,input_message_id")
        .eq("task_id", task_id)
        .eq("input_message_id", input_message_id)
        .eq("turn_id", turn_id)
    )
    if org_id:
        ref_query = ref_query.eq("org_id", org_id)
    response = ref_query.execute()
    refs = response.data if response and isinstance(response.data, list) else []
    if not refs:
        input_assets = tuple(_assets_from_input(input_content, input_message_id))
        if input_assets:
            return ResourceManifest(
                task_id=task_id,
                input_message_id=input_message_id,
                assets=input_assets,
                source="input_message",
            )

        selected = _asset_selected_from_conversation(
            db,
            conversation_id=conversation_id,
            org_id=org_id,
            current_text=current_text,
        )
        return ResourceManifest(
            task_id=task_id,
            input_message_id=input_message_id,
            assets=tuple(selected),
            source="conversation_selection" if selected else "input_message",
        )

    attachment_ids = [str(ref["attachment_id"]) for ref in refs]
    asset_response = (
        db.table("conversation_attachment_refs")
        .select(
            "id,conversation_id,org_id,canonical_name,workspace_path,"
            "detected_mime_type,size,url,status"
        )
        .in_("id", attachment_ids)
        .execute()
    )
    rows = (
        asset_response.data
        if asset_response and isinstance(asset_response.data, list)
        else []
    )
    set_by_asset = {
        str(ref["attachment_id"]): str(ref["attachment_set_id"])
        for ref in refs
    }
    assets = tuple(
        _asset_from_row(row, set_by_asset, conversation_id, org_id)
        for row in rows
    )
    if len(assets) != len(set(attachment_ids)):
        raise RuntimeError("RESOURCE_MANIFEST_INCOMPLETE")
    return ResourceManifest(
        task_id=task_id,
        input_message_id=input_message_id,
        assets=assets,
        source="task_attachment_refs",
    )


def _asset_from_row(
    row: dict[str, Any],
    set_by_asset: dict[str, str],
    conversation_id: str,
    org_id: str | None,
) -> ResourceAsset:
    asset_id = str(row.get("id") or "")
    if (
        str(row.get("conversation_id")) != conversation_id
        or (org_id and str(row.get("org_id")) != org_id)
        or row.get("status") != "ready"
        or asset_id not in set_by_asset
    ):
        raise RuntimeError("RESOURCE_MANIFEST_SCOPE_MISMATCH")
    return ResourceAsset(
        asset_id=asset_id,
        attachment_set_id=set_by_asset[asset_id],
        name=str(row.get("canonical_name") or ""),
        workspace_path=str(row.get("workspace_path") or ""),
        mime_type=str(row.get("detected_mime_type") or ""),
        size=row.get("size"),
        url=str(row.get("url") or ""),
    )


def _assets_from_input(
    content: Any,
    input_message_id: str,
) -> Iterable[ResourceAsset]:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(content, list):
        return []
    return [
        ResourceAsset(
            asset_id=str(part.get("asset_id") or f"{input_message_id}:{index}"),
            name=str(part.get("name") or ""),
            workspace_path=str(part["workspace_path"]),
            mime_type=str(part.get("mime_type") or ""),
            size=part.get("size"),
            url=str(part.get("url") or ""),
        )
        for index, part in enumerate(content)
        if isinstance(part, dict)
        and part.get("type") in ("file", "image")
        and part.get("workspace_path")
    ]


def _asset_selected_from_conversation(
    db: Any,
    *,
    conversation_id: str,
    org_id: str | None,
    current_text: str,
) -> list[ResourceAsset]:
    """Resolve a bare list selection against recent conversation attachments.

    The numbered list is presentation-only, so the backend must resolve it
    before the model is allowed to invent a file ID.  Assets are ordered by
    newest source message first, matching the conversation's recent-file UI.
    """
    match = re.fullmatch(r"\s*(\d{1,3})\s*", current_text or "")
    if not match:
        return []
    selected_index = int(match.group(1)) - 1
    if selected_index < 0:
        return []

    query = (
        db.table("messages")
        .select("id,content,created_at")
        .eq("conversation_id", conversation_id)
        .eq("role", "user")
    )
    if org_id:
        query = query.eq("org_id", org_id)
    try:
        response = query.order("created_at", desc=True).limit(100).execute()
    except Exception:
        return []
    rows = response.data if response and isinstance(response.data, list) else []

    assets: list[ResourceAsset] = []
    seen: set[str] = set()
    for row in rows:
        message_id = str(row.get("id") or "")
        for asset in _assets_from_input(row.get("content"), message_id):
            key = asset.workspace_path or asset.asset_id
            if key in seen:
                continue
            seen.add(key)
            assets.append(asset)
    if selected_index >= len(assets):
        return []
    return [assets[selected_index]]
