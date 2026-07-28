"""Safe Sandbox receipt and manifest builders."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping

from .contracts import bounded_summary
from .workspace import WorkspaceObject


def build_receipt(
    *, execution_outcome: str, stdout: bytes, stderr: bytes,
    artifacts: Iterable[WorkspaceObject] = (),
    partials: Iterable[dict] = (),
    materialized: bool = False, cleaned: bool = True,
) -> tuple[str, dict[str, object]]:
    stdout_fields = bounded_summary(stdout)
    stderr_fields = bounded_summary(stderr)
    artifact_items = [
        {
            "workspace_object_ref": item.reference,
            "content_sha256": item.content_sha256,
            "size_bytes": item.size_bytes,
            "media_type": item.media_type,
        }
        for item in artifacts
    ]
    partial_items = list(partials)
    receipt: dict[str, object] = {
        "receipt_revision": 1,
        "execution_outcome": execution_outcome,
        "stdout_summary": stdout_fields[0],
        "stdout_original_length": stdout_fields[1],
        "stdout_sha256": stdout_fields[2],
        "stdout_truncated": stdout_fields[3],
        "stderr_summary": stderr_fields[0],
        "stderr_original_length": stderr_fields[1],
        "stderr_sha256": stderr_fields[2],
        "stderr_truncated": stderr_fields[3],
        "artifact_manifest": {"schema_revision": 1, "items": artifact_items},
        "partial_effects": {"schema_revision": 1, "items": partial_items},
        "materialization_status": "completed" if materialized else "not_started",
        "materialization_receipt": {},
        "cleanup_status": (
            "completed" if partial_items and cleaned
            else "pending" if partial_items
            else "not_required"
        ),
        "cleanup_evidence": (
            {"kind": "SANDBOX_PARTIAL_CLEANED"}
            if partial_items and cleaned else
            {"kind": "SANDBOX_PARTIAL_CLEANUP_PENDING"}
            if partial_items else {}
        ),
    }
    encoded = _postgres_jsonb_text(receipt).encode()
    return hashlib.sha256(encoded).hexdigest(), receipt


def _postgres_jsonb_text(value: object) -> str:
    """Encode the JSON-only receipt exactly like PostgreSQL jsonb::text."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        items = ", ".join(_postgres_jsonb_text(item) for item in value)
        return f"[{items}]"
    if isinstance(value, Mapping):
        keys = tuple(value)
        if any(not isinstance(key, str) for key in keys):
            raise ValueError("SANDBOX_RECEIPT_KEY_INVALID")
        ordered = sorted(keys, key=lambda key: (len(key.encode()), key.encode()))
        return "{" + ", ".join(
            f"{json.dumps(key, ensure_ascii=False)}: "
            f"{_postgres_jsonb_text(value[key])}"
            for key in ordered
        ) + "}"
    raise ValueError("SANDBOX_RECEIPT_VALUE_INVALID")
