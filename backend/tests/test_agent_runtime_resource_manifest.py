from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.agent.file_id import compute_fid
from services.agent.runtime.executors.resource_manifest import (
    PostgresRuntimeResourceManifestResolver,
)


def _attempt():
    return SimpleNamespace(
        attempt_id="attempt-1",
        request_hash="a" * 64,
        lease=SimpleNamespace(fencing_token="token-1"),
    )


def _payload():
    return {
        "org_id": "org-1", "user_id": "user-1",
        "conversation_id": "conversation-1",
        "input_message_id": "message-1",
        "workspace_scope": "user", "workspace_owner_id": "user-1",
        "manifest_source": "task_attachment_refs",
        "assets": [{
            "asset_id": "asset-1", "name": "销售.csv",
            "workspace_path": "上传/2026-08/销售.csv",
            "mime_type": "text/csv", "size": 12,
        }],
    }


@pytest.mark.asyncio
async def test_resolver_uses_attempt_fenced_rpc_and_resolves_existing_file_id() -> None:
    query = MagicMock()
    query.execute = AsyncMock(return_value=SimpleNamespace(data=_payload()))
    database = MagicMock()
    database.rpc.return_value = query
    resolver = PostgresRuntimeResourceManifestResolver(
        database, worker_id="runtime-1",
    )

    manifest = await resolver.resolve(_attempt(), {
        "file_id": compute_fid("org-1", "上传/2026-08/销售.csv"),
        "_dispatch_context": {"expected_attempt_version": 7},
    })

    assert manifest.resolve_file({
        "file_id": compute_fid("org-1", "上传/2026-08/销售.csv"),
    }).asset_id == "asset-1"
    database.rpc.assert_called_once_with(
        "get_agent_runtime_resource_manifest_v1", {
            "p_attempt_id": "attempt-1", "p_worker_id": "runtime-1",
            "p_execution_token": "token-1",
            "p_expected_attempt_version": 7,
            "p_request_hash": "a" * 64,
        },
    )


@pytest.mark.asyncio
async def test_resolver_rejects_missing_dispatch_fence() -> None:
    resolver = PostgresRuntimeResourceManifestResolver(
        MagicMock(), worker_id="runtime-1",
    )
    with pytest.raises(ValueError, match="DISPATCH_CONTEXT_REQUIRED"):
        await resolver.resolve(_attempt(), {"file_id": "asset-1"})


@pytest.mark.asyncio
async def test_manifest_rejects_unbound_path_and_mismatched_dual_identity() -> None:
    database = MagicMock()
    query = MagicMock()
    query.execute = AsyncMock(return_value=SimpleNamespace(data=_payload()))
    database.rpc.return_value = query
    resolver = PostgresRuntimeResourceManifestResolver(
        database, worker_id="runtime-1",
    )

    manifest = await resolver.resolve(_attempt(), {
        "_dispatch_context": {"expected_attempt_version": 1},
    })
    with pytest.raises(PermissionError, match="NOT_IN_MANIFEST"):
        manifest.resolve_file({"path": "旧文件.csv"})
    with pytest.raises(PermissionError, match="NOT_IN_MANIFEST"):
        manifest.resolve_file({
            "file_id": "asset-1", "path": "另一个文件.csv",
        })


@pytest.mark.asyncio
async def test_resolver_rejects_noncanonical_workspace_path() -> None:
    payload = _payload()
    payload["assets"][0]["workspace_path"] = "../越权.csv"
    query = MagicMock()
    query.execute = AsyncMock(return_value=SimpleNamespace(data=payload))
    database = MagicMock()
    database.rpc.return_value = query
    resolver = PostgresRuntimeResourceManifestResolver(
        database, worker_id="runtime-1",
    )
    with pytest.raises(RuntimeError, match="RESPONSE_INVALID"):
        await resolver.resolve(_attempt(), {
            "_dispatch_context": {"expected_attempt_version": 1},
        })
