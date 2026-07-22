"""统一生成生命周期服务测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.exceptions import AppException
from services.generation_lifecycle import (
    GenerationLifecycle,
    GenerationPreparation,
)


class _RPC:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return SimpleNamespace(data=self._data)


class _FailingRPC:
    def __init__(self, error: Exception):
        self._error = error

    def execute(self):
        raise self._error


class _FailingDB:
    def __init__(self, error: Exception):
        self.error = error

    def rpc(self, name, params):
        return _FailingRPC(self.error)


class _DB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RPC(self.responses.pop(0))


def _preparation_data(**overrides):
    data = {
        "request_id": "request-1",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "input_message_id": "input-1",
        "output_message_id": "output-1",
        "base_context_revision": 4,
        "context_through_message_id": "through-1",
        "task_ids": ["task-1"],
        "already_prepared": False,
    }
    data.update(overrides)
    return data


def test_prepare_serializes_payload_and_returns_authoritative_anchor() -> None:
    db = _DB([_preparation_data()])
    lifecycle = GenerationLifecycle(db)

    result = lifecycle.prepare(
        request_id="request-1",
        operation="send",
        conversation_id="conversation-1",
        user_id="user-1",
        org_id="org-1",
        turn_id="turn-1",
        input_message={"id": "input-1", "content": []},
        output_message={"id": "output-1", "content": []},
        tasks=[{"id": "task-1", "type": "image"}],
    )

    assert result.task_ids == ("task-1",)
    assert result.base_context_revision == 4
    name, params = db.calls[0]
    assert name == "prepare_generation"
    assert params["p_input_message"].obj["id"] == "input-1"
    assert params["p_tasks"].obj == [{"id": "task-1", "type": "image"}]
    anchor = result.context_anchor("task-1", "org-1")
    assert anchor.turn_id == "turn-1"
    assert anchor.base_revision == 4


@pytest.mark.parametrize(
    "overrides",
    [
        {"turn_id": None},
        {"base_context_revision": -1},
        {"task_ids": []},
        {"task_ids": [str(index) for index in range(17)]},
        {"already_prepared": "false"},
    ],
)
def test_preparation_rejects_invalid_rpc_results(overrides) -> None:
    with pytest.raises(RuntimeError, match="GENERATION_PREPARE_RESULT_INVALID"):
        GenerationPreparation.from_rpc(_preparation_data(**overrides))


def test_prepare_rejects_invalid_task_count_before_rpc() -> None:
    db = _DB([])
    lifecycle = GenerationLifecycle(db)

    with pytest.raises(ValueError, match="GENERATION_PREPARE_TASK_COUNT_INVALID"):
        lifecycle.prepare(
            request_id="request-1",
            operation="send",
            conversation_id="conversation-1",
            user_id="user-1",
            org_id=None,
            turn_id="turn-1",
            input_message={"id": "input-1"},
            output_message={"id": "output-1"},
            tasks=[],
        )

    assert db.calls == []


@pytest.mark.parametrize(
    "marker",
    [
        "TURN_MESSAGE_RELATION_MISMATCH",
        "GENERATION_PREPARE_MESSAGE_CONFLICT",
        "GENERATION_PREPARE_TASK_CONFLICT",
        "GENERATION_PREPARE_TURN_CONFLICT",
        "GENERATION_PREPARE_REQUEST_MISMATCH",
        "GENERATION_PREPARE_ANCHOR_MISSING",
    ],
)
def test_prepare_maps_declared_database_conflicts_to_http_409(marker) -> None:
    lifecycle = GenerationLifecycle(_FailingDB(RuntimeError(f"database: {marker}")))

    with pytest.raises(AppException) as caught:
        lifecycle.prepare(
            request_id="request-1", operation="send",
            conversation_id="conversation-1", user_id="user-1", org_id=None,
            turn_id="turn-1", input_message={"id": "input-1"},
            output_message={"id": "output-1"},
            tasks=[{"id": "task-1", "type": "chat"}],
        )

    assert caught.value.code == "GENERATION_PREPARE_CONFLICT"
    assert caught.value.status_code == 409
    assert marker not in caught.value.message


def test_prepare_preserves_unknown_database_failure() -> None:
    original = RuntimeError("connection reset")
    lifecycle = GenerationLifecycle(_FailingDB(original))

    with pytest.raises(RuntimeError) as caught:
        lifecycle.prepare(
            request_id="request-1", operation="send",
            conversation_id="conversation-1", user_id="user-1", org_id=None,
            turn_id="turn-1", input_message={"id": "input-1"},
            output_message={"id": "output-1"},
            tasks=[{"id": "task-1", "type": "chat"}],
        )

    assert caught.value is original


def test_context_anchor_rejects_unknown_task() -> None:
    preparation = GenerationPreparation.from_rpc(_preparation_data())

    with pytest.raises(ValueError, match="GENERATION_PREPARE_TASK_ID_UNKNOWN"):
        preparation.context_anchor("missing", None)


def test_attach_external_task_returns_typed_result() -> None:
    db = _DB([{"task_id": "task-1", "already_attached": True}])
    lifecycle = GenerationLifecycle(db)

    result = lifecycle.attach_external_task(
        task_id="task-1",
        external_task_id="external-1",
        credit_transaction_id="transaction-1",
        org_id="org-1",
        user_id="user-1",
        provider="kie",
        actual_model_id="retry-model",
        actual_request_params={"prompt": "cat", "_retried": True},
    )

    assert result.task_id == "task-1"
    assert result.already_attached is True
    assert db.calls[0][0] == "attach_generation_external_task"
    assert db.calls[0][1]["p_actual_model_id"] == "retry-model"
    assert db.calls[0][1]["p_actual_request_params"].obj["_retried"] is True


def test_fail_prepared_task_returns_typed_result() -> None:
    db = _DB([{"task_id": "task-1", "already_failed": False}])
    lifecycle = GenerationLifecycle(db)

    result = lifecycle.fail_prepared_task(
        task_id="task-1",
        terminal_reason="provider_rejected",
        error_message="rejected",
        org_id=None,
        user_id="user-1",
    )

    assert result.task_id == "task-1"
    assert result.already_failed is False
    assert db.calls[0][0] == "fail_prepared_generation_task"


@pytest.mark.parametrize("method", ["attach", "fail"])
def test_transition_rejects_invalid_rpc_result(method) -> None:
    lifecycle = GenerationLifecycle(_DB([{"task_id": "task-1"}]))

    with pytest.raises(RuntimeError, match="GENERATION_TRANSITION_RESULT_INVALID"):
        if method == "attach":
            lifecycle.attach_external_task(
                task_id="task-1",
                external_task_id="external-1",
                credit_transaction_id=None,
                org_id=None,
                user_id="user-1",
                provider="kie",
            )
        else:
            lifecycle.fail_prepared_task(
                task_id="task-1",
                terminal_reason="provider_rejected",
                error_message=None,
                org_id=None,
                user_id="user-1",
            )


def test_prepare_rejects_non_mapping_rpc_result() -> None:
    lifecycle = GenerationLifecycle(_DB([None]))

    with pytest.raises(RuntimeError, match="GENERATION_PREPARE_RESULT_INVALID"):
        lifecycle.prepare(
            request_id="request-1",
            operation="send",
            conversation_id="conversation-1",
            user_id="user-1",
            org_id=None,
            turn_id="turn-1",
            input_message={"id": "input-1"},
            output_message={"id": "output-1"},
            tasks=[{"id": "task-1", "type": "chat"}],
        )


def test_transition_rejects_mismatched_task_id() -> None:
    lifecycle = GenerationLifecycle(
        _DB([{"task_id": "different", "already_attached": False}])
    )

    with pytest.raises(RuntimeError, match="GENERATION_TRANSITION_RESULT_INVALID"):
        lifecycle.attach_external_task(
            task_id="task-1",
            external_task_id="external-1",
            credit_transaction_id=None,
            org_id=None,
            user_id="user-1",
            provider="kie",
        )
