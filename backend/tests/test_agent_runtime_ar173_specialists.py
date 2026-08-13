from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio

import pytest

from services.agent.runtime.domain import ActionAttempt, ActionAttemptStatus, ActionStatus, Lease, RuntimeScope, ScopeKind
from services.agent.runtime.executors.contracts import canonical_request_hash
from services.agent.runtime.executors.materializer import ArtifactMaterializer, MaterializeCheckpoint
from services.agent.runtime.executors.specialist_contracts import (
    CostReservation, NetworkRule, ProviderReceipt, ProviderState,
    ReconciliationContext, validate_public_request,
)
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.executors.reconciler import assert_reconcile_only
from services.agent.runtime.executors.specialist_registry import (
    REMOTE_READ_TOOLS, SPECIALIST_TOOLS, build_specialist_registry, specialist_descriptor,
)
from services.agent.runtime.executors.read_registry import READ_TOOL_SPECS, build_read_executor_registry
from services.agent.runtime.executors.read_only import CallableReadCapability
from services.agent.runtime.executors.sandbox_job import SANDBOX_JOB_DESCRIPTOR, SandboxJobExecutor
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.catalog.consistency import build_nonproduction_full_catalog
from services.agent.runtime.costs import InMemoryActionCostLedger
from services.agent.runtime.providers.callback_inbox import CallbackInbox
from services.agent.runtime.providers.callback_inbox import HMACCallbackVerifier
from services.agent.runtime.executors.real_specialist_composition import (
    NonProductionSpecialistPorts, build_nonproduction_specialist_registry,
)
from services.agent.runtime.executors.resource_contracts import WorkspaceResourceService
from services.agent.runtime.executors.provider_adapters import AllowlistedTransport, HttpProviderTransport, KieMediaProvider
from services.agent.runtime.executors.resource_contracts import (
    ErpSyncService, FetchAllPagesService, FileAnalyzeService, LocalDataService,
)
from services.agent.runtime.infrastructure.postgres.specialist_repository import (
    PostgresSpecialistRepository, SpecialistRpcConflict,
)


def _attempt(request: dict[str, object], status: ActionAttemptStatus = ActionAttemptStatus.DISPATCHING) -> ActionAttempt:
    now = datetime.now(timezone.utc)
    scope = RuntimeScope(kind=ScopeKind.USER, scope_id="user-1", user_id="user-1", org_id=None)
    return ActionAttempt(
        attempt_id="attempt-1", action_id="action-1", scope=scope,
        attempt_number=1, status=status, worker_id="worker-1",
        idempotency_key="attempt-1", request_hash=canonical_request_hash(request),
        lease=Lease(fencing_token="fence-1", expires_at=now + timedelta(minutes=1)),
        started_at=now,
    )


def _reconciliation_context() -> ReconciliationContext:
    return ReconciliationContext(
        token="reconcile-1", lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        state_version=2,
    )


class _Provider:
    async def submit(self, attempt, request, *, idempotency_key):
        return ProviderReceipt(state=ProviderState.COMPLETED, provider="fake", request_hash=attempt.request_hash, result={"summary": "ok", "count": 1})

    async def reconcile(self, attempt, receipt):
        return ProviderReceipt(state=ProviderState.COMPLETED, provider="fake", request_hash=attempt.request_hash, result={"summary": "reconciled"})

    async def cancel(self, attempt, receipt):
        return ProviderReceipt(state=ProviderState.CANCELLED, provider="fake", request_hash=attempt.request_hash, evidence={"cancelled": True})


class _UnknownProvider(_Provider):
    async def submit(self, attempt, request, *, idempotency_key):
        raise TimeoutError("response lost")


class _AcceptedProvider(_Provider):
    async def submit(self, attempt, request, *, idempotency_key):
        return ProviderReceipt(state=ProviderState.ACCEPTED, provider="fake", request_hash=attempt.request_hash, provider_task_ref="task-1", evidence={"accepted": True})


class _Dispatcher:
    async def execute(self, tool_name, action, params):
        from services.kuaimai.registry import TOOL_REGISTRIES
        assert action in TOOL_REGISTRIES[tool_name]
        return type("Result", (), {"status": "success", "summary": f"{tool_name}:{action}", "data": []})()


class _CallbackRepository:
    def __init__(self):
        self.events = []

    async def callback(self, **kwargs):
        self.events.append(kwargs)
        return {"outcome": "accepted"}


class _Facts:
    def __init__(self):
        self.calls = []

    async def cost(self, operation, item, **extra):
        self.calls.append(("cost", operation))
        return {"outcome": "applied"}

    async def provider_terminal(self, **params):
        self.calls.append(("provider", params["state"]))
        return {"outcome": params["state"]}

    async def provider_reconcile(self, **params):
        self.calls.append(("reconcile", params["resolution"]))
        return {"outcome": params["resolution"]}


class _RpcResponse:
    def __init__(self, data):
        self.data = data

    async def execute(self):
        return self


class _RpcDatabase:
    def __init__(self, data):
        self.data = data

    def rpc(self, name, params):
        return _RpcResponse(self.data)

    async def provider_unknown(self, **params):
        self.calls.append(("provider", "unknown"))
        return {"outcome": "unknown"}


class _ObjectStore:
    def __init__(self):
        self.items = {}

    async def put_verified(self, key, content, *, content_hash):
        import hashlib
        actual = hashlib.sha256(content).hexdigest()
        self.items[key] = content
        return {"verified": actual == content_hash, "content_hash": actual}

    async def get(self, key):
        return self.items[key]


async def _value(value):
    return value


def test_specialist_registry_has_exact_23_unique_descriptors() -> None:
    assert len(SPECIALIST_TOOLS) == 23
    registry = build_specialist_registry({name: _Provider() for name in SPECIALIST_TOOLS})
    assert len(registry.descriptors()) == 23
    assert {name for descriptor in registry.descriptors() for name in descriptor.action_kinds} == SPECIALIST_TOOLS
    assert specialist_descriptor("erp_execute").mode.value == "external_action"
    assert specialist_descriptor("generate_video").callback is True


def test_nonproduction_catalog_gate_merges_18_reads_code_and_23_specialists() -> None:
    read_registry = build_read_executor_registry({
        name: CallableReadCapability(lambda snapshot, request: _value({"summary": "ok"}))
        for name in READ_TOOL_SPECS
    })
    specialist_registry = build_specialist_registry({name: _Provider() for name in SPECIALIST_TOOLS})
    sandbox_registry = ExecutorRegistry([(SANDBOX_JOB_DESCRIPTOR, SandboxJobExecutor())])
    catalog = build_nonproduction_full_catalog(read_registry, specialist_registry, sandbox_registry)
    assert len(catalog.definitions()) == 42
    assert SPECIALIST_TOOLS.issubset({item.canonical_name for item in catalog.definitions()})


def test_real_nonproduction_composition_binds_every_tool_to_provider_and_family() -> None:
    registry = build_nonproduction_specialist_registry(NonProductionSpecialistPorts(
        transport=object(), erp_dispatcher=_Dispatcher(), erp_search=lambda query: query,
        artifact=object(), media_task=object(), resource_mutation=object(), child_run=object(),
    ))
    assert len(registry.descriptors()) == 23
    assert {type(registry.resolve(tool)[1]).__name__ for tool in SPECIALIST_TOOLS} == {
        "RemoteReadExecutor", "ArtifactJobExecutor", "MediaGenerationExecutor",
        "ChildRunExecutor", "ErpMutationExecutor", "SyncExecutor",
        "WorkspaceMutationExecutor", "ScheduledTaskExecutor",
    }
    assert type(registry.resolve("erp_api_search")[1].provider).__name__ == "ErpApiSearchProvider"
    assert type(registry.resolve("erp_trade_query")[1].provider).__name__ == "ERPQueryProvider"
    assert registry.resolve("erp_execute")[1].provider.write is True
    assert len({id(registry.resolve(tool)[1].provider) for tool in SPECIALIST_TOOLS}) == 23


@pytest.mark.asyncio
async def test_postgres_repository_rejects_ambiguous_rpc_outcomes() -> None:
    repository = object.__new__(PostgresSpecialistRepository)
    repository._database = _RpcDatabase({"outcome": "fenced"})
    with pytest.raises(SpecialistRpcConflict):
        await repository.provider_terminal(attempt_id="a", execution_token="t", request_hash="a" * 64, state="completed")
    repository._database = _RpcDatabase({"outcome": "duplicate"})
    with pytest.raises(SpecialistRpcConflict):
        await repository.callback(provider="kie", event_id="e", correlation="c", payload_hash="a" * 64, payload_redacted={}, action_id="a", attempt_id="t")
    repository._database = _RpcDatabase({"outcome": "idempotent_readback", "settlement_id": "s"})
    result = await repository.cost("reserve", CostReservation(action_id="a", attempt_id="t", kind="reserve", reserved_amount=1))
    assert result["outcome"] == "idempotent_readback"


@pytest.mark.asyncio
async def test_executor_returns_receipt_without_persisting_terminal_facts() -> None:
    from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
    facts = _Facts()
    executor = SpecialistExecutor(
        executor_type="runtime_remote_read", revision=1, provider=_Provider(), facts=facts,
    )
    receipt = await executor.dispatch(_attempt({"query": "orders", "reserved_credits": 2}), {"query": "orders", "reserved_credits": 2})
    assert receipt.outcome.value == "completed"
    assert facts.calls == []


@pytest.mark.asyncio
async def test_local_file_and_erp_page_services_have_distinct_semantics(tmp_path) -> None:
    (tmp_path / "orders.csv").write_text("id,total\n1,10\n2,20\n", encoding="utf-8")
    staging = tmp_path / "staging"
    materializer = ArtifactMaterializer()
    attempt = _attempt({"path": "orders.csv"})
    local = LocalDataService(root=tmp_path, staging=staging, materializer=materializer)
    summary = await local.prepare(attempt, {"path": "orders.csv", "mode": "summary"})
    detail = await local.prepare(attempt, {"path": "orders.csv", "mode": "detail", "limit": 1})
    exported = await local.prepare(attempt, {"path": "orders.csv", "mode": "export"})
    analyzed = await FileAnalyzeService(root=tmp_path, staging=staging, materializer=materializer).prepare(attempt, {"path": "orders.csv"})
    assert summary["rows"] == 2 and len(detail["data"]) == 1
    assert exported["lineage"]["output_format"] == "parquet"
    assert analyzed["lineage"]["role"] == "file_analyze"

    class _Pages:
        async def execute(self, tool, action, params):
            return {"page": params["page"], "items": [params["page"]]}

    pages = await FetchAllPagesService(
        dispatcher=_Pages(), staging=staging, materializer=materializer,
    ).prepare(attempt, {"tool_name": "erp_product_query", "action": "product_list", "total_pages": 2})
    assert pages["state"] == "completed" and pages["pages"] == 2


@pytest.mark.asyncio
async def test_erp_sync_persists_monotone_phases_and_resumes_without_resubmit() -> None:
    class _Provider:
        def __init__(self):
            self.submits = 0
        async def submit(self, request):
            self.submits += 1
            return {"state": "accepted", "provider_task_ref": "sync-1"}
        async def progress(self, submission):
            return {"state": "completed", "provider_task_ref": submission["provider_task_ref"]}

    class _SyncFacts:
        def __init__(self):
            self.phases = []
        async def sync_phase(self, **params):
            self.phases.append(params["p_phase"])
            return {"outcome": "recorded"}

        async def read_sync_facts(self, **params):
            if not self.phases:
                return {}
            return {"submitted": {"submission": {"state": "accepted", "provider_task_ref": "sync-1"}}}

    provider = _Provider()
    facts = _SyncFacts()
    service = ErpSyncService(provider=provider, local_apply=lambda value: {"rows": 1}, checkpoint_store=lambda value: {"cursor": 1}, facts=facts)
    attempt = _attempt({"scope": "org"})
    completed = await service.run({"scope": "org"}, attempt)
    resumed = await service.run({"scope": "org", "resume_submission": {"state": "accepted", "provider_task_ref": "sync-1"}}, attempt)
    assert completed["state"] == "completed" and resumed["state"] == "completed"
    assert provider.submits == 1
    assert facts.phases == ["submitted", "progressing", "applying", "checkpointed", "completed", "progressing", "applying", "checkpointed", "completed"]


@pytest.mark.asyncio
async def test_isolated_harness_invokes_all_23_distinct_provider_adapters(tmp_path) -> None:
    import json
    calls = []

    async def handler(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode().splitlines()
        method, path, _ = lines[0].split()
        length = next(int(line.split(":", 1)[1]) for line in lines if line.lower().startswith("content-length:"))
        await reader.readexactly(length)
        calls.append((method, path))
        if path.endswith("/tasks/status"):
            response = {"state": "completed", "provider_task_ref": "mock-task", "result": {"count": 1}}
        elif path.endswith("/tasks/cancel"):
            response = {"state": "cancelled", "provider_task_ref": "mock-task", "evidence": {"cancel_confirmed": True}}
        else:
            response = {"state": "accepted", "provider_task_ref": "mock-task", "status_locator": "/api/v1/tasks/status", "evidence": {"mock": True}}
        payload = json.dumps(response).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(payload)).encode() + b"\r\nConnection: close\r\n\r\n" + payload)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    class _Media:
        async def prepare(self, attempt, *, kind):
            return {"task_id": f"task-{kind}", "state": "prepared"}

    class _Resource:
        async def mutate(self, attempt, request, *, operation):
            return {"state": "completed", "operation": operation}

    class _Child:
        async def create(self, attempt, request):
            return {"state": "accepted", "child_run_id": "child-1", "provider_task_ref": "child-1", "evidence": {"created": True}}

    (tmp_path / "data.csv").write_text("id,value\n1,2\n", encoding="utf-8")
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        transport = AllowlistedTransport(HttpProviderTransport({"kie": ("127.0.0.1", port), "crawler": ("127.0.0.1", port), "dashscope": ("127.0.0.1", port)}), (
            NetworkRule(provider="kie", method="POST", paths=frozenset({"/api/v1/image/generations", "/api/v1/video/generations", "/api/v1/tasks/cancel"})),
            NetworkRule(provider="kie", method="GET", paths=frozenset({"/api/v1/tasks/status"})),
            NetworkRule(provider="crawler", method="POST", paths=frozenset({"/v1/crawl"})),
            NetworkRule(provider="crawler", method="GET", paths=frozenset({"/v1/crawl/status"})),
            NetworkRule(provider="dashscope", method="POST", paths=frozenset({"/api/v1/search"})),
        ))
        local = LocalDataService(root=tmp_path, staging=tmp_path / "staging", materializer=ArtifactMaterializer())
        registry = build_nonproduction_specialist_registry(NonProductionSpecialistPorts(
            transport=transport, erp_dispatcher=_Dispatcher(), erp_search=lambda query: query,
            artifact=local, local_data=local, file_analyze=FileAnalyzeService(root=tmp_path, staging=tmp_path / "staging", materializer=ArtifactMaterializer()),
            fetch_all_pages=FetchAllPagesService(dispatcher=_Dispatcher(), staging=tmp_path / "staging", materializer=ArtifactMaterializer()),
            media_task=_Media(), resource_mutation=_Resource(), child_run=_Child(),
        ))
        for tool in sorted(SPECIALIST_TOOLS):
            provider = registry.resolve(tool)[1].provider
            if tool in REMOTE_READ_TOOLS:
                if tool.startswith("erp_"):
                    from services.kuaimai.registry import TOOL_REGISTRIES
                    action = next(iter(TOOL_REGISTRIES[tool]))
                    request = {"action": action, "params": {}}
                else:
                    request = {"query": "isolated"}
            elif tool == "erp_api_search":
                request = {"query": "catalog"}
            elif tool in {"local_data", "file_analyze"}:
                request = {"path": "data.csv", "mode": "summary"}
            elif tool == "fetch_all_pages":
                request = {"tool_name": "erp_product_query", "action": "product_list", "total_pages": 1}
            elif tool in {"generate_image", "generate_video"}:
                request = {"prompt": "isolated"}
            elif tool in {"image_agent", "erp_agent", "erp_analyze"}:
                request = {"child_ordinal": 0, "capability": "runtime.child"}
            elif tool == "erp_execute":
                request = {"operation": "isolated", "action": "isolated", "params": {}}
            elif tool == "trigger_erp_sync":
                request = {"scope": "isolated"}
            elif tool == "manage_scheduled_task":
                request = {"task_id": "task-1", "state_version": 0, "operation": "list"}
            else:
                request = {"resource_id": "1", "relative_path": "data.csv", "oss_key": "mock/data.csv"}
            result = await provider.submit(_attempt(request), request, idempotency_key=f"mock-{tool}")
            assert result.provider in {"erp", "erp_catalog", "crawler", "dashscope", "kie", "artifact", "child_run", "workspace", "scheduler", "erp_sync"}
        assert len(registry.descriptors()) == 23
        assert len({type(registry.resolve(tool)[1].provider).__name__ for tool in SPECIALIST_TOOLS}) >= 7
        assert len({id(registry.resolve(tool)[1].provider) for tool in SPECIALIST_TOOLS}) == 23
        assert {path for _, path in calls} >= {"/api/v1/image/generations", "/api/v1/video/generations", "/v1/crawl", "/api/v1/search"}
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_specialist_executor_converts_completed_receipt_and_unknown_is_reconcile_only() -> None:
    request = {"query": "orders"}
    executor = SpecialistExecutor(executor_type="runtime_remote_read", revision=1, provider=_Provider())
    receipt = await executor.dispatch(_attempt(request), request)
    assert receipt.outcome.value == "completed"
    assert receipt.result and receipt.result.data == {"summary": "ok", "count": 1}
    with pytest.raises(RuntimeError, match="RECONCILE_STATUS_REQUIRED"):
        await executor.reconcile(_attempt(request), _reconciliation_context())


def test_callback_cost_and_materialize_contracts_are_idempotent_and_redacted() -> None:
    import asyncio
    import hashlib
    import hmac
    import time
    body = b'{"status":"done","token":"secret"}'
    timestamp = str(int(time.time()))
    signature = hmac.new(b"isolated-secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    repository = _CallbackRepository()
    inbox = CallbackInbox(HMACCallbackVerifier(secrets_by_provider={"kie": b"isolated-secret"}), repository)
    event = asyncio.run(inbox.ingest("kie", "event-1", "corr-1", {"token": "secret", "status": "done"}, body=body, signature=signature, timestamp=timestamp, action_id="action-1", attempt_id="attempt-1"))
    assert event.payload_redacted["token"] == "[redacted]" and repository.events[0]["action_id"] == "action-1"
    with pytest.raises(RuntimeError, match="USE_INGEST"):
        inbox.record("kie", "event-1", "corr-1", {}, signature_valid=True)

    ledger = InMemoryActionCostLedger()
    item = CostReservation(action_id="a", attempt_id="t", kind="reserve", reserved_amount=2)
    import asyncio
    asyncio.run(ledger.reserve(item))
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        asyncio.run(ledger.reserve(CostReservation(action_id="a", attempt_id="t", kind="reserve", reserved_amount=3)))

    materializer = ArtifactMaterializer()
    checkpoint = materializer.checkpoint(b"artifact")
    assert checkpoint.status == "materialized" and len(checkpoint.content_hash) == 64
    with pytest.raises(ValueError, match="RETRY_MATERIALIZE_ONLY"):
        materializer.retry_materialize(checkpoint)


@pytest.mark.asyncio
async def test_callback_application_verifier_rejects_tampering() -> None:
    import time
    import hmac
    import hashlib
    body = b'{"status":"done"}'
    timestamp = str(int(time.time()))
    signature = hmac.new(b"isolated-secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    repository = _CallbackRepository()
    inbox = CallbackInbox(HMACCallbackVerifier(secrets_by_provider={"kie": b"isolated-secret"}), repository)
    event = await inbox.ingest("kie", "event-2", "corr-2", {"status": "done"}, body=body, signature=signature, timestamp=timestamp, action_id="action-2", attempt_id="attempt-2")
    assert event.signature_valid is True
    with pytest.raises(PermissionError, match="SIGNATURE_INVALID"):
        await inbox.ingest("kie", "event-3", "corr-3", {"status": "done"}, body=body + b"x", signature=signature, timestamp=timestamp, action_id="action-3", attempt_id="attempt-3")


@pytest.mark.asyncio
async def test_workspace_delete_requires_verified_oss_and_restores_by_stable_id(tmp_path) -> None:
    root = tmp_path / "workspace"
    staging = tmp_path / "staging"
    root.mkdir()
    (root / "report.txt").write_bytes(b"report")
    service = WorkspaceResourceService(root=root, staging=staging, objects=_ObjectStore())
    deleted = await service.delete("deleted-1", "report.txt", "workspace/deleted-1")
    assert deleted["state"] == "completed" and not (root / "report.txt").exists()
    restored = await service.restore("deleted-1", "restored/report.txt", "workspace/deleted-1")
    assert restored["state"] == "completed" and (root / "restored/report.txt").read_bytes() == b"report"


@pytest.mark.asyncio
async def test_http_mock_server_proves_media_query_and_cancel_semantics() -> None:
    requests = []

    async def handler(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode().splitlines()
        method, path, _ = lines[0].split()
        length = next(int(line.split(":", 1)[1]) for line in lines if line.lower().startswith("content-length:"))
        body = await reader.readexactly(length)
        requests.append((method, path, body))
        if method == "POST" and path.endswith("/generations"):
            response = {"state": "accepted", "provider_task_ref": "kie-task-1", "status_locator": "/api/v1/tasks/status", "evidence": {"accepted": True}}
        elif method == "GET":
            response = {"state": "completed", "provider_task_ref": "kie-task-1", "result": {"summary": "done", "count": 1}}
        else:
            response = {"state": "cancelled", "provider_task_ref": "kie-task-1", "evidence": {"cancel_confirmed": True}}
        payload = json.dumps(response).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(payload)).encode() + b"\r\nConnection: close\r\n\r\n" + payload)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    import json
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        transport = AllowlistedTransport(HttpProviderTransport({"kie": ("127.0.0.1", port)}), (
            NetworkRule(provider="kie", method="POST", paths=frozenset({"/api/v1/image/generations", "/api/v1/tasks/cancel"})),
            NetworkRule(provider="kie", method="GET", paths=frozenset({"/api/v1/tasks/status"})),
        ))
        with pytest.raises(PermissionError, match="NETWORK_NOT_ALLOWED"):
            await transport.request(provider="kie", method="GET", path="/not-allowlisted", body={}, idempotency_key="bad")
        provider = KieMediaProvider(transport, kind="image")
        attempt = _attempt({"prompt": "a safe image"}, ActionAttemptStatus.DISPATCHING)
        submitted = await provider.submit(attempt, {"prompt": "a safe image"}, idempotency_key="k1")
        assert submitted.state is ProviderState.ACCEPTED
        base = _attempt({"prompt": "a safe image"})
        accepted = ActionAttempt(**{
            **base.__dict__, "status": ActionAttemptStatus.ACCEPTED,
            "accepted_at": datetime.now(timezone.utc),
            "external_receipt": {"provider_task_ref": "kie-task-1"},
        })
        reconciled = await provider.reconcile(accepted, {"provider_task_ref": "kie-task-1", "status_locator": "/api/v1/tasks/status"})
        assert reconciled.state is ProviderState.COMPLETED
        cancelled = await provider.cancel(accepted, {"provider_task_ref": "kie-task-1"})
        assert cancelled.state is ProviderState.CANCELLED
        assert [item[:2] for item in requests] == [("POST", "/api/v1/image/generations"), ("GET", "/api/v1/tasks/status"), ("POST", "/api/v1/tasks/cancel")]
    finally:
        server.close()
        await server.wait_closed()


def test_secret_boundary_requires_opaque_handles() -> None:
    with pytest.raises(PermissionError, match="SECRET_HANDLE_REQUIRED"):
        validate_public_request({"api_token": "plaintext"})
    validate_public_request({"credential_handle": "secret:kie-prod"})


@pytest.mark.asyncio
async def test_submit_timeout_is_unknown_and_accepted_reconciles_without_resubmit() -> None:
    request = {"prompt": "safe"}
    unknown = SpecialistExecutor(executor_type="runtime_media_generation:generate_image", revision=1, provider=_UnknownProvider())
    lost = await unknown.dispatch(_attempt(request), request)
    assert lost.outcome.value == "unknown"

    accepted_executor = SpecialistExecutor(executor_type="runtime_media_generation:generate_image", revision=1, provider=_AcceptedProvider())
    accepted = await accepted_executor.dispatch(_attempt(request), request)
    assert accepted.outcome.value == "accepted"
    accepted_attempt = _attempt(request)
    accepted_attempt = ActionAttempt(**{
        **accepted_attempt.__dict__, "status": ActionAttemptStatus.ACCEPTED,
        "accepted_at": datetime.now(timezone.utc),
        "external_receipt": accepted.external_receipt,
    })
    reconciled = await accepted_executor.reconcile(accepted_attempt, _reconciliation_context())
    assert reconciled.outcome.value == "completed"


def test_network_and_reconcile_guards_fail_closed() -> None:
    rule = NetworkRule(provider="dashscope", method="POST", paths=frozenset({"/search"}))
    assert rule.allows("dashscope", "POST", "/search")
    with pytest.raises(PermissionError, match="NETWORK_NOT_ALLOWED"):
        rule.assert_allowed("dashscope", "GET", "/search")
    with pytest.raises(ValueError, match="RECONCILE_STATUS_REQUIRED"):
        assert_reconcile_only(ActionStatus.RUNNING, ActionAttemptStatus.DISPATCHING)


def test_226_lanes_are_additive_and_rollbacks_fail_closed() -> None:
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for number in range(1, 7):
        migration = next((root / "migrations").glob(f"226_{number:02d}_*.sql"))
        rollback = next((root / "migrations/rollback").glob(f"226_{number:02d}_*_rollback.sql"))
        sql = migration.read_text()
        down = rollback.read_text()
        assert "SECURITY DEFINER" in sql and "SET search_path" in sql
        assert "GRANT EXECUTE" in sql and "REVOKE ALL" in sql
        assert "ROLLBACK_GUARD_FACTS_EXIST" in down
    callback_sql = (root / "migrations/226_02_agent_runtime_action_callback_inbox.sql").read_text()
    assert "p_signature_valid" not in callback_sql
    assert "action_id UUID NOT NULL" in callback_sql and "attempt_id UUID NOT NULL" in callback_sql
    child_sql = (root / "migrations/226_05_agent_runtime_child_runs.sql").read_text()
    assert "md5(" not in child_sql and "digest(convert_to" in child_sql
    resource_sql = (root / "migrations/226_06_agent_runtime_resource_mutation_contracts.sql").read_text()
    assert "p_execution_token UUID" in resource_sql and "i.execution_token" in resource_sql
    assert not any("226_" in path.read_text() for path in (root / "migrations").glob("21[2-9]_*.sql"))
