from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agent.agent_result import AgentResult
from services.agent.runtime.domain import RuntimeScope, ScopeKind
from services.agent.runtime.executors.data_adapters import (
    RuntimeFetchAllPagesAdapter,
    RuntimeFileAnalyzeAdapter,
)
from services.agent.runtime.executors.family_executors import ArtifactJobExecutor
from services.agent.runtime.executors.provider_adapters import LocalArtifactProvider
from services.agent.runtime.executors.resource_manifest import (
    RuntimeResourceAsset,
    RuntimeResourceManifest,
)
from services.kuaimai.registry import TOOL_REGISTRIES


ORG = "22222222-2222-2222-2222-222222222222"
USER = "44444444-4444-4444-4444-444444444444"


def _attempt(*, kind: ScopeKind = ScopeKind.USER):
    return SimpleNamespace(
        attempt_id="attempt-1", action_id="action-1", session_id="session-1",
        run_id="run-1", request_hash="a" * 64,
        scope=RuntimeScope(
            kind=kind, scope_id=USER if kind is ScopeKind.USER else "group-1",
            user_id=USER if kind is ScopeKind.USER else None, org_id=ORG,
        ),
        lease=SimpleNamespace(fencing_token="token-1"),
    )


@pytest.mark.asyncio
async def test_file_analyze_uses_frozen_manifest_and_restricted_tool_executor() -> None:
    manifest = RuntimeResourceManifest(
        org_id=ORG, user_id=USER, conversation_id="conversation-1",
        input_message_id="message-1", workspace_scope="user",
        workspace_owner_id=USER, source="task_attachment_refs",
        assets=(RuntimeResourceAsset(
            asset_id="asset-1", name="销售.csv",
            workspace_path="上传/销售.csv", mime_type="text/csv", size=12,
        ),),
    )
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=manifest))
    executor = SimpleNamespace(execute=AsyncMock(return_value=AgentResult(
        summary="分析完成", metadata={"ignored": "not exported"},
    )))
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return executor

    adapter = RuntimeFileAnalyzeAdapter(
        database=object(), manifest_resolver=resolver,
        executor_factory=factory,
    )
    request = {
        "file_id": "asset-1",
        "_dispatch_context": {"expected_attempt_version": 3},
    }
    result = await adapter.prepare(_attempt(), request)
    assert captured["allowed_tools"] == frozenset({"file_analyze"})
    assert captured["workspace_user_id"] == USER
    assert captured["resource_manifest"].allowed_paths == frozenset({"上传/销售.csv"})
    executor.execute.assert_awaited_once_with(
        "file_analyze", {"path": "上传/销售.csv"},
    )
    assert result["source_asset_id"] == "asset-1"


@pytest.mark.asyncio
async def test_file_analyze_rejects_unimplemented_sheet_and_redacts_failure() -> None:
    manifest = RuntimeResourceManifest(
        org_id=ORG, user_id=USER, conversation_id="conversation-1",
        input_message_id="message-1", workspace_scope="user",
        workspace_owner_id=USER, source="task_attachment_refs",
        assets=(RuntimeResourceAsset(
            asset_id="asset-1", name="销售.xlsx",
            workspace_path="上传/销售.xlsx", mime_type="application/xlsx", size=12,
        ),),
    )
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=manifest))
    adapter = RuntimeFileAnalyzeAdapter(
        database=object(), manifest_resolver=resolver,
        executor_factory=lambda **_: SimpleNamespace(execute=AsyncMock()),
    )
    with pytest.raises(ValueError, match="SHEET_UNSUPPORTED"):
        await adapter.prepare(_attempt(), {
            "file_id": "asset-1", "sheet": "Sheet2",
            "_dispatch_context": {"expected_attempt_version": 1},
        })
    failing = SimpleNamespace(execute=AsyncMock(return_value=AgentResult(
        summary="cannot read /srv/secret/customer.xlsx", status="error",
        error_message="/srv/secret/customer.xlsx",
    )))
    redacting = RuntimeFileAnalyzeAdapter(
        database=object(), manifest_resolver=resolver,
        executor_factory=lambda **_: failing,
    )
    result = await redacting.prepare(_attempt(), {
        "file_id": "asset-1",
        "_dispatch_context": {"expected_attempt_version": 1},
    })
    assert result["summary"] == "file_analyze failed"
    assert "/srv/secret" not in str(result)


class _Dispatcher:
    def __init__(self) -> None:
        self.close = AsyncMock()
        self.calls = 0

    async def execute_raw(self, tool, action, params):
        self.calls += 1
        assert tool and action
        return {"list": [{"page": params["page"]}], "total": 1}


class _PartialDispatcher(_Dispatcher):
    async def execute_raw(self, tool, action, params):
        self.calls += 1
        if params["page"] == 1:
            return {"list": [{"id": index} for index in range(20)], "total": 40}
        return {"error": "provider credential and internal path must not escape"}


@pytest.mark.asyncio
async def test_fetch_all_pages_uses_runtime_dispatcher_factory_and_closes() -> None:
    tool, actions = next(iter(TOOL_REGISTRIES.items()))
    action = next(name for name, entry in actions.items() if not entry.is_write)
    dispatcher = _Dispatcher()
    factory = SimpleNamespace(create=AsyncMock(return_value=dispatcher))
    adapter = RuntimeFetchAllPagesAdapter(dispatcher_factory=factory)
    request = {
        "tool_name": tool, "action": action, "params": {},
        "page_size": 20, "max_pages": 2,
        "_dispatch_context": {"expected_attempt_version": 1},
    }
    result = await adapter.prepare(_attempt(), request)
    assert result["status"] == "success"
    assert result["count"] == 1
    factory.create.assert_awaited_once_with(_attempt(), request)
    dispatcher.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_all_pages_preserves_partial_failure_without_error_text() -> None:
    tool, actions = next(iter(TOOL_REGISTRIES.items()))
    action = next(name for name, entry in actions.items() if not entry.is_write)
    dispatcher = _PartialDispatcher()
    adapter = RuntimeFetchAllPagesAdapter(
        dispatcher_factory=SimpleNamespace(
            create=AsyncMock(return_value=dispatcher),
        ),
    )
    result = await adapter.prepare(_attempt(), {
        "tool_name": tool, "action": action, "params": {},
        "page_size": 20, "max_pages": 3,
    })
    assert result["status"] == "partial"
    assert result["count"] == 20
    assert result["partial_error"] == {
        "error_code": "ERP_PAGE_PROVIDER_PARTIAL", "failed_page": 2,
    }
    assert "credential" not in str(result)
    dispatcher.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_all_pages_rejects_write_action_before_credentials() -> None:
    write_pair = next(
        (tool, action) for tool, actions in TOOL_REGISTRIES.items()
        for action, entry in actions.items() if entry.is_write
    )
    factory = SimpleNamespace(create=AsyncMock())
    adapter = RuntimeFetchAllPagesAdapter(dispatcher_factory=factory)
    with pytest.raises(PermissionError, match="NOT_READ_ONLY"):
        await adapter.prepare(_attempt(), {
            "tool_name": write_pair[0], "action": write_pair[1], "params": {},
        })
    factory.create.assert_not_awaited()


def test_artifact_family_matches_real_requests_and_forwards_dispatch_fence() -> None:
    class Provider:
        async def submit(self, *args, **kwargs):
            raise AssertionError("not dispatched")

    common = {"executor_type": "test", "revision": 1, "provider": Provider()}
    ArtifactJobExecutor(action_kind="local_data", **common).validate_request({
        "doc_type": "order",
    })
    ArtifactJobExecutor(action_kind="file_analyze", **common).validate_request({
        "file_id": "asset-1",
    })
    ArtifactJobExecutor(action_kind="fetch_all_pages", **common).validate_request({
        "tool_name": "erp_trade_query", "action": "query",
    })
    port = SimpleNamespace(prepare=AsyncMock())
    assert LocalArtifactProvider(port=port, operation="file_analyze").requires_dispatch_context


@pytest.mark.asyncio
async def test_artifact_provider_maps_existing_tool_failure_to_failed_receipt() -> None:
    port = SimpleNamespace(prepare=AsyncMock(return_value={
        "status": "error", "summary": "unsupported file",
    }))
    provider = LocalArtifactProvider(port=port, operation="file_analyze")
    receipt = await provider.submit(
        _attempt(), {"path": "上传/a.exe"}, idempotency_key="key-1",
    )
    assert receipt.state.value == "failed"
