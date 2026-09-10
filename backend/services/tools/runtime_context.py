"""Trusted production facts and read-only resource checks for tool entrypoints."""

from dataclasses import asdict, replace
from pathlib import Path

from .context import ToolContext
from .spec import thaw


def catalog_context(org_id, permission_mode="auto", personal_context_allowed=True):
    """Metadata-only compatibility helpers; never used as an execution grant."""
    from core.config import get_settings
    settings = get_settings()
    return ToolContext(
        actor_user_id="catalog", workspace_owner_id="catalog" if personal_context_allowed else "channel",
        org_id=org_id, context_scope="user" if personal_context_allowed else "channel",
        personal_context_allowed=personal_context_allowed, agent_domain="general",
        permission_mode=permission_mode, execution_mode="interactive",
        feature_flags={key: getattr(settings, key, False) is True for key in (
            "file_workspace_enabled", "sandbox_enabled", "crawler_enabled",
        )},
    )


def chat_context(handler, *, user_id, conversation_id, task_id, permission_mode,
                 budget=None, cancellation=None):
    from types import SimpleNamespace
    scope = getattr(handler, "execution_scope", None)
    return executor_context(SimpleNamespace(
        user_id=user_id, workspace_user_id=getattr(handler, "_workspace_user_id", user_id),
        org_id=handler.org_id, conversation_id=conversation_id, task_id=task_id,
        context_scope=getattr(scope, "context_scope", "user"),
        personal_context_allowed=getattr(handler, "_personal_context_allowed", True),
        permission_mode=permission_mode, agent_domain="general", execution_mode="interactive",
        allowed_tool_names=None, tool_policy_snapshot={},
        resource_manifest=getattr(handler, "_resource_manifest", None),
        execution_budget=budget, cancellation_event=cancellation,
        tool_entrypoint="model", tool_confirmer=True,
    ))


def executor_context(executor, *, call_id=None) -> ToolContext:
    from core.config import get_settings
    settings = get_settings()
    manifest = executor.resource_manifest
    return ToolContext(
        actor_user_id=executor.user_id, workspace_owner_id=executor.workspace_user_id,
        org_id=executor.org_id, conversation_id=executor.conversation_id,
        task_id=executor.task_id, call_id=call_id,
        context_scope=executor.context_scope,
        personal_context_allowed=executor.personal_context_allowed,
        permission_mode=executor.permission_mode, agent_domain=executor.agent_domain,
        execution_mode=executor.execution_mode, entrypoint=executor.tool_entrypoint,
        authorized_tool_names=executor.allowed_tool_names,
        authorization_snapshot=executor.tool_policy_snapshot,
        feature_flags={name: getattr(settings, name) is True for name in (
            "file_workspace_enabled", "sandbox_enabled", "crawler_enabled",
        )},
        resource_manifest=None if manifest is None else tuple(asdict(a) for a in manifest.assets),
        budget=executor.execution_budget, cancellation=executor.cancellation_event,
        confirmation_available=executor.tool_confirmer is not None,
    )


async def refresh_context(executor, context, registry):
    """Recheck the same membership rules as OrgContext, without role invention.

    Scope originates at the authenticated entrypoint. Conversation ownership and
    org membership are read again after waiting; unknown/failed reads fail closed.
    PermissionChecker is used only for existing, explicitly declared codes.
    """
    import asyncio
    from services.permissions.checker import PermissionChecker
    snapshot = thaw(context.authorization_snapshot)
    try:
        await asyncio.to_thread(_check_identity, executor, context)
        if executor.resource_manifest_loader is not None:
            executor.resource_manifest = await executor.resource_manifest_loader()
            context = replace(context, resource_manifest=tuple(
                asdict(asset) for asset in executor.resource_manifest.assets
            ))
        codes = {code for spec in registry.specs() for code in spec.policy_rules.required_permissions}
        codes.update(snapshot.get("required_permissions") or ())
        checker = PermissionChecker(executor.db)
        snapshot["permissions"] = {
            code: bool(context.org_id) and await checker.check(
                context.actor_user_id, context.org_id, code,
            ) for code in sorted(codes)
        }
        if any(snapshot["permissions"].get(code) is not True for code in snapshot.get("required_permissions", ())):
            snapshot["access_denied_reason"] = "business_permission_required"
    except Exception:
        snapshot["access_denied_reason"] = "identity_or_authorization_unavailable"
    return replace(context, authorization_snapshot=snapshot)


def _check_identity(executor, context):
    def row(table, fields, **filters):
        query = executor.db.table(table).select(fields)
        for key, value in filters.items():
            query = query.eq(key, value)
        response = query.maybe_single().execute()
        data = response.data if response else None
        if not isinstance(data, dict):
            raise PermissionError("identity_unavailable")
        return data

    if context.org_id:
        if row("organizations", "status", id=context.org_id).get("status") != "active":
            raise PermissionError("organization_inactive")
        if row("org_members", "status", org_id=context.org_id,
               user_id=context.actor_user_id).get("status") != "active":
            raise PermissionError("organization_membership_required")
    if context.execution_mode == "interactive":
        conversation = row("conversations", "user_id,org_id,scope_type,scope_id,source",
                           id=context.conversation_id)
        if conversation.get("org_id") != context.org_id:
            raise PermissionError("conversation_organization_mismatch")
        if context.context_scope == "user":
            if (conversation.get("scope_type") or "user") != "user" or conversation.get("user_id") != context.actor_user_id:
                raise PermissionError("conversation_owner_mismatch")
        else:
            scope = executor.execution_scope
            if (scope is None or scope.workspace_owner_id != context.workspace_owner_id
                    or scope.actor_user_id != context.actor_user_id
                    or conversation.get("scope_type") != "channel"
                    or conversation.get("source") != "wecom" or conversation.get("user_id") is not None
                    or str(conversation.get("scope_id") or "") != executor.channel_scope_id):
                raise PermissionError("channel_scope_mismatch")


def resolve_resources(executor, name, arguments):
    """Compatibility facade for the single scoped file target resolver."""
    from .file_calls import resolve_file_call
    operation = resolve_file_call(executor, name, arguments)
    return operation.arguments if operation is not None else thaw(arguments)


async def check_deferred_resources(executor, name, arguments):
    """Compatibility check; production keeps the resolved record through dispatch."""
    from .file_calls import resolve_file_call, resolve_restore_record
    operation = resolve_file_call(executor, name, arguments)
    await resolve_restore_record(operation)


def check_result_resources(context, value):
    """Validate explicit workspace references in old cache/ledger results.

    Result artifacts can be in staging; this is containment validation, not the
    file-tools rule that forbids opening staging as user input. No payload rewrite.
    """
    from core.config import get_settings
    from services.file_executor import FileExecutor
    payloads = value.get("emit_payloads", []) if isinstance(value, dict) else getattr(value, "emit_payloads", [])
    if not payloads:
        return
    files = FileExecutor(get_settings().file_workspace_root, context.workspace_owner_id,
                         context.org_id, create_root=False)
    root = Path(files.workspace_root)
    for payload in payloads:
        if not isinstance(payload, dict) or not payload.get("workspace_path"):
            continue
        path = Path(payload["workspace_path"])
        target = path if path.is_absolute() else root / path
        try:
            target.resolve().relative_to(root)
        except ValueError:
            raise PermissionError("replay_resource_scope_mismatch: 工具未执行") from None
