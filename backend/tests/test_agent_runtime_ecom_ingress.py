from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agent.runtime.ecom_ingress import RuntimeEcomModelIngress


@pytest.mark.asyncio
async def test_ecom_ingress_submits_to_shared_runtime_ingress(monkeypatch) -> None:
    runtime = AsyncMock()
    runtime.submit.return_value = SimpleNamespace(
        outcome="created", command_id="cmd-1", run_id="run-1",
        session_id="session-1", runtime_owned=True,
    )
    monkeypatch.setattr(
        "services.agent.runtime.ecom_ingress.RuntimeIngress",
        lambda *args, **kwargs: runtime,
    )
    ingress = RuntimeEcomModelIngress(object())

    receipt = await ingress.submit(
        conversation_id="conversation-1", org_id="org-1", user_id="user-1",
        scope_kind="user", scope_id="user-1", agent_definition_id="chat",
        agent_definition_revision="v1", input_message_id="input-1",
        output_message_id="output-1", idempotency_key="ecom-1",
        model_id="qwen-vl-max", messages=[{"role": "user", "content": "x"}],
        feature="ecom_plan", source_id="project-1",
    )

    assert receipt.accepted is True
    assert receipt.runtime_owned is True
    runtime.submit.assert_awaited_once()
    assert runtime.submit.await_args.kwargs["command_type"] == "ecom_model_request"
    assert runtime.submit.await_args.kwargs["payload"]["feature"] == "ecom_plan"


@pytest.mark.asyncio
async def test_ecom_ingress_rejects_unknown_feature() -> None:
    ingress = RuntimeEcomModelIngress.__new__(RuntimeEcomModelIngress)
    with pytest.raises(ValueError, match="FEATURE_INVALID"):
        await ingress.submit(
            conversation_id="c", org_id=None, user_id="u", scope_kind="user",
            scope_id="u", agent_definition_id="chat", agent_definition_revision="v1",
            input_message_id="i", output_message_id="o", idempotency_key="k",
            model_id="m", messages=[{"role": "user", "content": "x"}],
            feature="unknown", source_id="s",
        )
