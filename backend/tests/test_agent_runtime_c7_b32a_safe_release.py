from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.catalog.safe_read_release import (
    build_safe_read_catalog,
    build_safe_read_snapshot,
)
from services.agent.runtime.executors.read_registry import (
    SAFE_READ_TOOL_NAMES,
    read_descriptor,
)
from services.agent.runtime.infrastructure.postgres.authorization import (
    PostgresActionAuthorizationRepository,
)
from services.agent.runtime.ports.coordinator_recovery import (
    ActionDispatchSnapshot,
)
from services.agent.runtime.production_model import _actions


ROOT = Path(__file__).resolve().parents[1]


def test_safe_release_is_exact_descriptor_backed_surface() -> None:
    catalog = build_safe_read_catalog()
    names = {tool.canonical_name for tool in catalog.definitions()}
    assert names == SAFE_READ_TOOL_NAMES
    assert len(names) == 17
    assert "file_search" not in names
    assert all(
        tool.safety_level == "safe"
        and tool.side_effect == "none"
        and tool.authorization_requirement == "none"
        and tool.executor_type == f"runtime_read:{tool.canonical_name}"
        for tool in catalog.definitions()
    )


def test_safe_release_preserves_existing_scope_contract() -> None:
    channel = build_safe_read_snapshot(
        scope="channel", channel="web", gate_state="disabled",
    )
    user = build_safe_read_snapshot(
        scope="user", channel="web", gate_state="disabled",
    )
    assert len(channel.toolset_document["tool_names"]) == 17
    assert len(user.toolset_document["tool_names"]) == 9
    assert not any(
        name.startswith("local_")
        for name in user.toolset_document["tool_names"]
    )
    enabled = build_safe_read_snapshot(
        scope="channel", channel="web", gate_state="enabled",
    )
    assert enabled.toolset_hash == channel.toolset_hash


def test_generated_safe_release_matches_python_ssot() -> None:
    sql = (ROOT / "migrations/230_02_agent_runtime_catalog_safe_read_v9.sql").read_text()
    match = re.search(
        r"INSERT INTO agent_runtime_catalog_facts.*?\$seed\$(.*?)\$seed\$::JSONB",
        sql, re.DOTALL,
    )
    assert match is not None
    stored = json.loads(match.group(1))
    expected = build_safe_read_snapshot(
        scope="user", channel="web", gate_state="enabled",
    ).catalog_document
    assert stored == expected
    assert "enabled_for_new_ingress,recoverable" in sql
    assert sql.count("FALSE, TRUE") == 8


def test_model_safe_read_action_is_preauthorized_with_frozen_facts() -> None:
    snapshot = build_safe_read_snapshot(
        scope="channel", channel="web", gate_state="disabled",
    )
    catalog = build_safe_read_catalog()
    toolset = SimpleNamespace(
        catalog_revision=str(snapshot.catalog_document["catalog_revision"]),
        toolset_hash=snapshot.toolset_hash,
        definitions=catalog.definitions(),
        validate_call=lambda name, arguments: None,
    )
    result = SimpleNamespace(tool_calls=(SimpleNamespace(
        index=0, call_id="call-1", provider_call_id=None,
        name="local_stock_query",
        arguments_json='{"product_code":"SKU-1"}',
    ),))
    _, actions = _actions(result, "run-1", toolset)
    action = actions[0]
    assert action["policy_decision"] == "preauthorized"
    assert action["retry_disposition"] == "retry_safe"
    assert action["policy_snapshot"] == {
        "source": "runtime_executor_registry",
        "safety_level": "safe",
        "side_effect": "none",
        "authorization_requirement": "none",
        "capability_requirements": ["erp.local.stock_query"],
        "capability_revision": next(
            tool.schema_hash for tool in catalog.definitions()
            if tool.canonical_name == "local_stock_query"
        ),
        "catalog_revision": toolset.catalog_revision,
        "effective_toolset_hash": toolset.toolset_hash,
        "schema_hash": next(
            tool.schema_hash for tool in catalog.definitions()
            if tool.canonical_name == "local_stock_query"
        ),
        "executor_revision": 1,
    }


class _Response:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data


class _Query:
    def __init__(self, response: _Response) -> None:
        self._response = response

    async def execute(self) -> _Response:
        return self._response


class _Database:
    scope = DatabaseScope(
        actor_user_id="44444444-4444-4444-4444-444444444444",
        org_id="22222222-2222-2222-2222-222222222222",
        access_kind=DatabaseAccessKind.AGENT_RUNTIME,
    )

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(self, name: str, params: dict[str, object]) -> _Query:
        self.calls.append((name, params))
        return _Query(_Response({
            "outcome": "dispatch_authorized",
            "intent_id": "77777777-7777-7777-7777-777777777777",
            "state_version": 1,
            "external_idempotency_key": "action:key",
            "recovery_mode": "idempotent_replay",
        }))


@pytest.mark.asyncio
async def test_safe_action_uses_one_final_dispatch_gate() -> None:
    database = _Database()
    repository = PostgresActionAuthorizationRepository(database)
    snapshot = ActionDispatchSnapshot(
        attempt={
            "id": "11111111-1111-1111-1111-111111111111",
            "execution_token": "22222222-2222-2222-2222-222222222222",
            "request_hash": "a" * 64,
            "state_version": 0,
        },
        action={
            "id": "33333333-3333-3333-3333-333333333333",
            "policy_revision": "agent-runtime-policy-v1",
            "policy_snapshot": {},
        },
    )
    receipt = await repository.gate(
        snapshot=snapshot, descriptor=read_descriptor("search_knowledge"),
    )
    assert receipt.state_version == 1
    assert [name for name, _ in database.calls] == [
        "gate_agent_action_dispatch_final_v1",
    ]
    assert database.calls[0][1]["p_policy_receipt_id"] is None
