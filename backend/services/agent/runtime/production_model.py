"""Production ModelLoop plan built only from fenced PostgreSQL context."""

from __future__ import annotations

import hashlib
import json
import math
from uuid import UUID, uuid5

from services.agent.runtime.application.model_loop import PreparedModelCall
from services.agent.runtime.context import build_runtime_context, build_context_receipt
from services.agent.runtime.catalog import (
    EffectiveToolset, restore_agent_definition, restore_frozen_toolset,
)
from services.agent.runtime.infrastructure.model.projection import (
    compute_request_hash, resolve_model_revision,
)
from services.agent.runtime.ports.coordinator_recovery import RunAggregateSnapshot
from services.agent.runtime.ports.model import (
    ModelInputReceipt, ModelRequestOptions, ModelStepId, ModelStepRequest,
    ModelStepResult,
)
from services.agent.runtime.credential_broker import CredentialBroker, CredentialLease
from services.agent.runtime.domain import RuntimeScope, ScopeKind


_ACTION_NAMESPACE = UUID("76bc769a-a201-43aa-8ee9-cd13f009f12d")
_POLICY_REVISION = "agent-runtime-policy-v1"


class PostgresModelCallFactory:
    def __init__(
        self, database, worker_id: str, *, version_registry=None,
        credential_broker: CredentialBroker | None = None,
    ) -> None:
        self._database = database
        self._worker_id = worker_id
        self._versions = version_registry
        self._credential_broker = credential_broker

    async def __call__(
        self, snapshot: RunAggregateSnapshot,
    ) -> PreparedModelCall:
        run_id = str(snapshot.run["id"])
        token = str(snapshot.run["execution_token"])
        response = await self._database.rpc(
            "get_agent_runtime_model_context_v2", {
                "p_run_id": run_id,
                "p_worker_id": self._worker_id,
                "p_execution_token": token,
            },
        ).execute()
        context = response.data
        if not isinstance(context, dict) or context.get("outcome") != "found":
            raise RuntimeError("AGENT_RUNTIME_MODEL_CONTEXT_UNAVAILABLE")
        command = _mapping(context.get("command"), "command")
        session = _mapping(context.get("session"), "session")
        payload = _mapping(command.get("payload"), "payload")
        from services.adapters.factory import get_model_config
        definition, toolset = _frozen_runtime_facts(context)
        model_policy = definition.model_policy
        requested_model = model_policy.get("model_id")
        if not isinstance(requested_model, str) or not get_model_config(requested_model):
            raise RuntimeError("RUNTIME_DEFINITION_MODEL_POLICY_INVALID")
        model_id = requested_model
        messages = _messages(context.get("messages"), definition.system_prompt)
        step_number = len(snapshot.model_steps) + 1
        stable_prefix_blocks = _stable_prefix_blocks(definition.context_policy)
        runtime_context = build_runtime_context(
            run=dict(snapshot.run), session=session, messages=messages,
            actions=_list(context.get("actions")), toolset=toolset,
            model_step=step_number, stable_prefix_blocks=stable_prefix_blocks,
        )
        plan = runtime_context.plan
        tools = toolset.provider_tools()
        context_messages, _ = plan.project()
        receipt = build_context_receipt(
            messages=context_messages,
            tools=tools,
            conversation_id=str(session["conversation_id"]),
            task_id=str(payload.get("task_id") or command["id"]),
            model_id=model_id,
            base_revision=int(str(runtime_context.base_context_revision).split(":")[-1] or 0)
            if str(runtime_context.base_context_revision).split(":")[-1].isdigit()
            else 0,
            stable_prefix_blocks=stable_prefix_blocks,
        )
        receipt_data = receipt.to_log_fields()
        org_id = str(session["org_id"])
        receipt_data["org_id"] = org_id
        receipt_hash = _hash(receipt_data)
        revision = resolve_model_revision(model_id)
        provider = _provider(model_id)
        credential, receipt_hash = await _bind_model_credential(
            self._credential_broker, context=context, receipt_data=receipt_data,
            user_id=str(session["user_id"]), org_id=org_id,
            provider=provider, revision=revision,
        )
        options = ModelRequestOptions(
            thinking_mode=_optional_text(
                _mapping(payload.get("params") or {}, "params").get(
                    "thinking_mode",
                ),
            ),
            timeout_seconds=120,
            max_provider_attempts=1,
        )
        reserved = _reserved_credits(
            model_id, receipt.estimated_prompt_tokens,
        )

        def build_request(step_id: str) -> ModelStepRequest:
            input_receipt = ModelInputReceipt(
                receipt_id=f"context:{run_id}:{step_number}",
                receipt_hash=receipt_hash,
                context_plan_hash=plan.plan_hash,
            )
            request_hash = compute_request_hash(
                model_id=model_id,
                model_revision=revision,
                prompt_revision=definition.prompt_revision,
                tool_catalog_revision=toolset.catalog_revision,
                input_receipt_hash=receipt_hash,
                context_plan_hash=plan.plan_hash,
                options=options,
            )
            return ModelStepRequest(
                model_step_id=ModelStepId(step_id),
                model_id=model_id,
                request_hash=request_hash,
                input_receipt=input_receipt,
                context_plan=plan,
                model_revision=revision,
                prompt_revision=definition.prompt_revision,
                tool_catalog_revision=toolset.catalog_revision,
                options=options,
                org_id=org_id,
                credential_lease=credential,
                credential_scope=RuntimeScope(
                    kind=ScopeKind.USER,
                    scope_id=f"user:{session['user_id']}",
                    user_id=str(session["user_id"]), org_id=org_id,
                ),
                credential_purpose=credential.purpose,
            )

        return PreparedModelCall(
            model_id=model_id,
            provider=_provider(model_id),
            model_revision=revision,
            prompt_revision=definition.prompt_revision,
            tool_catalog_revision=toolset.catalog_revision,
            request_receipt=receipt_data,
            reserved_credits=reserved,
            build_request=build_request,
            actual_credits=lambda result: _actual_credits(model_id, result),
            build_actions=lambda result: _actions(result, run_id, toolset),
        )


async def retain_unknown_model_attempt(
    snapshot: RunAggregateSnapshot,
) -> None:
    """No provider has a proven readback API; unknown is reconcile-only."""
    del snapshot


def _messages(value: object, system_prompt: str) -> list[dict]:
    if not isinstance(value, list):
        raise RuntimeError("AGENT_RUNTIME_MESSAGES_INVALID")
    from services.handlers.chat_context.content_extractors import (
        extract_oai_messages_from_content,
    )
    if not system_prompt:
        raise RuntimeError("RUNTIME_DEFINITION_PROMPT_MISSING")
    result = [{"role": "system", "content": system_prompt}]
    for item in value:
        row = _mapping(item, "message")
        role = str(row.get("role") or "")
        if role not in {"system", "user", "assistant"}:
            continue
        result.extend(extract_oai_messages_from_content(
            row.get("content"), role, safe_completed_tools_only=True,
        ))
    if len(result) == 1:
        raise RuntimeError("AGENT_RUNTIME_MESSAGES_EMPTY")
    return result


def _code_execute_tools(org_id: object) -> list[dict]:
    from config.chat_tools import get_chat_tools

    return [
        tool for tool in get_chat_tools(org_id=str(org_id) if org_id else None)
        if tool.get("function", {}).get("name") == "code_execute"
    ]


def _frozen_runtime_facts(context: dict):
    facts = (
        context.get("definition_fact"), context.get("catalog_fact"),
        context.get("effective_toolset_fact"),
    )
    if not all(isinstance(fact, dict) for fact in facts):
        raise RuntimeError("RUNTIME_VERSION_FACTS_UNAVAILABLE")
    try:
        definition = restore_agent_definition(facts[0]["definition_document"])
        toolset = restore_frozen_toolset(
            facts[0]["definition_document"], facts[1]["catalog_document"],
            facts[2]["toolset_document"],
            catalog_revision=facts[1].get("catalog_revision"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("RUNTIME_VERSION_FACTS_INVALID") from exc
    if toolset.toolset_hash != facts[2].get("effective_toolset_hash"):
        raise RuntimeError("RUNTIME_EFFECTIVE_TOOLSET_REVISION_MISMATCH")
    return definition, toolset


def _stable_prefix_blocks(policy: object) -> int:
    if not isinstance(policy, dict):
        raise RuntimeError("RUNTIME_CONTEXT_POLICY_INVALID")
    value = policy.get("stable_prefix_blocks", 0)
    if not isinstance(value, int) or not 0 <= value <= 8:
        raise RuntimeError("RUNTIME_CONTEXT_POLICY_INVALID")
    return value


def _list(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise RuntimeError("RUNTIME_ACTIONS_INVALID")
    return [item for item in value if isinstance(item, dict)]


def _reserved_credits(model_id: str, input_tokens: int) -> int:
    from services.adapters.factory import get_model_config

    config = get_model_config(model_id)
    if config is None:
        raise RuntimeError("AGENT_RUNTIME_MODEL_UNKNOWN")
    return max(1, math.ceil(
        max(input_tokens, config.context_window) / 1000
        * config.credits_per_1k_input
        + config.max_tokens / 1000 * config.credits_per_1k_output
    ) + 1)


def _actual_credits(model_id: str, result: ModelStepResult) -> int:
    from services.adapters.factory import get_model_config

    config = get_model_config(model_id)
    if config is None:
        raise RuntimeError("AGENT_RUNTIME_MODEL_UNKNOWN")
    usage = result.usage
    value = math.ceil(
        usage.input_tokens / 1000 * config.credits_per_1k_input
        + usage.output_tokens / 1000 * config.credits_per_1k_output
    )
    return max(1, value + 1)


def _actions(
    result: ModelStepResult, run_id: str, toolset: EffectiveToolset | None = None,
) -> tuple[str, tuple[dict, ...]]:
    validate_schema = toolset is not None
    if toolset is None:
        raise RuntimeError("RUNTIME_VERSION_FACTS_REQUIRED")
    actions = []
    for call in result.tool_calls:
        arguments = json.loads(call.arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError("RUNTIME_TOOL_CALL_ARGUMENTS_INVALID")
        if validate_schema:
            toolset.validate_call(call.name, arguments)
        tool = next(item for item in toolset.definitions
                    if item.canonical_name == call.name)
        action_id = str(uuid5(
            _ACTION_NAMESPACE, f"{run_id}:{call.index}:{call.call_id}",
        ))
        policy_snapshot = {
            "source": "runtime_executor_registry",
            "safety_level": tool.safety_level,
        }
        if validate_schema:
            policy_snapshot.update({
                "schema_hash": tool.schema_hash,
                "executor_revision": tool.executor_revision,
            })
        actions.append({
            "action_id": action_id,
            "index": call.index,
            "stable_tool_call_id": call.call_id,
            "provider_call_id": call.provider_call_id,
            "tool_name": call.name,
            "arguments": arguments,
            "wave": 0,
            "dependencies": [],
            "blocking": True,
            "policy_decision": "requires_authorization",
            "policy_snapshot": policy_snapshot,
            "policy_revision": _POLICY_REVISION,
            "retry_disposition": "retry_after_reconcile",
        })
    return "0" * 64, tuple(actions)


def _provider(model_id: str) -> str:
    from services.adapters.factory import get_model_config

    config = get_model_config(model_id)
    if config is None:
        raise RuntimeError("AGENT_RUNTIME_MODEL_UNKNOWN")
    return str(config.provider.value)


async def _model_credential(
    broker: CredentialBroker | None, *, context: dict, user_id: str,
    org_id: str, provider: str, revision: str,
) -> CredentialLease:
    if broker is None:
        raise RuntimeError("RUNTIME_CREDENTIAL_BROKER_REQUIRED")
    handle = context.get("model_credential_handle")
    if not isinstance(handle, str) or not handle.strip():
        raise RuntimeError("CREDENTIAL_HANDLE_REQUIRED")
    scope = RuntimeScope(
        kind=ScopeKind.USER, scope_id=f"user:{user_id}", user_id=user_id,
        org_id=org_id,
    )
    return await broker.resolve(
        scope=scope, credential_handle=handle, provider=provider,
        revision=revision, purpose="model.invoke",
    )


async def _bind_model_credential(
    broker: CredentialBroker | None, *, context: dict,
    receipt_data: dict, user_id: str, org_id: str, provider: str,
    revision: str,
) -> tuple[CredentialLease, str]:
    credential = await _model_credential(
        broker, context=context, user_id=user_id, org_id=org_id,
        provider=provider, revision=revision,
    )
    receipt_data.update({
        "credential_handle": credential.handle,
        "credential_provider": credential.provider,
        "credential_revision": credential.revision,
        "credential_purpose": credential.purpose,
    })
    return credential, _hash(receipt_data)


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode()).hexdigest()


def _mapping(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError(f"AGENT_RUNTIME_{name.upper()}_INVALID")
    return value


def _optional_text(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None
