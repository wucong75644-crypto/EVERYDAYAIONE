from __future__ import annotations

import asyncio
import json
import struct
from copy import deepcopy
from uuid import UUID

import pytest

from services.agent.runtime.model_gateway.protocol import (
    MAX_JSON_DEPTH,
    MAX_MESSAGES,
    MAX_REQUEST_BYTES,
    MAX_STRING_BYTES,
    MAX_TOOLS,
    REQUEST_FRAME_LIMIT,
    RESPONSE_FRAME_LIMIT,
    VERSION,
    GatewayProtocolError,
    decode_payload,
    encode_frame,
    read_frame,
    validate_request,
    validate_response,
)


def request_payload(seed: int = 1) -> dict[str, object]:
    def uid(offset: int) -> str:
        return str(UUID(int=seed * 100 + offset))

    return {
        "version": VERSION,
        "type": "request",
        "operation": "model.complete",
        "request_id": uid(1),
        "org_id": uid(2),
        "user_id": uid(3),
        "run_id": uid(4),
        "model_step_id": uid(5),
        "model_attempt_id": uid(6),
        "worker_id": "runtime-worker-1",
        "execution_token": uid(7),
        "request_hash": "a" * 64,
        "state_version": 1,
        "model_id": "model-v1",
        "provider": "fake",
        "model_revision": "revision-1",
        "purpose": "model.invoke",
        "tenant_kill_epoch": 0,
        "provider_kill_epoch": 0,
        "capability_kill_epoch": 0,
        "deadline_ms": 1000,
        "input": {
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "function": {"name": "safe_read"}}],
            "options": {"temperature": 0},
            "context_receipt_hash": "b" * 64,
        },
    }


def error_code(callable_) -> str:
    with pytest.raises(GatewayProtocolError) as caught:
        callable_()
    return caught.value.code


def test_request_frame_roundtrip_and_fixed_header() -> None:
    request = request_payload()
    frame = encode_frame(request)
    assert struct.unpack(">I", frame[:4])[0] == len(frame) - 4
    assert validate_request(decode_payload(frame[4:])) == request


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda value: value.update(extra=True), "GATEWAY_INVALID_REQUEST_FIELDS"),
        (lambda value: value.update(version="v0"), "GATEWAY_UNSUPPORTED_PROTOCOL"),
        (lambda value: value.update(version="agent-model-gateway.v1"), "GATEWAY_UNSUPPORTED_PROTOCOL"),
        (lambda value: value.update(type="response"), "GATEWAY_UNSUPPORTED_PROTOCOL"),
        (lambda value: value.update(operation="model.stream"), "GATEWAY_UNSUPPORTED_OPERATION"),
        (lambda value: value.update(request_id="bad"), "GATEWAY_INVALID_REQUEST_ID"),
        (lambda value: value.update(request_hash="bad"), "GATEWAY_INVALID_REQUEST_HASH"),
        (lambda value: value.update(deadline_ms=120001), "GATEWAY_INVALID_DEADLINE"),
        (lambda value: value["input"].update(extra=True), "GATEWAY_INVALID_INPUT_FIELDS"),
    ],
)
def test_request_rejects_invalid_contract(mutation, expected: str) -> None:
    value = request_payload()
    mutation(value)
    assert error_code(lambda: validate_request(value)) == expected


@pytest.mark.parametrize(
    "field",
    ["api_key", "Authorization", "kek", "SecretReference", "encrypted_envelope",
     "credential_lease", "credential_handle", "database_url", "access_token"],
)
def test_forbidden_secret_fields_are_rejected_recursively(field: str) -> None:
    value = request_payload()
    value["input"]["options"] = {"nested": [{field: "never-log-this"}]}
    assert error_code(lambda: validate_request(value)) == "GATEWAY_FORBIDDEN_SECRET_FIELD"
    assert "never-log-this" not in repr(pytest.raises)


def test_execution_token_is_allowed_but_protocol_error_repr_has_no_request() -> None:
    assert validate_request(request_payload())["execution_token"]
    value = request_payload()
    value["input"]["options"] = {"api_key": "sensitive-value"}
    with pytest.raises(GatewayProtocolError) as caught:
        validate_request(value)
    assert "sensitive-value" not in repr(caught.value)


def test_json_depth_string_message_and_tool_limits() -> None:
    deep: object = "leaf"
    for _ in range(MAX_JSON_DEPTH + 1):
        deep = {"safe": deep}
    value = request_payload()
    value["input"]["options"] = deep
    assert error_code(lambda: validate_request(value)) == "GATEWAY_JSON_DEPTH_EXCEEDED"

    value = request_payload()
    value["input"]["messages"][0]["content"] = "x" * (MAX_STRING_BYTES + 1)
    assert error_code(lambda: validate_request(value)) == "GATEWAY_STRING_TOO_LARGE"

    value = request_payload()
    value["input"]["messages"] = [{}] * (MAX_MESSAGES + 1)
    assert error_code(lambda: validate_request(value)) == "GATEWAY_MESSAGES_LIMIT_EXCEEDED"

    value = request_payload()
    value["input"]["tools"] = [{}] * (MAX_TOOLS + 1)
    assert error_code(lambda: validate_request(value)) == "GATEWAY_TOOLS_LIMIT_EXCEEDED"


def test_request_cumulative_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.agent.runtime.model_gateway.protocol.MAX_REQUEST_BYTES", 128,
    )
    assert error_code(lambda: validate_request(request_payload())) == "GATEWAY_REQUEST_TOO_LARGE"


def test_invalid_utf8_json_duplicate_and_oversize_frame_are_rejected() -> None:
    assert error_code(lambda: decode_payload(b"\xff")) == "GATEWAY_INVALID_UTF8"
    assert error_code(lambda: decode_payload(b"{")) == "GATEWAY_INVALID_JSON"
    duplicate = b'{"safe":1,"safe":2}'
    assert error_code(lambda: decode_payload(duplicate)) == "GATEWAY_DUPLICATE_JSON_FIELD"
    assert error_code(
        lambda: encode_frame({"safe": "x" * RESPONSE_FRAME_LIMIT})
    ) == "GATEWAY_FRAME_TOO_LARGE"


@pytest.mark.asyncio
async def test_partial_frame_and_eof() -> None:
    reader = asyncio.StreamReader()
    frame = encode_frame(request_payload())
    reader.feed_data(frame[:2])
    reader.feed_eof()
    with pytest.raises(GatewayProtocolError, match="GATEWAY_UNEXPECTED_EOF"):
        await read_frame(reader)

    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack(">I", RESPONSE_FRAME_LIMIT + 1))
    with pytest.raises(GatewayProtocolError, match="GATEWAY_FRAME_TOO_LARGE"):
        await read_frame(reader)


def test_response_sequence_id_and_delta_whitelist() -> None:
    request_id = request_payload()["request_id"]
    accepted = {
        "version": VERSION, "request_id": request_id, "sequence": 0,
        "type": "accepted", "operation_id": str(UUID(int=900)), "status": "claimed",
    }
    assert validate_response(accepted, request_id=request_id, expected_sequence=0) == accepted
    old = deepcopy(accepted)
    old["version"] = "agent-model-gateway.v1"
    assert error_code(
        lambda: validate_response(old, request_id=request_id, expected_sequence=0)
    ) == "GATEWAY_UNSUPPORTED_PROTOCOL"
    wrong = deepcopy(accepted)
    wrong["sequence"] = 2
    assert error_code(lambda: validate_response(wrong, request_id=request_id, expected_sequence=1)) == "GATEWAY_SEQUENCE_MISMATCH"
    wrong = deepcopy(accepted)
    wrong["request_id"] = str(UUID(int=901))
    assert error_code(lambda: validate_response(wrong, request_id=request_id, expected_sequence=0)) == "GATEWAY_REQUEST_ID_MISMATCH"
    delta = {
        "version": VERSION, "request_id": request_id, "sequence": 1, "type": "delta",
        "delta_kind": "provider_metadata", "delta": {"raw_payload": "forbidden"},
    }
    assert error_code(lambda: validate_response(delta, request_id=request_id, expected_sequence=1)) == "GATEWAY_INVALID_PROVIDER_METADATA"
    delta.update(delta_kind="text", delta={"text": "ok", "raw_chunk": "forbidden"})
    assert error_code(lambda: validate_response(delta, request_id=request_id, expected_sequence=1)) == "GATEWAY_INVALID_TEXT_DELTA"

    wrong_type = deepcopy(accepted)
    wrong_type["type"] = []
    assert error_code(
        lambda: validate_response(wrong_type, request_id=request_id, expected_sequence=0)
    ) == "GATEWAY_INVALID_RESPONSE_FIELDS"
    delta.update(delta_kind=[], delta={})
    assert error_code(
        lambda: validate_response(delta, request_id=request_id, expected_sequence=1)
    ) == "GATEWAY_INVALID_DELTA"


def test_failed_response_is_stable_and_secret_free() -> None:
    request_id = request_payload()["request_id"]
    response = {
        "version": VERSION, "request_id": request_id, "sequence": 0, "type": "failed",
        "error_code": "GATEWAY_PROVIDER_UNAVAILABLE", "retry_class": "terminal",
        "summary": "provider unavailable",
    }
    assert validate_response(response, request_id=request_id, expected_sequence=0) == response
    encoded = json.dumps(response)
    assert "api_key" not in encoded and "execution_token" not in encoded


def test_nullable_org_preserves_required_user_identity() -> None:
    personal = request_payload()
    personal["org_id"] = None
    assert validate_request(personal)["org_id"] is None
    personal["user_id"] = None
    assert error_code(lambda: validate_request(personal)) == "GATEWAY_INVALID_USER_ID"


def test_request_and_response_frame_limits_are_distinct() -> None:
    assert REQUEST_FRAME_LIMIT == MAX_REQUEST_BYTES == 4 * RESPONSE_FRAME_LIMIT


@pytest.mark.parametrize(
    ("kind", "delta", "expected"),
    [
        ("text", {"text": 3}, "GATEWAY_INVALID_TEXT_DELTA"),
        ("tool_call", {}, "GATEWAY_INVALID_TOOL_CALL_DELTA"),
        ("tool_call", {"index": True, "name": "x"}, "GATEWAY_INVALID_TOOL_CALL_DELTA"),
        ("tool_call", {"index": -1, "arguments": "{}"}, "GATEWAY_INVALID_TOOL_CALL_DELTA"),
        ("tool_call", {"index": 0, "name": 5}, "GATEWAY_INVALID_TOOL_CALL_DELTA"),
        ("usage", {}, "GATEWAY_INVALID_USAGE_DELTA"),
        ("usage", {"input_tokens": True}, "GATEWAY_INVALID_USAGE_DELTA"),
        ("usage", {"input_tokens": -1}, "GATEWAY_INVALID_USAGE_DELTA"),
        ("usage", {"raw_tokens": 1}, "GATEWAY_INVALID_USAGE_DELTA"),
        ("provider_metadata", {}, "GATEWAY_INVALID_PROVIDER_METADATA"),
        ("provider_metadata", {"provider_stop_reason": 1}, "GATEWAY_INVALID_PROVIDER_METADATA"),
    ],
)
def test_delta_payloads_are_strict(kind: str, delta: object, expected: str) -> None:
    request_id = request_payload()["request_id"]
    response = {
        "version": VERSION, "request_id": request_id, "sequence": 1, "type": "delta",
        "delta_kind": kind, "delta": delta,
    }
    assert error_code(
        lambda: validate_response(response, request_id=request_id, expected_sequence=1)
    ) == expected


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("tool_calls", [{"id": "missing-fields"}], "GATEWAY_INVALID_COMPLETED_TOOL_CALLS"),
        ("tool_calls", [{"index": True, "call_id": "1", "provider_call_id": None,
                          "name": "x", "arguments": "{}"}],
         "GATEWAY_INVALID_COMPLETED_TOOL_CALLS"),
        ("tool_calls", [
            {"index": 0, "call_id": "same", "provider_call_id": None,
             "name": "x", "arguments": "{}"},
            {"index": 1, "call_id": "same", "provider_call_id": "provider-2",
             "name": "y", "arguments": "{}"},
        ], "GATEWAY_INVALID_COMPLETED_TOOL_CALLS"),
        ("usage", {"input_tokens": True}, "GATEWAY_INVALID_COMPLETED_USAGE"),
        ("usage", {"raw": 1}, "GATEWAY_INVALID_COMPLETED_USAGE"),
        ("stop_reason", "not-stable", "GATEWAY_INVALID_STOP_REASON"),
        ("provider_stop_reason", 4, "GATEWAY_INVALID_PROVIDER_STOP_REASON"),
        ("provider_request_id", 4, "GATEWAY_INVALID_PROVIDER_REQUEST_ID"),
        ("operation_state_version", True, "GATEWAY_INVALID_STATE_VERSION"),
    ],
)
def test_completed_payload_is_strict(field: str, value: object, expected: str) -> None:
    request_id = request_payload()["request_id"]
    response = {
        "version": VERSION, "request_id": request_id, "sequence": 1, "type": "completed",
        "text": "ok", "tool_calls": [], "usage": {}, "stop_reason": "final",
        "provider_stop_reason": "stop",
        "provider_request_id": None, "response_hash": "c" * 64,
        "operation_state_version": 1,
    }
    response[field] = value
    assert error_code(
        lambda: validate_response(response, request_id=request_id, expected_sequence=1)
    ) == expected


@pytest.mark.parametrize(
    ("response_type", "field", "value", "expected"),
    [
        ("failed", "error_code", "bad-code", "GATEWAY_INVALID_ERROR_CODE"),
        ("failed", "summary", "/private/secret", "GATEWAY_INVALID_ERROR_SUMMARY"),
        ("unknown", "ambiguity_kind", "disconnect", "GATEWAY_INVALID_AMBIGUITY_KIND"),
        ("unknown", "response_started", 1, "GATEWAY_INVALID_UNKNOWN_RESULT"),
    ],
)
def test_terminal_codes_and_types_are_strict(
    response_type: str, field: str, value: object, expected: str,
) -> None:
    request_id = request_payload()["request_id"]
    if response_type == "failed":
        response = {
            "version": VERSION, "request_id": request_id, "sequence": 0, "type": "failed",
            "error_code": "GATEWAY_FAILED", "retry_class": "terminal", "summary": "request failed",
        }
        sequence = 0
    else:
        response = {
            "version": VERSION, "request_id": request_id, "sequence": 1, "type": "unknown",
            "ambiguity_kind": "GATEWAY_DISCONNECT", "response_started": False,
            "provider_request_id": None, "reconcile_only": True,
        }
        sequence = 1
    response[field] = value
    assert error_code(
        lambda: validate_response(response, request_id=request_id, expected_sequence=sequence)
    ) == expected
