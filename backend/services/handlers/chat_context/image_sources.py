"""Image discovery metadata, using existing file/message identities only.

This module performs no resource grants, paid IO or implicit reference selection.
Legacy rows are read exclusively from the claim's immutable private catalog.
"""
from copy import deepcopy
import json

from services.agent.file_id import compute_fid

SOURCE_KEY = "_image_sources_v1"
MAX_SOURCE_BYTES = 128000


def content_parts(content):
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            return []
    return content if isinstance(content, list) else []


def image_sources(row, org_id=None, visible_indices=()):
    result = []
    for index, part in enumerate(content_parts(row.get("content"))):
        if not isinstance(part, dict) or part.get("type") != "image":
            continue
        path = part.get("workspace_path")
        available = bool(path) and not part.get("failed")
        source = "generated" if row.get("role") == "assistant" else "uploaded"
        # Client origin hints are not assertions about another message/asset.
        if row.get("role") == "user" and part.get("source_message_id"):
            source = "quoted_pending_verification"
        item = {"name": str(part.get("name") or "图片"), "source": source,
                "available": available, "visually_present": index in visible_indices}
        if row.get("id"):
            item.update(message_id=str(row["id"]), content_index=index)
        if available:
            item["file_id"] = compute_fid(org_id, path)
            if row.get("id"):
                item["reference"] = {"message_id": str(row["id"]), "content_index": index}
            else:
                item["reference"] = {"file_id": item["file_id"]}
        else:
            item["unavailable_reason"] = "IMAGE_FAILED" if part.get("failed") else "IMAGE_ORIGINAL_UNAVAILABLE"
        if row.get("role") == "assistant":
            for key in ("task_id", "asset_id"):
                if part.get(key):
                    item[key] = part[key]
        result.append(item)
    return result


def format_image_sources(sources):
    if not sources:
        return ""
    from services.handlers.image_size_requirements import SIZE_GUIDANCE
    return ("对话图片定位（不是自动选定的生图参考；按用户本次用途复制 reference 中的唯一定位并补充 role，"
            "或复制服务器 file_id；visually_present=false 仅为来源元数据，不能声称本轮看过图；"
            "定位缺失先读取 get_conversation_context/file_search，不要编造 ID）。" + SIZE_GUIDANCE + "：\n"
            + json.dumps(sources, ensure_ascii=False))


def legacy_catalog(parent):
    catalog = (parent.get("request_params") or {}).get(SOURCE_KEY)
    if catalog is None:
        return []  # Pre-migration tasks retain their former strict boundary.
    expected = {"task_id": str(parent["id"]), "conversation_id": str(parent["conversation_id"]),
                "input_message_id": str(parent["input_message_id"]), "org_id": parent.get("org_id"),
                "user_id": str(parent["user_id"]), "base_revision": parent["base_context_revision"]}
    if (not isinstance(catalog, dict) or catalog.get("version") != 1
            or any(catalog.get(key) != value for key, value in expected.items())
            or not isinstance(catalog.get("messages"), list)
            or len(json.dumps(catalog, ensure_ascii=False).encode()) > MAX_SOURCE_BYTES):
        raise ValueError("IMAGE_SOURCE_CATALOG_INVALID")
    return deepcopy(catalog["messages"])


def same_legacy_message(row, frozen):
    # Whole original content is frozen, including exact prompts and origin hints.
    return all(row.get(key) == frozen.get(key) for key in
               ("id", "conversation_id", "org_id", "role", "status", "message_kind")) and (
               row.get("context_revision") is None and
               content_parts(row.get("content")) == content_parts(frozen.get("content")))


def legacy_source_notice(db, rows, conversation_id, org_id, owner_id=None):
    sources = []
    for frozen in rows:
        row = db.table("messages").select(
            "id,conversation_id,org_id,role,status,message_kind,context_revision,content"
        ).eq("id", frozen["id"]).maybe_single().execute().data
        if (not row or row.get("conversation_id") != conversation_id or row.get("org_id") != org_id
                or not same_legacy_message(row, frozen)):
            sources.append({"message_id": frozen["id"], "available": False,
                            "unavailable_reason": "IMAGE_SOURCE_MESSAGE_CHANGED"})
        else:
            sources.extend(discovered_image_sources(row, db, org_id=org_id, owner_id=owner_id) if owner_id else image_sources(row, org_id))
    # Only the model display is bounded. The exact reader retains the entire
    # immutable claim catalog; never silently truncate the authoritative set.
    shown = sources[-20:]
    text = format_image_sources(shown)
    if len(shown) != len(sources):
        text += "\n旧图片来源较多，本轮只展示最后20个来源；其余请用 get_conversation_context 精确读取。"
    return text


def registered_original(db, part, *, org_id, owner_id, scope="user"):
    """Resolve URL-only images through existing canonical assets, never download."""
    if part.get("workspace_path"):
        return part["workspace_path"], None
    url = part.get("original_url") or part.get("download_url") or part.get("url")
    if not url:
        return None, None
    from services.assets.asset_identity import resolve_asset_identity, AssetIdentityError
    try:
        identity = resolve_asset_identity(original_url=url, workspace_path=None,
            org_id=org_id, storage_scope=scope, storage_owner_key=owner_id)
    except AssetIdentityError:
        return None, None
    rows = db.table("user_assets").select("*").eq("org_id", org_id).eq(
        "storage_owner_key", owner_id).eq("storage_scope", scope).eq("status", "ready").eq(
        "media_type", "image").eq("storage_provider", identity.storage_provider).eq(
        "storage_key", identity.storage_key).limit(2).execute().data or []
    if len(rows) != 1 or not rows[0].get("workspace_path"):
        return None, None
    asset = rows[0]
    # Recheck scopes independently of the query implementation and RLS.
    if (asset.get("org_id") != org_id or asset.get("storage_owner_key") != owner_id
            or asset.get("storage_scope") != scope or asset.get("status") != "ready"
            or asset.get("media_type") != "image"):
        return None, None
    return asset["workspace_path"], asset


def discovered_image_sources(row, db, *, org_id, owner_id, scope="user", visible_indices=()):
    """Shared display of registered originals; no untrusted upstream assertions."""
    sources = image_sources(row, org_id, visible_indices)
    if not sources:
        return sources
    parts = content_parts(row.get("content"))
    indices = [index for index, part in enumerate(parts) if isinstance(part, dict) and part.get("type") == "image"]
    paths = {}
    for index, item in zip(indices, sources):
        part = parts[index]
        path, asset = registered_original(db, part, org_id=org_id, owner_id=owner_id, scope=scope)
        if path and not part.get("failed"):
            file_id = compute_fid(org_id, path)
            item.update(available=True, file_id=file_id,
                        reference={"message_id": str(row["id"]), "content_index": index} if row.get("id") else {"file_id": file_id})
            paths[index] = path
            item.pop("unavailable_reason", None)
        if asset:
            item["asset_id"] = str(asset["id"])
        if row.get("role") == "user" and (part.get("source_message_id") or part.get("asset_id")):
            origin = {}
            try:
                validate_quoted_source(db, part, origin, org_id=org_id, owner_id=owner_id,
                    scope=scope, conversation_id=row.get("conversation_id"))
            except (ValueError, PermissionError):
                item.update(available=False, unavailable_reason="IMAGE_QUOTED_SOURCE_DENIED")
                item.pop("file_id", None)
                item.pop("reference", None)
            else:
                item.update({key: value for key, value in origin.items() if key != "source_content_sha256"})
    from services.handlers.image_dimensions import read_image_dimensions
    from services.file_executor import FileExecutor
    from core.config import get_settings
    files = FileExecutor(get_settings().file_workspace_root, owner_id, org_id, create_root=False)
    for index, item in zip(indices, sources):
        if not item.get("available") or index not in paths:
            continue
        path = paths[index]
        try:
            facts = read_image_dimensions(files.resolve_safe_path(path))
            item["canvas"] = facts
        except (OSError, ValueError, PermissionError):
            item["canvas_unavailable_reason"] = "IMAGE_DIMENSIONS_UNAVAILABLE"
    return sources


def validate_quoted_source(db, part, source, *, org_id, owner_id, scope, conversation_id):
    """Verify client origin hints against real same-scope source rows."""
    selected_path, _ = registered_original(db, part, org_id=org_id, owner_id=owner_id, scope=scope)
    if part.get("source_message_id"):
        origin = db.table("messages").select(
            "id,conversation_id,org_id,content,status,role,message_kind"
        ).eq("id", part["source_message_id"]).maybe_single().execute().data
        index = part.get("source_content_index")
        parts = content_parts(origin.get("content")) if isinstance(origin, dict) else []
        if (not origin or origin.get("conversation_id") != conversation_id
                or origin.get("org_id") != org_id
                or origin.get("status") not in {"completed", "interrupted"}
                or origin.get("message_kind", "conversation") != "conversation"
                or type(index) is not int or not 0 <= index < len(parts)):
            raise PermissionError("IMAGE_QUOTED_SOURCE_DENIED")
        original = parts[index]
        if not isinstance(original, dict) or original.get("type") != "image" or original.get("failed"):
            raise ValueError("IMAGE_QUOTED_SOURCE_CHANGED")
        original_path, _ = registered_original(db, original, org_id=org_id, owner_id=owner_id, scope=scope)
        if not selected_path or original_path != selected_path:
            raise ValueError("IMAGE_QUOTED_SOURCE_CHANGED")
        source.update(source="quoted", quoted_message_id=str(origin["id"]), quoted_content_index=index)
        if part.get("source_task_id"):
            task = db.table("tasks").select("id,user_id,org_id,conversation_id,type,status,assistant_message_id").eq("id", part["source_task_id"]).maybe_single().execute().data
            if (not task or task.get("user_id") != owner_id or task.get("org_id") != org_id
                    or task.get("conversation_id") != conversation_id or task.get("type") != "image"
                    or task.get("status") != "completed" or str(task.get("assistant_message_id")) != str(origin["id"])):
                raise ValueError("IMAGE_QUOTED_SOURCE_CHANGED")
            source["quoted_task_id"] = str(task["id"])
    if part.get("asset_id"):
        asset = db.table("user_assets").select("*").eq("id", part["asset_id"]).maybe_single().execute().data
        if (not asset or asset.get("org_id") != org_id
                or asset.get("storage_owner_key") != owner_id
                or asset.get("storage_scope") != scope
                or not selected_path or asset.get("workspace_path") != selected_path
                or asset.get("status") != "ready" or asset.get("media_type") != "image"):
            raise PermissionError("IMAGE_ASSET_DENIED")
        source["source_asset_id"] = str(asset["id"])
        source["source_content_sha256"] = asset.get("content_sha256")


