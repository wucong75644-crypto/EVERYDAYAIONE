from types import SimpleNamespace

import pytest

from services.agent.runtime.ingress import RuntimeIngress


class _Rpc:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return SimpleNamespace(data=self.data)


class _Database:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _Rpc(self.responses[name])


def _payload() -> dict[str, str]:
    return {
        "channel": "web", "task_id": "task", "client_task_id": "client",
        "input_message_id": "input", "output_message_id": "output",
        "turn_id": "turn", "request_id": "request",
    }


@pytest.mark.asyncio
async def test_ingress_calls_one_required_owner_contract_without_probe() -> None:
    database = _Database({
        "get_agent_runtime_definition_fact": {
            "definition_hash": "definition", "catalog_revision": "catalog",
        },
        "submit_runtime_ingress_required_v1": {
            "outcome": "marked", "runtime_owned": True,
            "session_id": "session", "entity_id": "command",
        },
    })
    result = await RuntimeIngress(database).submit(
        conversation_id="conversation", org_id="org", user_id="user",
        scope_kind="user", scope_id="user", agent_definition_id="agent",
        agent_definition_revision="v1", command_type="submit_input",
        idempotency_key="request", payload=_payload(),
    )

    assert result.accepted
    assert result.runtime_owned is True
    assert [name for name, _ in database.calls] == [
        "get_agent_runtime_definition_fact",
        "submit_runtime_ingress_required_v1",
    ]


@pytest.mark.asyncio
async def test_ingress_rejects_incomplete_owner_binding_before_database() -> None:
    database = _Database({})
    with pytest.raises(RuntimeError, match="OWNER_BINDING_MISSING"):
        await RuntimeIngress(database).submit(
            conversation_id="conversation", org_id="org", user_id="user",
            scope_kind="user", scope_id="user", agent_definition_id="agent",
            agent_definition_revision="v1", command_type="submit_input",
            idempotency_key="request", payload={"channel": "web"},
        )
    assert database.calls == []


@pytest.mark.asyncio
async def test_ingress_preserves_required_owner_unavailability() -> None:
    database = _Database({
        "get_agent_runtime_definition_fact": {
            "definition_hash": "definition", "catalog_revision": "catalog",
        },
        "submit_runtime_ingress_required_v1": {
            "outcome": "runtime_required_unavailable", "runtime_owned": False,
        },
    })
    result = await RuntimeIngress(database).submit(
        conversation_id="conversation", org_id="org", user_id="user",
        scope_kind="user", scope_id="user", agent_definition_id="agent",
        agent_definition_revision="v1", command_type="submit_input",
        idempotency_key="request", payload=_payload(),
    )
    assert result.outcome == "runtime_required_unavailable"
    assert result.runtime_owned is False
