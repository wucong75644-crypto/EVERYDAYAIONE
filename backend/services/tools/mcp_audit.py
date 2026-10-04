"""Allowlisted, token-free MCP identity facts for invocation audit payloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace


def connector_facts(spec):
    if spec is None or spec.executor_type != "mcp":
        return None
    from .mcp_allowlist import CONNECTOR_ID, REMOTE_TOOL_NAME, TOOLS

    reviewed = TOOLS.get(REMOTE_TOOL_NAME)
    if reviewed is None or spec != reviewed.spec():
        return None
    return {
        "connector_id": CONNECTOR_ID,
        "capability": spec.capability,
        "remote_tool_name": reviewed.remote_name,
        "replay_requirement": spec.replay_requirement,
    }


def decorate_result(result, spec):
    facts = connector_facts(spec)
    if facts is None:
        return result
    audit = dict(result.audit)
    audit.update(facts)
    audit["invocation_status"] = result.execution.status
    audit["replayed"] = result.execution.replayed
    audit["result_sha256"] = None
    if result.exception is None:
        raw = result.raw if isinstance(result.raw, str) else json.dumps(
            result.raw, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        )
        audit["result_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if result.error is not None:
        audit["error_code"] = result.error.kind[:80]
    return replace(result, audit=audit)
