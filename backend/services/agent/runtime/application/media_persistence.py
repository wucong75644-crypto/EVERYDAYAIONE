"""Workspace/OSS/asset-registry adapter for Runtime media projection."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from services.agent.runtime.ports.media_projection import MediaProjectionAssetRequest
from services.assets.asset_registry import (
    AssetRefDraft, AssetRegistryService, ReadyAssetDraft,
)

WorkspacePersist = Callable[..., Awaitable[Mapping[str, object] | None]]


class RuntimeMediaPersistence:
    """Persist one slot under a deterministic identity, then register its Asset."""

    def __init__(
        self, *, workspace_persist: WorkspacePersist,
        asset_registry: AssetRegistryService,
    ) -> None:
        self._workspace_persist = workspace_persist
        self._asset_registry = asset_registry
        self._locks: dict[str, asyncio.Lock] = {}
        self._persisted: dict[str, Mapping[str, object]] = {}

    async def persist(
        self, request: MediaProjectionAssetRequest,
    ) -> Mapping[str, object]:
        lock = self._locks.setdefault(request.identity, asyncio.Lock())
        async with lock:
            existing = self._persisted.get(request.identity)
            if existing is not None:
                return dict(existing)
            payload = await self._workspace_persist(
                url=request.source_url,
                user_id=request.user_id,
                org_id=request.org_id,
                media_type=request.media_kind,
                identity=request.identity,
            )
            if not isinstance(payload, Mapping) or not payload.get("url"):
                raise RuntimeError("RUNTIME_MEDIA_WORKSPACE_PERSIST_FAILED")
            result = dict(payload)
            result["source_url"] = request.source_url
            asset_result = await self._register_asset(request, result)
            asset = asset_result.get("asset")
            if isinstance(asset, Mapping) and asset.get("id"):
                result["asset_id"] = asset["id"]
            self._persisted[request.identity] = dict(result)
            return result

    async def _register_asset(
        self, request: MediaProjectionAssetRequest,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        runtime_register = getattr(self._asset_registry, "register_runtime_media_asset", None)
        if runtime_register is not None:
            registered = runtime_register(request, payload)
            return await registered if inspect.isawaitable(registered) else registered
        url = str(payload["url"])
        asset = ReadyAssetDraft(
            org_id=request.org_id,
            storage_scope="user",
            storage_owner_key=request.user_id,
            media_type=request.media_kind,
            original_url=url,
            download_url=str(payload.get("download_url") or url),
            thumbnail_url=_text(payload.get("thumbnail_url")),
            workspace_path=_text(payload.get("workspace_path")),
            name=str(payload.get("name") or Path(url).name or request.identity),
            mime_type=_text(payload.get("mime_type")),
            size=_int(payload.get("size")),
            metadata={"runtime_identity": request.identity, "source_url": request.source_url},
        )
        ref = AssetRefDraft(
            ref_key=request.identity,
            actor_user_id=request.user_id,
            source_type="generated",
            source_kind=f"{request.media_kind}_task",
            ref_kind="task",
            conversation_id=request.conversation_id,
            source_message_id=request.message_id,
            source_task_id=request.task_id,
            content_index=request.slot_index,
            model_id=request.model_id,
            prompt=request.prompt,
            metadata={"slot_id": request.slot_id, "action_id": request.action_id},
        )
        return await asyncio.to_thread(
            self._asset_registry.register_ready_asset, asset, ref,
        )


def build_runtime_media_persistence(
    *, asset_registry: Any, workspace_root: str | None = None,
    cdn_domain: str | None = None, allowed_result_hosts: tuple[str, ...],
) -> RuntimeMediaPersistence:
    """Build the real adapter from existing Workspace/OSS/registry services."""

    from services.agent.runtime.application.media_safe_download import (
        RuntimeMediaSafeDownloader,
    )

    async def persist_workspace(**kwargs: object) -> Mapping[str, object] | None:
        from services.file_upload import download_url_to_workspace

        source_url = str(kwargs["url"])
        user_id = str(kwargs["user_id"])
        org_id = kwargs.get("org_id")
        org_text = str(org_id) if org_id else None
        identity = str(kwargs["identity"])
        media_type = str(kwargs["media_type"])
        subdir = "下载/AI视频" if media_type == "video" else "下载/AI图片"
        downloader = RuntimeMediaSafeDownloader(allowed_result_hosts)
        try:
            return await download_url_to_workspace(
                url=source_url, user_id=user_id, org_id=org_text,
                subdir=subdir, suggested_stem=identity.replace(":", "_"),
                media_type=media_type, idx=1,
                meta={"runtime_identity": identity}, strict_content_mime=True,
                idempotent_name=True, workspace_root=workspace_root,
                cdn_domain=cdn_domain, use_configured_oss=workspace_root is None,
                downloader=downloader, strict_download_errors=True,
            )
        finally:
            await downloader.close()

    return RuntimeMediaPersistence(
        workspace_persist=persist_workspace, asset_registry=asset_registry,
    )


class RuntimeMediaAssetRegistry:
    """Projection-scoped wrapper over the canonical asset registry RPC."""

    def __init__(
        self, database: Any, *, allowed_asset_hosts: frozenset[str] | None = None,
    ) -> None:
        self._database = database
        self._allowed_asset_hosts = allowed_asset_hosts

    async def register_runtime_media_asset(
        self, request: MediaProjectionAssetRequest,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        from services.assets.asset_identity import resolve_asset_identity

        url = str(payload["url"])
        workspace_path = _text(payload.get("workspace_path"))
        identity = resolve_asset_identity(
            original_url=url, workspace_path=workspace_path,
            org_id=request.org_id, storage_scope="user",
            storage_owner_key=request.user_id,
            allowed_hosts=self._allowed_asset_hosts,
        )
        rpc_payload = {
            **dict(payload),
            "storage_provider": identity.storage_provider,
            "storage_key": identity.storage_key,
            "original_url": url,
            "download_url": str(payload.get("download_url") or url),
            "prompt": request.prompt,
        }
        response = await self._database.rpc(
            "register_agent_runtime_media_asset_v1",
            {"p_action_id": request.action_id, "p_payload": rpc_payload},
        ).execute()
        result = response.data if response else None
        if not isinstance(result, Mapping) or not result.get("asset"):
            raise RuntimeError("RUNTIME_MEDIA_ASSET_REGISTRATION_FAILED")
        return result


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = [
    "RuntimeMediaAssetRegistry", "RuntimeMediaPersistence",
    "build_runtime_media_persistence",
]
