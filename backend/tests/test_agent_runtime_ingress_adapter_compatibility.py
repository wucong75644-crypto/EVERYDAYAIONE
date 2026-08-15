from types import SimpleNamespace

import pytest

from core.org_scoped_db import OrgScopedDB
from services.agent.runtime.ingress import RuntimeIngress


class _Rpc:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data)


class _Database:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        response = self.responses[name]
        return response if isinstance(response, _Rpc) else _Rpc(response)


@pytest.mark.asyncio
async def test_contract_three_confirms_v5_before_submit() -> None:
    database = _Database({
        "get_agent_runtime_ingress_capability": {"outcome": "available", "ingress_version": 5},
        "runtime_submit_ingress_v5": {"outcome": "created", "entity_id": "command"},
    })
    ingress = RuntimeIngress(database, contract_revision=3)
    ingress._versions = None
    # The fact lookup is supplied directly because this test targets routing.
    database.responses["get_agent_runtime_definition_fact"] = {
        "definition_hash": "definition", "catalog_revision": "catalog",
    }
    result = await ingress.submit(
        conversation_id="conversation", org_id="org", user_id="user",
        scope_kind="user", scope_id="user", agent_definition_id="agent",
        agent_definition_revision="v1", command_type="submit_input",
        idempotency_key="request", payload={"channel": "web", "input_message_id": "message"},
    )
    assert result.accepted
    assert [name for name, _ in database.calls] == [
        "get_agent_runtime_definition_fact", "get_agent_runtime_ingress_capability",
        "runtime_submit_ingress_v5",
    ]


@pytest.mark.asyncio
async def test_prepared_task_uses_atomic_v5_owner_transition_wrapper() -> None:
    database = _Database({
        "get_agent_runtime_ingress_capability": {"outcome": "available", "ingress_version": 5},
        "runtime_submit_ingress_v5_owner_transition": {"outcome": "created", "entity_id": "command"},
        "get_agent_runtime_definition_fact": {
            "definition_hash": "definition", "catalog_revision": "catalog",
        },
    })
    result = await RuntimeIngress(database, contract_revision=3).submit(
        conversation_id="conversation", org_id="org", user_id="user",
        scope_kind="user", scope_id="user", agent_definition_id="agent",
        agent_definition_revision="v1", command_type="submit_input",
        idempotency_key="request", payload={
            "channel": "web", "input_message_id": "message", "output_message_id": "output",
            "turn_id": "turn", "task_id": "task", "client_task_id": "client",
            "request_id": "request",
        },
    )
    assert result.accepted
    assert result.owner_state == "runtime_owned"
    assert result.runtime_owned is True
    assert database.calls[-1][0] == "runtime_submit_ingress_v5_owner_transition"


@pytest.mark.asyncio
async def test_runtime_required_prepared_task_uses_v6_without_legacy_fallback() -> None:
    database = _Database({
        "get_agent_runtime_ingress_capability": {
            "outcome": "available", "ingress_version": 5,
        },
        "runtime_submit_ingress_v6_required": {
            "outcome": "runtime_required_unavailable", "runtime_owned": False,
        },
        "get_agent_runtime_definition_fact": {
            "definition_hash": "definition", "catalog_revision": "catalog",
        },
    })
    result = await RuntimeIngress(
        database, contract_revision=3, require_runtime_owner=True,
    ).submit(
        conversation_id="conversation", org_id="org", user_id="user",
        scope_kind="user", scope_id="user", agent_definition_id="agent",
        agent_definition_revision="v1", command_type="submit_input",
        idempotency_key="request", payload={
            "channel": "web", "input_message_id": "message",
            "output_message_id": "output", "turn_id": "turn", "task_id": "task",
            "client_task_id": "client", "request_id": "request",
        },
    )
    assert result.outcome == "runtime_required_unavailable"
    assert result.owner_state == "runtime_required_unavailable"
    assert result.runtime_owned is False
    assert database.calls[-1][0] == "runtime_submit_ingress_v6_required"


@pytest.mark.asyncio
async def test_runtime_required_org_scope_reaches_v6_capability() -> None:
    database = _Database({
        "get_agent_runtime_ingress_capability": {
            "outcome": "available", "ingress_version": 5,
        },
        "runtime_submit_ingress_v6_required": {
            "outcome": "created", "runtime_owned": True,
        },
        "get_agent_runtime_definition_fact": {
            "definition_hash": "definition", "catalog_revision": "catalog",
        },
    })
    result = await RuntimeIngress(
        OrgScopedDB(database, "org"),
        contract_revision=3,
        require_runtime_owner=True,
    ).submit(
        conversation_id="conversation", org_id="org", user_id="user",
        scope_kind="user", scope_id="user", agent_definition_id="agent",
        agent_definition_revision="v1", command_type="submit_input",
        idempotency_key="request", payload={
            "channel": "web", "input_message_id": "message",
            "output_message_id": "output", "turn_id": "turn",
            "task_id": "task", "client_task_id": "client",
            "request_id": "request",
        },
    )

    assert result.accepted
    capability_call = database.calls[1]
    assert capability_call == ("get_agent_runtime_ingress_capability", {})
    assert database.calls[-1][0] == "runtime_submit_ingress_v6_required"


@pytest.mark.asyncio
async def test_runtime_required_does_not_fallback_when_capability_is_missing() -> None:
    missing = RuntimeError("PGRST202: Could not find the function")
    database = _Database({
        "get_agent_runtime_ingress_capability": _Rpc(error=missing),
        "runtime_submit_ingress_v4": {"outcome": "created", "entity_id": "legacy"},
        "get_agent_runtime_definition_fact": {
            "definition_hash": "definition", "catalog_revision": "catalog",
        },
    })
    with pytest.raises(RuntimeError, match="RUNTIME_INGRESS_REQUIRED_CAPABILITY_UNAVAILABLE"):
        await RuntimeIngress(
            database, contract_revision=3, require_runtime_owner=True,
        ).submit(
            conversation_id="conversation", org_id="org", user_id="user",
            scope_kind="user", scope_id="user", agent_definition_id="agent",
            agent_definition_revision="v1", command_type="submit_input",
            idempotency_key="request", payload={
                "channel": "web", "input_message_id": "message",
                "task_id": "task", "request_id": "request",
            },
        )
    assert [name for name, _ in database.calls].count("runtime_submit_ingress_v4") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_outcome", "outcome", "owner_state", "runtime_owned"),
    (
        ("marked", "created", "runtime_owned", True),
        ("already_runtime_owned", "already_exists", "runtime_owned", True),
        ("restored", "fallback_to_legacy", "legacy_fallback", False),
        ("already_actor_owned", "fallback_to_legacy", "legacy_fallback", False),
    ),
)
async def test_owner_transition_preserves_atomic_database_evidence(
    raw_outcome, outcome, owner_state, runtime_owned,
) -> None:
    database = _Database({
        "get_agent_runtime_ingress_capability": {
            "outcome": "available", "ingress_version": 5,
        },
        "runtime_submit_ingress_v5_owner_transition": {
            "outcome": raw_outcome, "entity_id": "command",
        },
        "get_agent_runtime_definition_fact": {
            "definition_hash": "definition", "catalog_revision": "catalog",
        },
    })
    result = await RuntimeIngress(database, contract_revision=3).submit(
        conversation_id="conversation", org_id="org", user_id="user",
        scope_kind="user", scope_id="user", agent_definition_id="agent",
        agent_definition_revision="v1", command_type="submit_input",
        idempotency_key="request", payload={
            "channel": "web", "input_message_id": "message",
            "output_message_id": "output", "turn_id": "turn",
            "task_id": "task", "client_task_id": "client",
            "request_id": "request",
        },
    )

    assert result.outcome == outcome
    assert result.raw_outcome == raw_outcome
    assert result.owner_state == owner_state
    assert result.runtime_owned is runtime_owned


@pytest.mark.asyncio
async def test_missing_v5_capability_falls_back_to_v4_then_v3_only_when_missing() -> None:
    missing = RuntimeError("PGRST202: Could not find the function")
    database = _Database({
        "get_agent_runtime_definition_fact": {"definition_hash": "definition", "catalog_revision": "catalog"},
        "get_agent_runtime_ingress_capability": _Rpc(error=missing),
        "runtime_submit_ingress_v4": _Rpc(error=missing),
        "runtime_submit_ingress_v3": {"outcome": "created", "entity_id": "command"},
    })
    result = await RuntimeIngress(database, contract_revision=3).submit(
        conversation_id="conversation", org_id="org", user_id="user",
        scope_kind="user", scope_id="user", agent_definition_id="agent",
        agent_definition_revision="v1", command_type="submit_input",
        idempotency_key="request", payload={"channel": "web", "input_message_id": "message"},
    )
    assert result.accepted
    assert [name for name, _ in database.calls][-3:] == [
        "get_agent_runtime_ingress_capability", "runtime_submit_ingress_v4", "runtime_submit_ingress_v3",
    ]


@pytest.mark.asyncio
async def test_missing_v5_capability_keeps_v4_as_the_legacy_entrypoint() -> None:
    missing = RuntimeError("PGRST202: Could not find the function")
    database = _Database({
        "get_agent_runtime_definition_fact": {"definition_hash": "definition", "catalog_revision": "catalog"},
        "get_agent_runtime_ingress_capability": _Rpc(error=missing),
        "runtime_submit_ingress_v4": {"outcome": "created", "entity_id": "command"},
    })
    result = await RuntimeIngress(database, contract_revision=3).submit(
        conversation_id="conversation", org_id="org", user_id="user",
        scope_kind="user", scope_id="user", agent_definition_id="agent",
        agent_definition_revision="v1", command_type="submit_input",
        idempotency_key="request", payload={"channel": "web", "input_message_id": "message"},
    )
    assert result.accepted
    assert [name for name, _ in database.calls][-2:] == [
        "get_agent_runtime_ingress_capability", "runtime_submit_ingress_v4",
    ]
