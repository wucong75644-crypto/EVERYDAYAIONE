"""C7-R2 canonical Model Gateway result integrity contracts."""

from __future__ import annotations

import pytest

from services.agent.runtime.domain import StopReason
from services.agent.runtime.ports.model import ModelCallUnknownError
from services.agent.runtime.production_model import _actions
from tests.test_agent_runtime_c7_bg4_runtime_gateway import (
    IDS,
    _accepted,
    _binding,
    _client,
    _completed,
    _operation,
    _request,
    _Repository,
    _sync_completed_fact,
    _Transport,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_call_id", (None, "provider-call-distinct"))
async def test_runtime_preserves_both_tool_call_ids(
    provider_call_id: str | None,
) -> None:
    frame, operation = _completed(), _operation()
    frame["tool_calls"][0]["provider_call_id"] = provider_call_id
    _sync_completed_fact(frame, operation)
    result = await _client(
        _Repository(operation), _Transport((_accepted(), frame)),
    ).complete(_request(_binding()))
    assert result.tool_calls[0].call_id == "runtime-call-1"
    assert result.tool_calls[0].provider_call_id == provider_call_id


@pytest.mark.asyncio
async def test_tool_call_without_provider_stop_reason_stays_tool_calls() -> None:
    frame, operation = _completed(), _operation()
    frame["provider_stop_reason"] = None
    _sync_completed_fact(frame, operation)
    result = await _client(
        _Repository(operation), _Transport((_accepted(), frame)),
    ).complete(_request(_binding()))
    assert result.stop_reason is StopReason.TOOL_CALLS
    assert result.provider_stop_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("with_tool_call", (False, True))
async def test_explicit_refusal_is_never_remapped_or_executed(
    with_tool_call: bool,
) -> None:
    frame, operation = _completed(), _operation()
    frame["stop_reason"] = "model_refusal"
    if not with_tool_call:
        frame["tool_calls"] = []
    _sync_completed_fact(frame, operation)
    result = await _client(
        _Repository(operation), _Transport((_accepted(), frame)),
    ).complete(_request(_binding()))
    assert result.stop_reason is StopReason.MODEL_REFUSAL
    assert bool(result.tool_calls) is with_tool_call
    if with_tool_call:
        assert _actions(result, IDS["run_id"], object())[1] == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    (
        lambda frame: frame.update(stop_reason="model_refusal"),
        lambda frame: frame.update(provider_stop_reason="stop"),
        lambda frame: frame.update(text="tampered"),
        lambda frame: frame["tool_calls"][0].update(arguments='{"query":"tampered"}'),
        lambda frame: frame["tool_calls"][0].update(call_id="runtime-tampered"),
        lambda frame: frame["tool_calls"][0].update(provider_call_id=None),
        lambda frame: frame["tool_calls"][0].update(name="tampered"),
        lambda frame: frame["tool_calls"][0].update(index=1),
        lambda frame: frame["usage"].update(input_tokens=12),
        lambda frame: frame["usage"].update(output_tokens=6),
        lambda frame: frame["usage"].update(cache_read_tokens=4),
        lambda frame: frame["usage"].update(cache_write_tokens=1),
    ),
)
async def test_any_wire_canonical_result_tamper_becomes_unknown(mutate) -> None:
    frame = _completed()
    mutate(frame)
    with pytest.raises(ModelCallUnknownError):
        await _client(
            _Repository(_operation()), _Transport((_accepted(), frame)),
        ).complete(_request(_binding()))


@pytest.mark.asyncio
@pytest.mark.parametrize("db_tamper", ("response_hash", "reasoning_tokens"))
async def test_db_hash_or_reasoning_usage_tamper_becomes_unknown(db_tamper: str) -> None:
    operation = _operation()
    if db_tamper == "response_hash":
        operation["response_hash"] = "f" * 64
    else:
        operation["usage_summary"]["reasoning_tokens"] = 3
    with pytest.raises(ModelCallUnknownError):
        await _client(
            _Repository(operation), _Transport((_accepted(), _completed())),
        ).complete(_request(_binding()))


@pytest.mark.asyncio
async def test_self_consistent_hash_still_obeys_stop_output_tool_contract() -> None:
    frame, operation = _completed(), _operation()
    frame["stop_reason"] = "final"
    _sync_completed_fact(frame, operation)
    with pytest.raises(ModelCallUnknownError):
        await _client(
            _Repository(operation), _Transport((_accepted(), frame)),
        ).complete(_request(_binding()))
