from __future__ import annotations

import asyncio
import json
import struct
from copy import deepcopy
from uuid import UUID

import pytest

from services.agent.runtime.model_gateway.protocol import (
    MAX_FRAME_BYTES,
    MAX_JSON_DEPTH,
    MAX_MESSAGES,
    MAX_STRING_BYTES,
    MAX_TOOLS,
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
    assert error_code(lambda: encode_frame({"safe": "x" * MAX_FRAME_BYTES})) == "GATEWAY_FRAME_TOO_LARGE"


@pytest.mark.asyncio
async def test_partial_frame_and_eof() -> None:
    reader = asyncio.StreamReader()
    frame = encode_frame(request_payload())
    reader.feed_data(frame[:2])
    reader.feed_eof()
    with pytest.raises(GatewayProtocolError, match="GATEWAY_UNEXPECTED_EOF"):
        await read_frame(reader)

    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack(">I", MAX_FRAME_BYTES + 1))
    with pytest.raises(GatewayProtocolError, match="GATEWAY_FRAME_TOO_LARGE"):
        await read_frame(reader)


def test_response_sequence_id_and_delta_whitelist() -> None:
    request_id = request_payload()["request_id"]
    accepted = {
        "version": VERSION, "request_id": request_id, "sequence": 0,
        "type": "accepted", "operation_id": str(UUID(int=900)), "status": "claimed",
    }
    assert validate_response(accepted, request_id=request_id, expected_sequence=0) == accepted
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
