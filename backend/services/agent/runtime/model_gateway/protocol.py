"""Strict, secret-free framing and validation for model gateway UDS v1."""

from __future__ import annotations

import asyncio
import json
import math
import re
import struct
from collections.abc import Mapping
from typing import Any
from uuid import UUID

VERSION = "agent-model-gateway.v1"
PRODUCTION_READY = False
RESPONSE_FRAME_LIMIT = 1024 * 1024
REQUEST_FRAME_LIMIT = 4 * RESPONSE_FRAME_LIMIT
MAX_REQUEST_BYTES = REQUEST_FRAME_LIMIT
MAX_RESPONSE_BYTES = 16 * RESPONSE_FRAME_LIMIT
MAX_JSON_DEPTH = 32
MAX_STRING_BYTES = 512 * 1024
MAX_MESSAGES = 512
MAX_TOOLS = 128

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEY_PARTS = (
    "api_key", "apikey", "authorization", "kek", "secret", "envelope",
    "credential", "dsn", "password", "access_token", "refresh_token",
    "bearer_token", "database_url",
)
_REQUEST_FIELDS = frozenset({
    "version", "type", "operation", "request_id", "org_id", "user_id",
    "run_id", "model_step_id", "model_attempt_id", "worker_id",
    "execution_token", "request_hash", "state_version", "model_id",
    "provider", "model_revision", "purpose", "tenant_kill_epoch",
    "provider_kill_epoch", "capability_kill_epoch", "deadline_ms", "input",
})
_INPUT_FIELDS = frozenset({"messages", "tools", "options", "context_receipt_hash"})
_COMMON_RESPONSE = frozenset({"version", "request_id", "sequence", "type"})
_RESPONSE_FIELDS = {
    "accepted": _COMMON_RESPONSE | {"operation_id", "status"},
    "delta": _COMMON_RESPONSE | {"delta_kind", "delta"},
    "completed": _COMMON_RESPONSE | {
        "text", "tool_calls", "usage", "finish_reason", "provider_request_id",
        "response_hash", "operation_state_version",
    },
    "failed": _COMMON_RESPONSE | {"error_code", "retry_class", "summary"},
    "unknown": _COMMON_RESPONSE | {
        "ambiguity_kind", "response_started", "provider_request_id",
        "reconcile_only",
    },
}
_DELTA_KINDS = frozenset({"text", "tool_call", "usage", "provider_metadata"})
_PROVIDER_METADATA_FIELDS = frozenset({"provider_request_id", "finish_reason"})
_TOOL_CALL_DELTA_FIELDS = frozenset({"index", "id", "name", "arguments"})
_USAGE_DELTA_FIELDS = frozenset({
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "credits_minor",
})
_COMPLETED_TOOL_CALL_FIELDS = frozenset({"index", "id", "name", "arguments"})
_STABLE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


class GatewayProtocolError(Exception):
    """Protocol rejection carrying only a stable, non-sensitive code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"GatewayProtocolError(code={self.code!r})"


def _fail(code: str) -> None:
    raise GatewayProtocolError(code)


def _strict_object(value: Any, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(code)
    return value


def _text(value: Any, code: str, *, allow_empty: bool = False,
          maximum_bytes: int = MAX_STRING_BYTES) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        _fail(code)
    if len(value.encode("utf-8")) > maximum_bytes:
        _fail("GATEWAY_STRING_TOO_LARGE")
    return value


def _uuid(value: Any, code: str) -> str:
    text = _text(value, code)
    try:
        parsed = UUID(text)
    except (ValueError, AttributeError):
        _fail(code)
    if str(parsed) != text.lower():
        _fail(code)
    return text


def _integer(value: Any, code: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(code)
    if maximum is not None and value > maximum:
        _fail(code)
    return value


def _stable_code(value: Any, code: str) -> str:
    text = _text(value, code, maximum_bytes=128)
    if not _STABLE_CODE_RE.fullmatch(text):
        _fail(code)
    return text


def _optional_text(value: Any, code: str, *, maximum_bytes: int) -> None:
    if value is not None:
        _text(value, code, maximum_bytes=maximum_bytes)


def _scan_json(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        _fail("GATEWAY_JSON_DEPTH_EXCEEDED")
    if isinstance(value, str):
        _text(value, "GATEWAY_INVALID_STRING", allow_empty=True)
        return
    if isinstance(value, float) and not math.isfinite(value):
        _fail("GATEWAY_INVALID_JSON_NUMBER")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, list):
        for item in value:
            _scan_json(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("GATEWAY_INVALID_JSON_OBJECT")
            normalized = key.lower().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                _fail("GATEWAY_FORBIDDEN_SECRET_FIELD")
            _scan_json(item, depth=depth + 1)
        return
    _fail("GATEWAY_INVALID_JSON_VALUE")


def validate_request(value: Any) -> dict[str, Any]:
    request = _strict_object(value, _REQUEST_FIELDS, "GATEWAY_INVALID_REQUEST_FIELDS")
    if request["version"] != VERSION or request["type"] != "request":
        _fail("GATEWAY_UNSUPPORTED_PROTOCOL")
    if request["operation"] != "model.complete":
        _fail("GATEWAY_UNSUPPORTED_OPERATION")
    for field in ("request_id", "user_id", "run_id", "model_step_id",
                  "model_attempt_id", "execution_token"):
        _uuid(request[field], f"GATEWAY_INVALID_{field.upper()}")
    if request["org_id"] is not None:
        _uuid(request["org_id"], "GATEWAY_INVALID_ORG_ID")
    for field in ("worker_id", "model_id", "provider", "model_revision"):
        _text(request[field], f"GATEWAY_INVALID_{field.upper()}")
    if request["purpose"] != "model.invoke":
        _fail("GATEWAY_INVALID_PURPOSE")
    if not isinstance(request["request_hash"], str) or not _HASH_RE.fullmatch(request["request_hash"]):
        _fail("GATEWAY_INVALID_REQUEST_HASH")
    for field in ("state_version", "tenant_kill_epoch", "provider_kill_epoch",
                  "capability_kill_epoch"):
        _integer(request[field], f"GATEWAY_INVALID_{field.upper()}")
    _integer(request["deadline_ms"], "GATEWAY_INVALID_DEADLINE", minimum=1, maximum=120_000)
    input_value = _strict_object(request["input"], _INPUT_FIELDS, "GATEWAY_INVALID_INPUT_FIELDS")
    if not isinstance(input_value["messages"], list) or len(input_value["messages"]) > MAX_MESSAGES:
        _fail("GATEWAY_MESSAGES_LIMIT_EXCEEDED")
    if not isinstance(input_value["tools"], list) or len(input_value["tools"]) > MAX_TOOLS:
        _fail("GATEWAY_TOOLS_LIMIT_EXCEEDED")
    if not isinstance(input_value["options"], dict):
        _fail("GATEWAY_INVALID_OPTIONS")
    if not isinstance(input_value["context_receipt_hash"], str) or not _HASH_RE.fullmatch(
        input_value["context_receipt_hash"]
    ):
        _fail("GATEWAY_INVALID_CONTEXT_RECEIPT_HASH")
    _scan_json(request)
    try:
        request_size = len(json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        _fail("GATEWAY_JSON_ENCODE_FAILED")
    if request_size > MAX_REQUEST_BYTES:
        _fail("GATEWAY_REQUEST_TOO_LARGE")
    return request


def validate_response(value: Any, *, request_id: str, expected_sequence: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("GATEWAY_INVALID_RESPONSE")
    response_type = value.get("type")
    if not isinstance(response_type, str):
        _fail("GATEWAY_INVALID_RESPONSE_FIELDS")
    fields = _RESPONSE_FIELDS.get(response_type)
    if fields is None or set(value) != fields:
        _fail("GATEWAY_INVALID_RESPONSE_FIELDS")
    if value["version"] != VERSION:
        _fail("GATEWAY_UNSUPPORTED_PROTOCOL")
    if value["request_id"] != request_id:
        _fail("GATEWAY_REQUEST_ID_MISMATCH")
    if _integer(value["sequence"], "GATEWAY_INVALID_SEQUENCE") != expected_sequence:
        _fail("GATEWAY_SEQUENCE_MISMATCH")
    if expected_sequence == 0 and response_type not in {"accepted", "failed"}:
        _fail("GATEWAY_ACCEPTED_REQUIRED")
    if expected_sequence > 0 and response_type == "accepted":
        _fail("GATEWAY_DUPLICATE_ACCEPTED")
    if response_type == "accepted":
        _uuid(value["operation_id"], "GATEWAY_INVALID_OPERATION_ID")
        if value["status"] not in {"claimed", "readback"}:
            _fail("GATEWAY_INVALID_ACCEPTED_STATUS")
    elif response_type == "delta":
        if (not isinstance(value["delta_kind"], str)
                or value["delta_kind"] not in _DELTA_KINDS
                or not isinstance(value["delta"], dict)):
            _fail("GATEWAY_INVALID_DELTA")
        _validate_delta(value["delta_kind"], value["delta"])
    elif response_type == "completed":
        _integer(value["operation_state_version"], "GATEWAY_INVALID_STATE_VERSION", minimum=1)
        if not _HASH_RE.fullmatch(_text(value["response_hash"], "GATEWAY_INVALID_RESPONSE_HASH")):
            _fail("GATEWAY_INVALID_RESPONSE_HASH")
        _validate_completed_tool_calls(value["tool_calls"])
        _validate_usage(value["usage"], "GATEWAY_INVALID_COMPLETED_USAGE", allow_empty=True)
        _text(value["text"], "GATEWAY_INVALID_COMPLETED_RESULT", allow_empty=True)
        _text(value["finish_reason"], "GATEWAY_INVALID_FINISH_REASON", maximum_bytes=256)
        _optional_text(
            value["provider_request_id"], "GATEWAY_INVALID_PROVIDER_REQUEST_ID", maximum_bytes=512,
        )
    elif response_type == "failed":
        _stable_code(value["error_code"], "GATEWAY_INVALID_ERROR_CODE")
        if value["retry_class"] != "terminal":
            _fail("GATEWAY_INVALID_RETRY_CLASS")
        summary = _text(value["summary"], "GATEWAY_INVALID_ERROR_SUMMARY", allow_empty=True)
        if (len(summary) > 128 or not re.fullmatch(r"[a-z0-9 _-]*", summary)
                or any(part in summary for part in ("secret", "token", "password", "api key", "dsn"))):
            _fail("GATEWAY_INVALID_ERROR_SUMMARY")
    else:
        _stable_code(value["ambiguity_kind"], "GATEWAY_INVALID_AMBIGUITY_KIND")
        _optional_text(
            value["provider_request_id"], "GATEWAY_INVALID_PROVIDER_REQUEST_ID", maximum_bytes=512,
        )
        if not isinstance(value["response_started"], bool) or value["reconcile_only"] is not True:
            _fail("GATEWAY_INVALID_UNKNOWN_RESULT")
    _scan_json(value)
    return value


def _validate_delta(kind: str, delta: dict[str, Any]) -> None:
    if kind == "text":
        if set(delta) != {"text"}:
            _fail("GATEWAY_INVALID_TEXT_DELTA")
        _text(delta["text"], "GATEWAY_INVALID_TEXT_DELTA", allow_empty=True)
        return
    if kind == "tool_call":
        fields = set(delta)
        if "index" not in fields or len(fields) < 2 or not fields <= _TOOL_CALL_DELTA_FIELDS:
            _fail("GATEWAY_INVALID_TOOL_CALL_DELTA")
        _integer(delta["index"], "GATEWAY_INVALID_TOOL_CALL_DELTA")
        for field in fields - {"index"}:
            maximum = 256 if field == "name" else 512 if field == "id" else MAX_STRING_BYTES
            _text(
                delta[field], "GATEWAY_INVALID_TOOL_CALL_DELTA",
                allow_empty=True, maximum_bytes=maximum,
            )
        return
    if kind == "usage":
        _validate_usage(delta, "GATEWAY_INVALID_USAGE_DELTA", allow_empty=False)
        return
    if not delta or not set(delta) <= _PROVIDER_METADATA_FIELDS:
        _fail("GATEWAY_INVALID_PROVIDER_METADATA")
    for field in delta:
        _text(delta[field], "GATEWAY_INVALID_PROVIDER_METADATA", maximum_bytes=512)


def _validate_usage(value: Any, code: str, *, allow_empty: bool) -> None:
    if not isinstance(value, dict) or (not value and not allow_empty) or not set(value) <= _USAGE_DELTA_FIELDS:
        _fail(code)
    for amount in value.values():
        _integer(amount, code)


def _validate_completed_tool_calls(value: Any) -> None:
    if not isinstance(value, list) or len(value) > MAX_TOOLS:
        _fail("GATEWAY_INVALID_COMPLETED_TOOL_CALLS")
    indices: set[int] = set()
    identifiers: set[str] = set()
    for call in value:
        call = _strict_object(
            call, _COMPLETED_TOOL_CALL_FIELDS, "GATEWAY_INVALID_COMPLETED_TOOL_CALLS",
        )
        _integer(call["index"], "GATEWAY_INVALID_COMPLETED_TOOL_CALLS")
        _text(call["id"], "GATEWAY_INVALID_COMPLETED_TOOL_CALLS", maximum_bytes=512)
        _text(call["name"], "GATEWAY_INVALID_COMPLETED_TOOL_CALLS", maximum_bytes=256)
        _text(
            call["arguments"], "GATEWAY_INVALID_COMPLETED_TOOL_CALLS",
            allow_empty=True, maximum_bytes=MAX_STRING_BYTES,
        )
        if call["index"] in indices or call["id"] in identifiers:
            _fail("GATEWAY_INVALID_COMPLETED_TOOL_CALLS")
        indices.add(call["index"])
        identifiers.add(call["id"])


def encode_frame(value: Mapping[str, Any], *, limit: int = RESPONSE_FRAME_LIMIT) -> bytes:
    try:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("GATEWAY_JSON_ENCODE_FAILED")
    if not payload or len(payload) > limit:
        _fail("GATEWAY_FRAME_TOO_LARGE")
    return struct.pack(">I", len(payload)) + payload


def decode_payload(payload: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("GATEWAY_DUPLICATE_JSON_FIELD")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: _fail("GATEWAY_INVALID_JSON_NUMBER"),
        )
    except UnicodeDecodeError:
        _fail("GATEWAY_INVALID_UTF8")
    except (json.JSONDecodeError, RecursionError):
        _fail("GATEWAY_INVALID_JSON")
    if not isinstance(value, dict):
        _fail("GATEWAY_INVALID_JSON_OBJECT")
    _scan_json(value)
    return value


async def read_frame(reader: asyncio.StreamReader, *, limit: int = RESPONSE_FRAME_LIMIT) -> dict[str, Any]:
    try:
        header = await reader.readexactly(4)
        length = struct.unpack(">I", header)[0]
        if length == 0 or length > limit:
            _fail("GATEWAY_FRAME_TOO_LARGE")
        return decode_payload(await reader.readexactly(length))
    except asyncio.IncompleteReadError:
        _fail("GATEWAY_UNEXPECTED_EOF")
    except (ConnectionError, OSError):
        _fail("GATEWAY_READ_FAILED")


async def write_frame(writer: asyncio.StreamWriter, value: Mapping[str, Any], *,
                      limit: int = RESPONSE_FRAME_LIMIT) -> int:
    frame = encode_frame(value, limit=limit)
    writer.write(frame)
    try:
        await writer.drain()
    except (ConnectionError, BrokenPipeError):
        _fail("GATEWAY_WRITE_FAILED")
    return len(frame) - 4
