"""统一用户资产登记服务。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from loguru import logger

from core.exceptions import AppException
from services.assets.asset_identity import (
    AssetIdentityError,
    resolve_asset_identity,
)

StorageScope = Literal["user", "channel"]
SourceType = Literal["upload", "generated"]
SourceKind = Literal[
    "web_upload", "wecom_upload", "image_task",
    "video_task", "media_tool", "ecom_image",
]
MediaType = Literal["image", "video", "file"]

@dataclass(frozen=True)
class ReadyAssetDraft:
    """已持久化、可登记为 ready 的资产。"""

    org_id: Optional[str]
    storage_scope: StorageScope
    storage_owner_key: str
    media_type: MediaType
    original_url: str
    download_url: str
    name: str
    thumbnail_url: Optional[str] = None
    workspace_path: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None
    content_sha256: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None


@dataclass(frozen=True)
class AssetRefDraft:
    """资产在上传、任务、消息或生成记录中的来源事实。"""

    ref_key: str
    actor_user_id: str
    source_type: SourceType
    source_kind: SourceKind
    ref_kind: Literal[
        "upload", "task", "message", "image_generation", "attachment",
    ]
    conversation_id: Optional[str] = None
    source_message_id: Optional[str] = None
    source_task_id: Optional[str] = None
    source_generation_id: Optional[str] = None
    source_attachment_id: Optional[str] = None
    content_index: Optional[int] = None
    model_id: Optional[str] = None
    prompt: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AssetRegistryService:
    """通过数据库 RPC 原子创建 canonical 资产并绑定来源。"""

    def __init__(self, db: Any):
        self.db = db

    def register_ready_asset(
        self,
        asset: ReadyAssetDraft,
        ref: AssetRefDraft,
    ) -> dict[str, Any]:
        params = _validated_rpc_params(asset, ref)
        try:
            result = self.db.rpc("register_user_asset", params).execute()
            payload = _rpc_payload(result.data if result else None)
            logger.info(
                "asset_registry_registered | asset_id={} | ref_id={} | "
                "user_id={} | org_id={} | source_kind={} | "
                "asset_created={} | ref_created={}",
                payload["asset"].get("id"), payload["ref"].get("id"),
                ref.actor_user_id, asset.org_id, ref.source_kind,
                payload.get("asset_created"), payload.get("ref_created"),
            )
            return payload
        except Exception as exc:
            logger.error(
                "asset_registry_create_failed | user_id={} | org_id={} | "
                "source_kind={} | error_type={}",
                ref.actor_user_id, asset.org_id,
                ref.source_kind, type(exc).__name__,
            )
            if "USER_ASSET_" in str(exc) and "CONFLICT" in str(exc):
                raise AppException(
                    code="ASSET_REGISTRY_CONFLICT",
                    message="资产登记冲突",
                    status_code=409,
                ) from exc
            raise AppException(
                code="ASSET_REGISTRY_WRITE_FAILED",
                message="资产登记失败",
                status_code=500,
            ) from exc


def register_web_upload_best_effort(
    db: Any,
    *,
    user_id: str,
    org_id: Optional[str],
    url: str,
    name: str,
    mime_type: Optional[str],
    size: Optional[int] = None,
    workspace_path: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """登记 Web 上传；索引失败不把已持久化文件改判为上传失败。"""
    if not url:
        return None
    media_type: MediaType = _media_type_from_mime(mime_type)
    source_identity = workspace_path or hashlib.sha256(
        url.encode("utf-8"),
    ).hexdigest()
    asset = ReadyAssetDraft(
        org_id=org_id,
        storage_scope="user",
        storage_owner_key=user_id,
        media_type=media_type,
        original_url=url,
        thumbnail_url=thumbnail_url,
        download_url=url,
        workspace_path=workspace_path,
        name=name,
        mime_type=mime_type,
        size=size,
    )
    ref = AssetRefDraft(
        ref_key=f"upload:user:{user_id}:{source_identity}",
        actor_user_id=user_id,
        source_type="upload",
        source_kind="web_upload",
        ref_kind="upload",
    )
    try:
        return AssetRegistryService(db).register_ready_asset(asset, ref)
    except Exception as exc:
        logger.error(
            "web_upload_asset_registration_failed | user_id={} | org_id={} | "
            "error_type={}",
            user_id, org_id, type(exc).__name__,
        )
        return None


def register_task_media_best_effort(
    db: Any,
    *,
    task: dict[str, Any],
    content_parts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """登记普通图片/视频任务产物；单条失败不影响任务完成链路。"""
    task_id = task.get("id")
    user_id = task.get("user_id")
    task_type = task.get("type")
    if not task_id or not user_id or task_type not in ("image", "video"):
        logger.error(
            "task_asset_registration_skipped | task_id={} | user_id={} | "
            "task_type={} | reason=invalid_task_identity",
            task_id, user_id, task_type,
        )
        return []

    request_params = task.get("request_params") or {}
    registered: list[dict[str, Any]] = []
    for index, part in enumerate(content_parts):
        url = part.get("url")
        media_type = part.get("type")
        if not url or media_type not in ("image", "video"):
            continue
        asset = ReadyAssetDraft(
            org_id=task.get("org_id"),
            storage_scope="user",
            storage_owner_key=user_id,
            media_type=media_type,
            original_url=url,
            thumbnail_url=part.get("thumbnail_url"),
            download_url=part.get("download_url") or url,
            workspace_path=part.get("workspace_path"),
            name=part.get("name") or _name_from_url(url, media_type, index),
            mime_type=part.get("mime_type"),
            size=part.get("size"),
            created_at=task.get("created_at"),
        )
        ref = AssetRefDraft(
            ref_key=f"task:{task_id}:{index}",
            actor_user_id=user_id,
            source_type="generated",
            source_kind=(
                "image_task" if task_type == "image" else "video_task"
            ),
            ref_kind="task",
            conversation_id=task.get("conversation_id"),
            source_message_id=(
                task.get("placeholder_message_id")
                or task.get("assistant_message_id")
            ),
            source_task_id=task_id,
            content_index=index,
            model_id=task.get("model_id"),
            prompt=request_params.get("prompt"),
            metadata=_task_asset_metadata(request_params, part),
        )
        try:
            registered.append(
                AssetRegistryService(db).register_ready_asset(asset, ref),
            )
        except Exception as exc:
            logger.error(
                "task_asset_registration_failed | task_id={} | user_id={} | "
                "task_type={} | asset_index={} | error_type={}",
                task_id, user_id, task_type, index, type(exc).__name__,
            )
    return registered


def register_wecom_attachment_best_effort(
    db: Any,
    *,
    attachment_id: str,
    message_id: str,
    conversation_id: str,
    actor_user_id: str,
    org_id: Optional[str],
    storage_scope: StorageScope,
    storage_owner_key: str,
    file_payload: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """企微附件暂存成功后登记资产；索引失败不回滚已暂存附件。"""
    identity = file_payload.get("asset_identity") or {}
    url = file_payload.get("url")
    if not url:
        return None
    asset = ReadyAssetDraft(
        org_id=org_id,
        storage_scope=storage_scope,
        storage_owner_key=storage_owner_key,
        media_type=_media_type_from_mime(file_payload.get("mime_type")),
        original_url=url,
        download_url=file_payload.get("download_url") or url,
        thumbnail_url=file_payload.get("thumbnail_url"),
        workspace_path=file_payload.get("workspace_path"),
        name=file_payload.get("name") or _name_from_url(url, "file", 0),
        mime_type=file_payload.get("mime_type"),
        size=file_payload.get("size"),
        content_sha256=identity.get("content_sha256"),
    )
    ref = AssetRefDraft(
        ref_key=f"wecom:{attachment_id}",
        actor_user_id=actor_user_id,
        source_type="upload",
        source_kind="wecom_upload",
        ref_kind="attachment",
        conversation_id=conversation_id,
        source_message_id=message_id,
        source_attachment_id=attachment_id,
    )
    try:
        return AssetRegistryService(db).register_ready_asset(asset, ref)
    except Exception as exc:
        logger.error(
            "wecom_asset_registration_failed | attachment_id={} | "
            "user_id={} | org_id={} | error_type={}",
            attachment_id, actor_user_id, org_id, type(exc).__name__,
        )
        return None


def register_message_media_best_effort(
    db: Any,
    *,
    actor_user_id: str,
    org_id: Optional[str],
    storage_scope: StorageScope,
    storage_owner_key: str,
    conversation_id: str,
    source_message_id: str,
    indexed_parts: list[tuple[int, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """按最终消息内容序号登记媒体工具或电商图片产物。"""
    registered: list[dict[str, Any]] = []
    for content_index, part in indexed_parts:
        url = part.get("url")
        media_type = part.get("type")
        source_kind = part.get("_asset_source_kind")
        if (
            not url
            or media_type not in ("image", "video")
            or source_kind not in ("media_tool", "ecom_image")
            or not part.get("workspace_path")
        ):
            continue
        asset = ReadyAssetDraft(
            org_id=org_id,
            storage_scope=storage_scope,
            storage_owner_key=storage_owner_key,
            media_type=media_type,
            original_url=part.get("original_url") or url,
            download_url=part.get("download_url") or url,
            thumbnail_url=(
                part.get("thumbnail_url") or part.get("thumbnail")
            ),
            workspace_path=part.get("workspace_path"),
            name=part.get("name") or _name_from_url(
                url, media_type, content_index,
            ),
            mime_type=part.get("mime_type"),
            size=part.get("size"),
        )
        ref = AssetRefDraft(
            ref_key=f"message:{source_message_id}:{content_index}",
            actor_user_id=actor_user_id,
            source_type="generated",
            source_kind=source_kind,
            ref_kind="message",
            conversation_id=conversation_id,
            source_message_id=source_message_id,
            content_index=content_index,
            model_id=part.get("_asset_model_id"),
            prompt=part.get("_asset_prompt"),
        )
        try:
            registered.append(
                AssetRegistryService(db).register_ready_asset(asset, ref),
            )
        except Exception as exc:
            logger.error(
                "message_asset_registration_failed | message_id={} | "
                "user_id={} | content_index={} | source_kind={} | "
                "error_type={}",
                source_message_id, actor_user_id, content_index,
                source_kind, type(exc).__name__,
            )
    return registered


def _validated_rpc_params(
    asset: ReadyAssetDraft,
    ref: AssetRefDraft,
) -> dict[str, Any]:
    required = (
        ref.ref_key, ref.actor_user_id, asset.storage_owner_key,
        asset.original_url, asset.download_url, asset.name,
    )
    if any(not value or not str(value).strip() for value in required):
        raise _invalid_asset("资产登记参数不完整")
    if asset.size is not None and asset.size < 0:
        raise _invalid_asset("资产大小不能为负数")
    if ref.content_index is not None and ref.content_index < 0:
        raise _invalid_asset("资产来源序号不能为负数")
    if asset.content_sha256 is not None and (
        len(asset.content_sha256) != 64
        or any(char not in "0123456789abcdef" for char in asset.content_sha256)
    ):
        raise _invalid_asset("资产摘要格式无效")
    try:
        identity = resolve_asset_identity(
            original_url=asset.original_url,
            workspace_path=asset.workspace_path,
            org_id=asset.org_id,
            storage_scope=asset.storage_scope,
            storage_owner_key=asset.storage_owner_key,
        )
    except AssetIdentityError as exc:
        raise _invalid_asset(str(exc)) from exc
    return {
        "p_org_id": asset.org_id,
        "p_storage_scope": asset.storage_scope,
        "p_storage_owner_key": asset.storage_owner_key,
        "p_storage_provider": identity.storage_provider,
        "p_storage_key": identity.storage_key,
        "p_media_type": asset.media_type,
        "p_original_url": asset.original_url,
        "p_thumbnail_url": asset.thumbnail_url,
        "p_download_url": asset.download_url,
        "p_workspace_path": asset.workspace_path,
        "p_name": asset.name,
        "p_mime_type": asset.mime_type,
        "p_size": asset.size,
        "p_content_sha256": asset.content_sha256,
        "p_asset_metadata": asset.metadata,
        "p_ref_key": ref.ref_key,
        "p_actor_user_id": ref.actor_user_id,
        "p_source_type": ref.source_type,
        "p_source_kind": ref.source_kind,
        "p_ref_kind": ref.ref_kind,
        "p_conversation_id": ref.conversation_id,
        "p_source_message_id": ref.source_message_id,
        "p_source_task_id": ref.source_task_id,
        "p_source_generation_id": ref.source_generation_id,
        "p_source_attachment_id": ref.source_attachment_id,
        "p_content_index": ref.content_index,
        "p_model_id": ref.model_id,
        "p_prompt": ref.prompt,
        "p_ref_metadata": ref.metadata,
        "p_created_at": asset.created_at,
    }


def _invalid_asset(message: str) -> AppException:
    return AppException(
        code="ASSET_REGISTRY_INVALID",
        message=message,
        status_code=422,
    )


def _media_type_from_mime(mime_type: Optional[str]) -> MediaType:
    if mime_type and mime_type.startswith("image/"):
        return "image"
    if mime_type and mime_type.startswith("video/"):
        return "video"
    return "file"


def _name_from_url(url: str, media_type: str, index: int) -> str:
    name = PurePosixPath(urlparse(url).path).name
    if name:
        return name
    extension = "png" if media_type == "image" else "mp4"
    return f"generated-{index + 1}.{extension}"


def _task_asset_metadata(
    request_params: dict[str, Any],
    part: dict[str, Any],
) -> dict[str, Any]:
    values = {
        "aspect_ratio": request_params.get("aspect_ratio"),
        "resolution": request_params.get("resolution"),
        "duration": part.get("duration"),
    }
    return {key: value for key, value in values.items() if value is not None}


def _rpc_payload(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        data = data[0] if data else None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("asset"), dict)
        or not isinstance(data.get("ref"), dict)
    ):
        raise RuntimeError("asset registry RPC returned invalid payload")
    return data
