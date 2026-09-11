"""Bounded v1 ToolResult payload, with an independently readable legacy shell.

No pickle, object import, repr/default=str, file IO or execution context restore.
The writer is opt-in after compatible readers have been deployed everywhere.
"""
from __future__ import annotations

import asyncio
import json
import math
from dataclasses import fields, replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from .result import ToolError, ToolExecutionMetadata, ToolResult

VERSION = 1
MAX_BYTES = 512 * 1024
MAX_STRING = 128 * 1024
MAX_ITEMS = 30000
MAX_DEPTH = 16
MAX_ROWS = 200
EXTENSION = "tool_result"


class ToolPayloadError(ValueError):
    """A result cannot safely be persisted/restored; never redo business to repair it."""


class _JSONBoundary:
    def __init__(self):
        self.nodes = 0
        self.bytes = 0
        self.omitted = []

    def copy(self, value, path="$", depth=0, *, metadata=False):
        self.nodes += 1
        if self.nodes > MAX_ITEMS or depth > MAX_DEPTH:
            raise ToolPayloadError("tool_payload_structure_limit")
        self.bytes += 8
        if self.bytes > MAX_BYTES:
            raise ToolPayloadError("tool_payload_size_limit")
        if value is None or type(value) in (bool, int):
            if type(value) is int and value.bit_length() > 256:
                raise ToolPayloadError("tool_payload_integer_limit")
            return value
        if isinstance(value, Decimal):
            value = float(value)
        if isinstance(value, (datetime, date)):
            value = value.isoformat()
        if isinstance(value, UUID):
            value = str(value)
        if type(value) is float:
            if not math.isfinite(value):
                raise ToolPayloadError("tool_payload_nonfinite_number")
            return value
        if type(value) is str:
            if len(value) > MAX_STRING:
                raise ToolPayloadError("tool_payload_string_limit")
            self.bytes += len(value.encode("utf-8"))
            if self.bytes > MAX_BYTES:
                raise ToolPayloadError("tool_payload_size_limit")
            return value
        if type(value) in (list, tuple):
            return [self.copy(v, f"{path}[{i}]", depth + 1, metadata=metadata) for i, v in enumerate(value)]
        if type(value) is dict:
            result = {}
            for k, v in value.items():
                if type(k) is not str or len(k) > 256:
                    raise ToolPayloadError("tool_payload_invalid_key")
                # Runtime handles are excluded only from optional metadata. Never
                # silently omit an artifact, retry argument or business data cell.
                if metadata and type(v) not in (dict, list, tuple, str, bool, int, float, type(None)) and not isinstance(v, (Decimal, date, UUID)):
                    self.omitted.append(f"{path}.{k}")
                    continue
                self.bytes += len(k.encode("utf-8"))
                result[k] = self.copy(v, f"{path}.{k}", depth + 1, metadata=metadata)
            return result
        raise ToolPayloadError("tool_payload_unsupported_value")


def _record(value):
    # Only call on explicitly supported value objects; never deep-copy a runtime
    # handle through dataclasses.asdict.
    return {f.name: getattr(value, f.name) for f in fields(value) if not f.name.startswith("_")}


def _agent_value(raw, boundary):
    value = _record(raw)
    if raw.data is not None and len(raw.data) > MAX_ROWS:
        raise ToolPayloadError("tool_payload_inline_rows_limit: use FileRef")
    value["format"] = raw.format.value
    value["columns"] = [_record(c) for c in raw.columns] if raw.columns is not None else None
    if raw.file_ref is not None:
        value["file_ref"] = {**_record(raw.file_ref), "columns": [_record(c) for c in raw.file_ref.columns]}
    # Retry context is required information, not disposable runtime metadata.
    metadata = dict(raw.metadata)
    retry_context = metadata.pop("retry_context", None)
    value["metadata"] = boundary.copy(metadata, "$.raw.metadata", metadata=True)
    if "retry_context" in raw.metadata:
        value["metadata"]["retry_context"] = boundary.copy(retry_context, "$.raw.metadata.retry_context")
    return _JSONBoundary().copy(value, "$.raw")


def encode_result(result: ToolResult) -> dict:
    boundary = _JSONBoundary()
    if result.kind == "agent":
        raw = _agent_value(result.raw, boundary)
    elif result.kind in {"file_read", "form"}:
        raw = boundary.copy(_record(result.raw), "$.raw")
    elif result.kind == "string":
        raw = boundary.copy(result.raw, "$.raw")
    elif result.kind == "exception":
        raw = None
    else:
        raise ToolPayloadError("tool_payload_unknown_kind")
    # Store facts, not Policy decisions, capabilities, argument values or handles.
    from services.tool_invocation_store import hash_tool_arguments
    from .spec import thaw
    audit = {k: result.audit.get(k) for k in (
        "tool_name", "tool_call_id", "actor_user_id", "workspace_owner_id", "org_id",
        "conversation_id", "task_id", "status", "elapsed_ms", "result_length", "truncated",
        "tokens_used", "source",
    )}
    audit["args_hash"] = hash_tool_arguments(thaw(result.audit.get("args", {})))
    extension = _JSONBoundary().copy({
        "version": VERSION, "kind": result.kind, "status": result.status, "raw": raw,
        "execution": _record(result.execution), "audit": audit,
        "error": _record(result.error) if result.error else None,
        "model_overrides": result.model_overrides,
        "omitted_metadata": boundary.omitted,
    })
    # The rollback reader cdba58f9 still understands this shell. Its historical
    # projection is deliberately lossy; it is not a full-fidelity rollback.
    if result.kind == "agent":
        restored = decode_raw(extension)
        shell = {"kind": "agent_result", "summary": restored.to_tool_content(),
                 "status": restored.status, "error_message": restored.error_message,
                 "emit_payloads": restored.emit_payloads}
    elif result.kind == "string":
        shell = {"kind": "scalar", "value": raw}
    else:
        shell = {"kind": "agent_result", "summary": result.display["text"],
                 "status": "error" if result.is_failure or result.execution.cancelled else result.status,
                 "error_message": result.error.message if result.error else "", "emit_payloads": []}
    payload = {**shell, EXTENSION: extension}
    validate_payload(payload)
    return payload


def validate_payload(payload):
    copied = _JSONBoundary().copy(payload)
    if len(json.dumps(copied, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_BYTES:
        raise ToolPayloadError("tool_payload_size_limit")
    return copied


def extension_of(payload):
    if not isinstance(payload, dict) or EXTENSION not in payload:
        return None
    checked = validate_payload(payload)
    extension = checked[EXTENSION]
    if not isinstance(extension, dict) or type(extension.get("version")) is not int or extension["version"] != VERSION:
        raise ToolPayloadError("tool_payload_unsupported_version")
    return extension


def decode_raw(extension):
    from services.agent.agent_result import AgentResult
    from services.agent.tool_output import ColumnMeta, FileRef, OutputFormat
    from schemas.multimodal import FileReadResult
    from services.scheduler.chat_task_manager import FormBlockResult
    kind, raw = extension["kind"], extension["raw"]
    if kind == "agent":
        raw = dict(raw)
        raw["format"] = OutputFormat(raw["format"])
        if raw["columns"] is not None:
            raw["columns"] = [ColumnMeta(**c) for c in raw["columns"]]
        if raw["file_ref"] is not None:
            ref = dict(raw["file_ref"])
            ref["columns"] = [ColumnMeta(**c) for c in ref["columns"]]
            ref["derived_from"] = tuple(ref["derived_from"])
            raw["file_ref"] = FileRef(**ref)
        return AgentResult(**raw)
    if kind == "file_read":
        return FileReadResult(**raw)
    if kind == "form":
        return FormBlockResult(**raw)
    if kind == "string" and type(raw) is str:
        return raw
    if kind == "exception":
        return None
    raise ToolPayloadError("tool_payload_unknown_kind")


def restore_result(payload, *, call, context, decision):
    """Restore facts and rebind to current authorized call; no saved permissions."""
    extension = extension_of(payload)
    if extension is None:
        from services.tool_invocation_store import deserialize_tool_result
        raw = deserialize_tool_result(payload)
        # Historical scalar/json payloads never carried a result type contract.
        from services.agent.agent_result import AgentResult
        if not isinstance(raw, (str, AgentResult)):
            raw = str(raw)
        return ToolResult.wrap(raw, call=call, context=context, decision=decision)
    try:
        raw = decode_raw(extension)
        if extension["kind"] == "agent" and raw.status != extension["status"]:
            raise ToolPayloadError("tool_payload_status_mismatch")
        error = ToolError(**extension["error"]) if extension["error"] else None
        execution = ToolExecutionMetadata(**{**extension["execution"], "effects": tuple(extension["execution"]["effects"])})
        if error is not None:
            error = replace(error, safe_to_retry=error.safe_to_retry and decision.effects == ("none",)
                            and execution.status != "uncertain" and not execution.cancelled)
        exception = None
        if extension["kind"] == "exception":
            message = error.message if error else ""
            exception = asyncio.CancelledError(message) if execution.cancelled else (
                TimeoutError(message) if extension["status"] == "timeout" else RuntimeError(message))
        audit = ToolResult._audit(call, context, extension["status"], execution.elapsed_ms,
                                 extension["audit"]["result_length"], raw if extension["kind"] == "agent" else None)
        audit.update(truncated=extension["audit"]["truncated"], origin=extension["audit"],
                     payload_version=VERSION, omitted_metadata=extension["omitted_metadata"])
        return ToolResult(raw, extension["kind"], extension["status"], decision, execution,
                          audit, error, exception, extension["model_overrides"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolPayloadError("tool_payload_invalid_result") from exc
