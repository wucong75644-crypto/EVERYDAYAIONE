"""Build a deterministic, Run-fenced Provider context from PostgreSQL facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from services.agent.runtime.catalog.effective_toolset import EffectiveToolset
from services.agent.runtime.context.provider_plan import ProviderContextPlan


@dataclass(frozen=True, kw_only=True)
class RuntimeContextV2:
    run_id: str
    session_id: str
    base_context_revision: str
    through_message_id: str
    plan: ProviderContextPlan
    receipt_facts: Mapping[str, object]


def build_runtime_context(
    *, run: Mapping[str, object], session: Mapping[str, object],
    messages: list[dict[str, object]], actions: list[Mapping[str, object]],
    toolset: EffectiveToolset, model_step: int,
) -> RuntimeContextV2:
    run_id = _required(run, "id")
    session_id = _required(session, "id")
    receipt = _mapping(run.get("context_receipt"), "context_receipt")
    base_revision = str(receipt.get("base_context_revision") or "")
    through = str(receipt.get("through_message_id") or "")
    if not base_revision or not through:
        raise RuntimeError("RUNTIME_CONTEXT_ANCHOR_MISSING")
    projected = list(messages)
    projected.extend(_action_messages(actions, toolset))
    plan = ProviderContextPlan.build(
        messages=projected, tools=toolset.provider_tools(),
        context_epoch_id=f"{run_id}:{base_revision}:{through}",
        model_step=model_step, stable_prefix_blocks=0,
    )
    return RuntimeContextV2(
        run_id=run_id, session_id=session_id,
        base_context_revision=base_revision, through_message_id=through,
        plan=plan,
        receipt_facts={
            "run_id": run_id, "session_id": session_id,
            "base_context_revision": base_revision,
            "through_message_id": through,
            "effective_toolset_hash": toolset.toolset_hash,
            "catalog_revision": toolset.catalog_revision,
            "context_plan_hash": plan.plan_hash,
        },
    )


def _action_messages(
    actions: list[Mapping[str, object]], toolset: EffectiveToolset,
) -> list[dict[str, object]]:
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for action in actions:
        name = str(action.get("tool_name") or "")
        args = action.get("arguments")
        if not isinstance(args, dict):
            raise RuntimeError("RUNTIME_ACTION_ARGUMENTS_INVALID")
        toolset.validate_call(name, args)
        grouped.setdefault(str(action.get("model_step_id") or "unknown"), []).append(action)
    result: list[dict[str, object]] = []
    for step_actions in grouped.values():
        calls = [{"id": str(item["stable_tool_call_id"]), "type": "function",
                  "function": {"name": str(item["tool_name"]),
                                "arguments": _json_arguments(item["arguments"])}}
                for item in step_actions]
        result.append({"role": "assistant", "content": None, "tool_calls": calls})
        for item in step_actions:
            action_result = item.get("result")
            result.append({"role": "tool", "tool_call_id": str(item["stable_tool_call_id"]),
                           "content": _result_content(
                               action_result, str(item.get("status") or "unknown"),
                           )})
    return result


def _result_content(value: object, action_status: str) -> str:
    import json

    if action_status not in {"completed", "failed"} or not isinstance(value, dict):
        return _canonical_json({
            "status": "unknown" if action_status == "unknown" else "unresolved",
            "action_status": action_status,
        })
    result_status = str(value.get("status") or "error")
    if result_status not in {"success", "empty", "degraded", "error"}:
        result_status = "error"
    view = {
        "status": result_status,
        "summary": _bounded_text(value.get("summary")),
        "data": _bounded_json(value.get("data")),
        "artifact_ids": _bounded_json(value.get("artifact_ids", [])),
        "error_code": _bounded_text(value.get("error_code")),
    }
    return json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bounded_text(value: object, limit: int = 2000) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:limit]


def _bounded_json(value: object, limit: int = 20000) -> object:
    import json
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None
    if len(encoded) > limit:
        return {"truncated": True, "sha256": _sha256(encoded), "bytes": len(encoded)}
    return value


def _canonical_json(value: object) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha256(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()


def _json_arguments(value: object) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"RUNTIME_{name.upper()}_INVALID")
    return value


def _required(value: Mapping[str, object], name: str) -> str:
    result = str(value.get(name) or "")
    if not result:
        raise RuntimeError(f"RUNTIME_{name.upper()}_MISSING")
    return result
