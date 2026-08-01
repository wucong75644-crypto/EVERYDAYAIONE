from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.db_scope import AsyncScopedDatabaseClient, DatabaseAccessKind, DatabaseScope
from core.local_db import AsyncLocalDBClient
from services.agent.runtime.domain import ActionAttempt, ActionAttemptStatus, Lease
from services.agent.runtime.domain.scope import RuntimeScope, ScopeKind
from services.agent.runtime.executors.contracts import canonical_request_hash
from services.agent.runtime.executors.real_base import RuntimeReadResources
from services.agent.runtime.executors.real_composition import build_nonproduction_read_registry
from services.agent.runtime.executors.read_registry import READ_TOOL_SPECS
from services.agent.runtime.ports.executor import ExecutionOutcome


pytestmark = pytest.mark.external


def _attempt(request: dict[str, object], index: int, org_id: str) -> ActionAttempt:
    now = datetime.now(timezone.utc)
    return ActionAttempt(
        attempt_id=f"ar172-pg-attempt-{index}", action_id=f"ar172-pg-action-{index}",
        scope=RuntimeScope(ScopeKind.CHANNEL, org_id, None, org_id), attempt_number=1,
        status=ActionAttemptStatus.DISPATCHING, worker_id="ar172-pg-worker",
        idempotency_key=f"ar172-pg-idem-{index}", request_hash=canonical_request_hash(request),
        lease=Lease(fencing_token=f"ar172-pg-token-{index}", expires_at=now + timedelta(minutes=1)),
        started_at=now,
    )


@pytest.mark.asyncio
async def test_real_adapters_run_against_isolated_postgres(tmp_path: Path) -> None:
    if os.getenv("RUN_AR172_REAL_DB_TEST") != "1":
        pytest.skip("RUN_AR172_REAL_DB_TEST=1_REQUIRED")
    database_url = os.getenv("AR172_TEST_DATABASE_URL")
    user_id = os.getenv("AR172_TEST_USER_ID")
    org_id = os.getenv("AR172_TEST_ORG_ID")
    conversation_id = os.getenv("AR172_TEST_CONVERSATION_ID")
    if not all((database_url, user_id, org_id, conversation_id)):
        pytest.skip("AR172_TEST_DATABASE_URL, user/org/conversation IDs required")
    client = AsyncLocalDBClient(database_url, min_size=1, max_size=2)
    await client.open()
    scoped = AsyncScopedDatabaseClient(client, DatabaseScope(
        actor_user_id=user_id, org_id=org_id, access_kind=DatabaseAccessKind.RUNTIME,
        request_id="ar172-isolated-test",
    ))
    try:
        root = tmp_path / "workspace" / "org" / org_id / user_id
        root.mkdir(parents=True)
        (root / "read-only.txt").write_text("fixture", encoding="utf-8")
        resources = RuntimeReadResources(
            database=scoped, user_id=user_id, org_id=org_id,
            conversation_id=conversation_id, base_revision=1,
            workspace_root=tmp_path / "workspace",
        )
        registry = build_nonproduction_read_registry(resources)
        requests = {
            "get_conversation_context": {"limit": 1}, "search_knowledge": {"query": "missing"},
            "evidence_search": {"query": ""}, "evidence_get": {"artifact_id": "missing"},
            "memory_search": {"query": "missing"}, "memory_get": {"memory_ref": "memory:00000000-0000-0000-0000-000000000000"},
            "artifact_search": {"query": ""}, "artifact_get": {"artifact_id": "missing"},
            "artifact_read": {"artifact_id": "missing", "cursor": 0, "max_tokens": 256},
            "file_search": {"keyword": "read-only"}, "local_product_identify": {"code": "missing"},
            "local_stock_query": {"product_code": "missing"}, "local_product_stats": {"product_code": "missing"},
            "local_platform_map_query": {"product_code": "missing"}, "local_compare_stats": {"doc_type": "sale", "compare_kind": "wow", "current_period": "today"},
            "local_shop_list": {}, "local_warehouse_list": {}, "local_supplier_list": {},
        }
        for index, tool in enumerate(READ_TOOL_SPECS, 1):
            request = requests[tool]
            _, executor = registry.resolve(tool)
            receipt = await executor.dispatch(_attempt(request, index, org_id), request)
            assert receipt.outcome in {ExecutionOutcome.COMPLETED, ExecutionOutcome.FAILED}
    finally:
        await client.close()
