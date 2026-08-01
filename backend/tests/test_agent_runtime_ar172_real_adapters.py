from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from core.local_db import QueryResponse
from services.agent.runtime.domain import ActionAttempt, ActionAttemptStatus, Lease
from services.agent.runtime.domain.scope import RuntimeScope, ScopeKind
from services.agent.runtime.catalog import RuntimeToolCatalog
from services.agent.runtime.executors.contracts import canonical_request_hash
from services.agent.runtime.executors.contracts import ActionSnapshot
from services.agent.runtime.executors.real_base import RuntimeReadResources
from services.agent.runtime.executors.real_composition import build_nonproduction_read_registry
from services.agent.runtime.executors.real_domain import (
    ArtifactReadCapability, ConversationReadCapability, EvidenceReadCapability,
    KnowledgeReadCapability, MemoryReadCapability, WorkspaceReadCapability,
)
from services.agent.runtime.executors.real_erp import ErpLocalReadCapability
from services.agent.runtime.executors.read_only import ReadOnlyExecutor
from services.agent.runtime.executors.read_registry import READ_TOOL_SPECS
from services.agent.runtime.ports.executor import ExecutionOutcome


USER = "11111111-1111-1111-1111-111111111111"
ORG = "22222222-2222-2222-2222-222222222222"
CONVERSATION = "33333333-3333-3333-3333-333333333333"


class _Query:
    def __init__(self, db: "ScopedReadDatabase", table: str) -> None:
        self.db, self.table_name = db, table
        self.filters: list[tuple[str, str, object]] = []
        self.limit_value = 100
        self.reverse = False
        self.order_key = ""

    def select(self, columns: str) -> "_Query":
        return self

    def eq(self, key: str, value: object) -> "_Query":
        self.filters.append(("eq", key, value))
        return self

    def is_(self, key: str, value: str) -> "_Query":
        self.filters.append(("is", key, value))
        return self

    def ilike(self, key: str, value: str) -> "_Query":
        self.filters.append(("ilike", key, value.strip("%").casefold()))
        return self

    def gte(self, key: str, value: object) -> "_Query":
        self.filters.append(("gte", key, value))
        return self

    def lte(self, key: str, value: object) -> "_Query":
        self.filters.append(("lte", key, value))
        return self

    def order(self, key: str, desc: bool = False) -> "_Query":
        self.order_key, self.reverse = key, desc
        return self

    def limit(self, value: int) -> "_Query":
        self.limit_value = value
        return self

    async def execute(self) -> QueryResponse:
        self.db.operations.append(("select", self.table_name))
        rows = deepcopy(self.db.tables.get(self.table_name, []))
        for operation, key, value in self.filters:
            if operation == "eq":
                rows = [row for row in rows if row.get(key) == value]
            elif operation == "is":
                rows = [row for row in rows if (row.get(key) is None) == (value == "null")]
            elif operation == "ilike":
                rows = [row for row in rows if str(value) in str(row.get(key) or "").casefold()]
            elif operation in {"gte", "lte"}:
                rows = [row for row in rows if _compare(row.get(key), value, operation)]
        if self.order_key:
            rows.sort(key=lambda row: str(row.get(self.order_key) or ""), reverse=self.reverse)
        return QueryResponse(data=rows[: self.limit_value])


class ScopedReadDatabase:
    """Isolated PostgreSQL-shaped read port; no write method exists."""

    def __init__(self, tables: dict[str, list[dict[str, object]]]) -> None:
        self.tables = tables
        self.operations: list[tuple[str, str]] = []
        self.scope = DatabaseScope(
            actor_user_id=USER, org_id=ORG, access_kind=DatabaseAccessKind.RUNTIME,
        )

    def table(self, table: str) -> _Query:
        return _Query(self, table)

    def rpc(self, name: str, params: dict[str, object]) -> "_Rpc":
        return _Rpc(self, name, params)


class _Rpc:
    def __init__(self, db: ScopedReadDatabase, name: str, params: dict[str, object]) -> None:
        self.db, self.name, self.params = db, name, params

    async def execute(self) -> QueryResponse:
        self.db.operations.append(("rpc", self.name))
        return QueryResponse(data={"doc_count": 2, "total_qty": 3, "total_amount": 4})


def _compare(actual: object, expected: object, operation: str) -> bool:
    if actual is None:
        return False
    return actual >= expected if operation == "gte" else actual <= expected


def _tables() -> dict[str, list[dict[str, object]]]:
    return {
        "messages": [{"id": "message-1", "conversation_id": CONVERSATION, "role": "user", "content": [{"type": "text", "text": "hello"}], "created_at": "2026-08-01"}],
        "knowledge_nodes": [{"id": "knowledge-1", "is_deleted": False, "title": "knowledge", "content": "knowledge fact", "category": "general", "node_type": "fact", "source": "local"}],
        "memory_atoms": [{"id": "44444444-4444-4444-4444-444444444444", "user_id": USER, "org_id": ORG, "is_deleted": False, "status": "active", "content": "memory fact", "metadata": {}, "source_message_ids": []}],
        "conversation_data_evidence": [{"artifact_id": "evidence-1", "conversation_id": CONVERSATION, "context_revision": 1, "validation_status": "ready", "source": "local", "columns": ["amount"], "rows": [{"amount": 4}], "query_scope": {}, "model_view": {}, "byte_size": 30}],
        "conversation_artifacts": [{"id": "artifact-1", "org_id": ORG, "conversation_id": CONVERSATION, "context_revision": 1, "status": "ready", "storage_kind": "inline", "inline_content": {"rows": [{"id": "r1"}]}, "artifact_type": "table", "byte_size": 20, "content_hash": "hash", "metadata": {}}],
        "erp_products": [{"org_id": ORG, "outer_id": "P-1", "title": "Product", "shipper": "local", "active_status": 1, "barcode": "B-1"}],
        "erp_product_skus": [{"org_id": ORG, "outer_id": "P-1", "sku_outer_id": "S-1", "properties_name": "Blue", "barcode": "B-1"}],
        "erp_stock_status": [{"org_id": ORG, "outer_id": "P-1", "sku_outer_id": "S-1", "warehouse_id": "W-1", "sellable_num": 2, "total_stock": 3, "lock_stock": 0, "purchase_num": 0, "stock_status": "ok"}],
        "erp_product_daily_stats": [{"org_id": ORG, "outer_id": "P-1", "stat_date": "2026-08-01", "order_count": 1, "order_qty": 2, "order_amount": 3, "purchase_count": 0, "purchase_qty": 0, "receipt_count": 0, "receipt_qty": 0, "aftersale_count": 0, "aftersale_qty": 0}],
        "erp_product_platform_map": [{"org_id": ORG, "outer_id": "P-1", "num_iid": "N-1", "user_id": USER, "sku_mappings": []}],
        "erp_shops": [{"org_id": ORG, "name": "Shop", "platform": "local", "state": 1, "shop_id": "S-1", "short_name": "Shop"}],
        "erp_warehouses": [{"org_id": ORG, "warehouse_id": "W-1", "name": "Warehouse", "code": "W", "warehouse_type": "local", "status": 1, "is_virtual": False}],
        "erp_suppliers": [{"org_id": ORG, "code": "SUP-1", "name": "Supplier", "status": 1, "contact_name": "", "category_name": "general", "remark": ""}],
    }


def _attempt(tool: str, request: dict[str, object], number: int = 1) -> ActionAttempt:
    now = datetime.now(timezone.utc)
    return ActionAttempt(
        attempt_id=f"attempt-{number}", action_id=f"action-{number}", scope=RuntimeScope(
            kind=ScopeKind.CHANNEL, scope_id=ORG, user_id=None, org_id=ORG,
        ), attempt_number=1, status=ActionAttemptStatus.DISPATCHING,
        worker_id="worker-1", idempotency_key=f"idem-{number}",
        request_hash=canonical_request_hash(request),
        lease=Lease(fencing_token=f"token-{number}", expires_at=now + timedelta(minutes=1)),
        started_at=now, capabilities={"tool": tool},
    )


@pytest.mark.asyncio
async def test_all_eighteen_tools_use_real_adapters_and_read_only_sources(tmp_path: Path) -> None:
    root = tmp_path / "workspace" / "org" / ORG / USER
    root.mkdir(parents=True)
    (root / "report.txt").write_text("report", encoding="utf-8")
    database = ScopedReadDatabase(_tables())
    resources = RuntimeReadResources(
        database=database, user_id=USER, org_id=ORG, conversation_id=CONVERSATION,
        base_revision=1, workspace_root=tmp_path / "workspace",
    )
    registry = build_nonproduction_read_registry(resources)
    catalog = RuntimeToolCatalog.from_executor_registry(registry)
    assert {item.canonical_name for item in catalog.definitions()} == set(READ_TOOL_SPECS)
    requests = {
        "get_conversation_context": {"limit": 5}, "search_knowledge": {"query": "knowledge"},
        "evidence_search": {"query": ""}, "evidence_get": {"artifact_id": "evidence-1"},
        "memory_search": {"query": "memory"}, "memory_get": {"memory_ref": "memory:44444444-4444-4444-4444-444444444444"},
        "artifact_search": {"query": ""}, "artifact_get": {"artifact_id": "artifact-1"},
        "artifact_read": {"artifact_id": "artifact-1", "cursor": 0, "max_tokens": 256},
        "file_search": {"keyword": "report"}, "local_product_identify": {"code": "P-1"},
        "local_stock_query": {"product_code": "P-1"}, "local_product_stats": {"product_code": "P-1", "start_date": "2026-08-01", "end_date": "2026-08-01"},
        "local_platform_map_query": {"product_code": "P-1"}, "local_compare_stats": {"doc_type": "sale", "compare_kind": "wow", "current_period": "today"},
        "local_shop_list": {}, "local_warehouse_list": {}, "local_supplier_list": {},
    }
    before = deepcopy(root / "report.txt").stat().st_mtime_ns
    for number, tool in enumerate(READ_TOOL_SPECS, start=1):
        descriptor, executor = registry.resolve(tool)
        assert isinstance(executor, ReadOnlyExecutor)
        assert executor._capability.__class__.__module__.endswith(("real_domain", "real_erp"))
        receipt = await executor.dispatch(_attempt(tool, requests[tool], number), requests[tool])
        assert receipt.outcome is ExecutionOutcome.COMPLETED, (tool, receipt.external_receipt)
    assert database.operations and all(kind in {"select", "rpc"} for kind, _ in database.operations)
    assert (root / "report.txt").stat().st_mtime_ns == before


@pytest.mark.asyncio
async def test_real_binding_scope_expiry_and_bad_reference_fail_closed(tmp_path: Path) -> None:
    database = ScopedReadDatabase(_tables())
    resources = RuntimeReadResources(database=database, user_id=USER, org_id=ORG, workspace_root=tmp_path)
    capability = ArtifactReadCapability(resources)
    request = {"artifact_id": "artifact-1", "cursor": 0, "max_tokens": 256}
    attempt = _attempt("artifact_read", request)
    snapshot = ActionSnapshot.from_attempt(attempt, request, executor_type="runtime_read:artifact_read", executor_revision=1)
    bound = capability.bind(snapshot)
    bound._binding = bound._binding.__class__(action_id="other", attempt_id=attempt.attempt_id, expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))
    with pytest.raises(PermissionError, match="CAPABILITY_BINDING"):
        await bound.read(snapshot, request)
    bound._binding = bound._binding.__class__(action_id=attempt.action_id, attempt_id=attempt.attempt_id, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(PermissionError, match="CAPABILITY_EXPIRED"):
        await bound.read(snapshot, request)
    bad_request = {"memory_ref": "memory:not-a-uuid"}
    memory = MemoryReadCapability(resources)
    bad_attempt = _attempt("memory_get", bad_request, 30)
    receipt = await ReadOnlyExecutor(executor_type="runtime_read:memory_get", executor_revision=1, capability=memory).dispatch(bad_attempt, bad_request)
    assert receipt.outcome is ExecutionOutcome.FAILED
    assert database.operations == []


@pytest.mark.asyncio
async def test_real_adapter_rejects_wrong_org_as_terminal_failure(tmp_path: Path) -> None:
    database = ScopedReadDatabase(_tables())
    resources = RuntimeReadResources(database=database, user_id=USER, org_id=ORG, workspace_root=tmp_path)
    request = {"product_code": "P-1"}
    attempt = _attempt("local_stock_query", request, 40)
    wrong_scope = RuntimeScope(
        kind=ScopeKind.CHANNEL, scope_id="55555555-5555-5555-5555-555555555555",
        user_id=None, org_id="55555555-5555-5555-5555-555555555555",
    )
    attempt = ActionAttempt(**{**attempt.__dict__, "scope": wrong_scope})
    executor = ReadOnlyExecutor(
        executor_type="runtime_read:local_stock_query", executor_revision=1,
        capability=ErpLocalReadCapability(resources, "local_stock_query"),
        allowed_scope_kinds=frozenset({"channel"}),
    )
    receipt = await executor.dispatch(attempt, request)
    assert receipt.outcome is ExecutionOutcome.FAILED
    assert receipt.external_receipt["error_code"] == "READ_PERMISSION_DENIED"
    assert database.operations == []
