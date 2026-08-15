from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.agent.runtime.ingress import RuntimeIngress
from services.agent.runtime.model_resolution import resolve_runtime_model
from services.agent.runtime.production_model import _resolve_model_selection


def test_explicit_valid_model_is_resolved_without_subscription_lookup() -> None:
    resolution = resolve_runtime_model("deepseek-v3.2")

    assert resolution.model_id == "deepseek-v3.2"
    assert resolution.source == "explicit"
    assert resolution.subscription_state == "not_used"


@pytest.mark.parametrize("value", [None, "", "auto", "unknown-model"])
def test_missing_or_invalid_model_uses_chat_default(value: object) -> None:
    resolution = resolve_runtime_model(value)

    from services.adapters.factory import DEFAULT_MODEL_ID

    assert resolution.model_id == DEFAULT_MODEL_ID
    assert resolution.source == "default"


def test_model_loop_uses_frozen_run_snapshot_before_definition_policy() -> None:
    snapshot = SimpleNamespace(run={
        "config_snapshot": {
            "resolved_model": {
                "model_id": "deepseek-v3.2",
                "provider": resolve_runtime_model("deepseek-v3.2").provider,
                "revision": resolve_runtime_model("deepseek-v3.2").revision,
            },
        },
    })
    definition = SimpleNamespace(model_policy={"model_id": "qwen3.5-plus"})

    resolution = _resolve_model_selection(
        snapshot, context={"task": {"model_id": "qwen3.5-plus"}},
        definition=definition,
    )

    assert resolution.model_id == "deepseek-v3.2"


def test_model_loop_compatibly_reads_existing_task_model_without_snapshot() -> None:
    snapshot = SimpleNamespace(run={"config_snapshot": {}})
    definition = SimpleNamespace(model_policy={"model_id": "qwen3.5-plus"})

    resolution = _resolve_model_selection(
        snapshot, context={"task": {"model_id": "deepseek-v3.2"}},
        definition=definition,
    )

    assert resolution.model_id == "deepseek-v3.2"


class _Rpc:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return SimpleNamespace(data=self.data)


class _Database:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        if name == "get_agent_runtime_definition_fact":
            return _Rpc({
                "definition_hash": "definition",
                "catalog_revision": "catalog",
            })
        return _Rpc({
            "outcome": "marked", "runtime_owned": True,
            "entity_id": "command", "result_entity_id": "run",
        })


@pytest.mark.asyncio
async def test_ingress_freezes_resolved_model_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.config.get_settings",
        lambda: SimpleNamespace(agent_runtime_release_revision="release"),
    )
    database = _Database()

    await RuntimeIngress(database).submit(
        conversation_id="conversation", org_id="org", user_id="user",
        scope_kind="user", scope_id="user", agent_definition_id="agent",
        agent_definition_revision="v1", command_type="submit_input",
        idempotency_key="request", payload={
            "channel": "web", "task_id": "task", "client_task_id": "client",
            "input_message_id": "message", "output_message_id": "output",
            "turn_id": "turn", "request_id": "request",
            "model_id": "deepseek-v3.2",
        },
    )

    assert [name for name, _ in database.calls] == [
        "get_agent_runtime_definition_fact",
        "submit_runtime_ingress_required_v1",
    ]
    config = database.calls[1][1]["p_config_snapshot"]
    assert config["resolved_model"]["model_id"] == "deepseek-v3.2"
    assert config["provider"] == config["resolved_model"]["provider"]
    assert config["revision"] == config["resolved_model"]["revision"]
    assert config["subscription_state"] == "not_used"
