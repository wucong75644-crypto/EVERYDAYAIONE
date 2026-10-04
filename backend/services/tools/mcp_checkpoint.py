"""MCP result snapshots in Actor checkpoints; never an execution permission."""
from .mcp_allowlist import TOOL_NAME
from .result_payload import encode_result, validate_payload

MAX_INVOCATIONS = 64


def restore_invocations(replay_context, runtime):
    rows = (replay_context or {}).get("mcp_invocations", [])
    if not isinstance(rows, list) or len(rows) > MAX_INVOCATIONS:
        raise ValueError("MCP_CHECKPOINT_INVALID")
    validate_payload(rows)
    saved = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"task_id", "turn_id", "tool_call_id", "result"}
                or row["task_id"] != runtime.task_id or row["turn_id"] != runtime.turn_id
                or not isinstance(row["tool_call_id"], str) or not row["tool_call_id"]
                or row["tool_call_id"] in saved):
            raise ValueError("MCP_CHECKPOINT_INVALID")
        extension = row["result"].get("tool_result") if isinstance(row["result"], dict) else None
        audit = extension.get("audit") if isinstance(extension, dict) else None
        if (not isinstance(audit, dict) or audit.get("tool_name") != TOOL_NAME
                or audit.get("tool_call_id") != row["tool_call_id"]
                or audit.get("task_id") != runtime.task_id
                or audit.get("conversation_id") != runtime.conversation_id):
            raise ValueError("MCP_CHECKPOINT_INVALID")
        saved[row["tool_call_id"]] = row
    # These are carried for review/recovery; replay uses the current-policy gated
    # persistent invocation store, never the checkpoint to authorize another call.
    return saved


def remember_invocation(runtime, result):
    row = {"task_id": runtime.task_id, "turn_id": runtime.turn_id,
           "tool_call_id": result.audit["tool_call_id"], "result": encode_result(result)}
    proposed = dict(runtime.mcp_invocations)
    proposed[row["tool_call_id"]] = row
    runtime.mcp_invocations = restore_invocations({"mcp_invocations": list(proposed.values())}, runtime)
