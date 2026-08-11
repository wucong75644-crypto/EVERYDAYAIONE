"""Attempt-fenced access to the existing task attachment manifest."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from services.agent.file_id import compute_fid
from services.agent.runtime.domain import ActionAttempt


@dataclass(frozen=True, kw_only=True)
class RuntimeResourceAsset:
    asset_id: str
    name: str
    workspace_path: str
    mime_type: str
    size: int | None


@dataclass(frozen=True, kw_only=True)
class RuntimeResourceManifest:
    org_id: str | None
    user_id: str
    conversation_id: str
    input_message_id: str
    workspace_scope: str
    workspace_owner_id: str
    source: str
    assets: tuple[RuntimeResourceAsset, ...]

    @property
    def allowed_paths(self) -> frozenset[str]:
        return frozenset(asset.workspace_path for asset in self.assets)

    def resolve_file(self, request: Mapping[str, object]) -> RuntimeResourceAsset:
        file_id = _optional_text(request.get("file_id"))
        path = _optional_text(request.get("path"))
        if not file_id and not path:
            raise ValueError("RUNTIME_RESOURCE_IDENTITY_REQUIRED")
        matches = [
            asset for asset in self.assets
            if (not path or asset.workspace_path == path)
            and (
                not file_id
                or asset.asset_id == file_id
                or compute_fid(self.org_id, asset.workspace_path) == file_id
            )
        ]
        if len(matches) != 1:
            raise PermissionError("RUNTIME_RESOURCE_NOT_IN_MANIFEST")
        return matches[0]


class PostgresRuntimeResourceManifestResolver:
    """Read one immutable manifest through the Runtime Worker facade."""

    def __init__(self, database, *, worker_id: str) -> None:
        self._database = database
        self._worker_id = worker_id

    async def resolve(
        self, attempt: ActionAttempt, request: Mapping[str, object],
    ) -> RuntimeResourceManifest:
        context = request.get("_dispatch_context")
        if not isinstance(context, Mapping):
            raise ValueError("RUNTIME_RESOURCE_DISPATCH_CONTEXT_REQUIRED")
        expected_version = context.get("expected_attempt_version")
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise ValueError("RUNTIME_RESOURCE_ATTEMPT_VERSION_REQUIRED")
        response = await self._database.rpc(
            "get_agent_runtime_resource_manifest_v1", {
                "p_attempt_id": attempt.attempt_id,
                "p_worker_id": self._worker_id,
                "p_execution_token": attempt.lease.fencing_token,
                "p_expected_attempt_version": expected_version,
                "p_request_hash": attempt.request_hash,
            },
        ).execute()
        return _manifest(response.data)


def _manifest(value: object) -> RuntimeResourceManifest:
    if not isinstance(value, Mapping):
        raise RuntimeError("RUNTIME_RESOURCE_MANIFEST_RESPONSE_INVALID")
    raw_assets = value.get("assets")
    if not isinstance(raw_assets, list):
        raise RuntimeError("RUNTIME_RESOURCE_MANIFEST_RESPONSE_INVALID")
    assets = tuple(_asset(item) for item in raw_assets)
    workspace_scope = _required_text(value, "workspace_scope")
    if workspace_scope not in {"user", "channel"}:
        raise RuntimeError("RUNTIME_RESOURCE_MANIFEST_RESPONSE_INVALID")
    source = _required_text(value, "manifest_source")
    if source not in {"task_attachment_refs", "input_message"}:
        raise RuntimeError("RUNTIME_RESOURCE_MANIFEST_RESPONSE_INVALID")
    org_id = _optional_text(value.get("org_id"))
    return RuntimeResourceManifest(
        org_id=org_id,
        user_id=_required_text(value, "user_id"),
        conversation_id=_required_text(value, "conversation_id"),
        input_message_id=_required_text(value, "input_message_id"),
        workspace_scope=workspace_scope,
        workspace_owner_id=_required_text(value, "workspace_owner_id"),
        source=source,
        assets=assets,
    )


def _asset(value: object) -> RuntimeResourceAsset:
    if not isinstance(value, Mapping):
        raise RuntimeError("RUNTIME_RESOURCE_MANIFEST_RESPONSE_INVALID")
    size = value.get("size")
    if isinstance(size, bool) or (size is not None and not isinstance(size, int)):
        raise RuntimeError("RUNTIME_RESOURCE_MANIFEST_RESPONSE_INVALID")
    workspace_path = _required_text(value, "workspace_path")
    _validate_workspace_path(workspace_path)
    return RuntimeResourceAsset(
        asset_id=_required_text(value, "asset_id"),
        name=str(value.get("name") or ""),
        workspace_path=workspace_path,
        mime_type=str(value.get("mime_type") or ""),
        size=size,
    )


def _required_text(value: Mapping[str, object], key: str) -> str:
    result = _optional_text(value.get(key))
    if not result:
        raise RuntimeError("RUNTIME_RESOURCE_MANIFEST_RESPONSE_INVALID")
    return result


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _validate_workspace_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        value != value.strip()
        or value.startswith("/")
        or "\\" in value
        or "//" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError("RUNTIME_RESOURCE_MANIFEST_RESPONSE_INVALID")


__all__ = [
    "PostgresRuntimeResourceManifestResolver", "RuntimeResourceAsset",
    "RuntimeResourceManifest",
]
