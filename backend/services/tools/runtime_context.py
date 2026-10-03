"""Trusted production facts and read-only resource checks for tool entrypoints."""

from dataclasses import asdict, replace
from pathlib import Path

from .context import ToolContext
from .spec import thaw
from services.skills.creation_policy import from_features


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
            "file_workspace_enabled", "sandbox_enabled", "crawler_enabled", "scheduled_task_direct_enabled",
            "mcp_connectors_enabled", "skill_catalog_enabled", "skill_chat_creation_enabled",
            "skill_org_admin",
        )},
    )


def chat_context(handler, *, user_id, conversation_id, task_id, permission_mode,
                 budget=None, cancellation=None):
    from types import SimpleNamespace
    scope = getattr(handler, "execution_scope", None)
    return executor_context(SimpleNamespace(
        db=handler.db,
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
    from .resource_access import resource_boundary
    manifest = executor.resource_manifest
    feature_flags = {name: getattr(settings, name, False) is True for name in (
        "file_workspace_enabled", "sandbox_enabled", "crawler_enabled", "scheduled_task_direct_enabled",
        "mcp_connectors_enabled", "skill_catalog_enabled", "skill_chat_creation_enabled",
    )}
    feature_flags["skill_org_admin"] = False
    # Connector access is separately organization-scoped. A missing row,
    # mismatched database scope, or failed status read always disables it.
    from .mcp_org import connector_is_enabled
    feature_flags["mcp_connector_test_readonly_enabled"] = (
        feature_flags["mcp_connectors_enabled"]
        and connector_is_enabled(
            executor.db, executor.org_id, actor_user_id=executor.user_id,
        )
    )
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
        feature_flags=feature_flags,
        resource_manifest=None if manifest is None else tuple(asdict(a) for a in manifest.assets),
        resource_access=resource_boundary(executor).as_dict(),
        budget=executor.execution_budget, cancellation=executor.cancellation_event,
        confirmation_available=executor.tool_confirmer is not None,
    )


async def prepare_initial_context(handler, context: ToolContext) -> ToolContext:
    """Load the trusted Skill-admin fact before the first model advertisement.

    The runtime rechecks identity again before dispatch. This initial read only
    controls whether the model sees the proposal tool in its first turn.
    """
    flags = dict(context.feature_flags)
    if (flags.get("skill_catalog_enabled") is not True
            or flags.get("skill_chat_creation_enabled") is not True
            or context.execution_mode != "interactive"):
        return replace(context, feature_flags={**flags, "skill_org_admin": False})
    from types import SimpleNamespace
    import asyncio

    identity_executor = SimpleNamespace(
        db=handler.db,
        execution_scope=getattr(handler, "execution_scope", None),
        channel_scope_id=getattr(handler, "channel_scope_id", None),
    )
    try:
        identity = await asyncio.to_thread(_check_identity, identity_executor, context)
        skill_org_admin, org_chat_creation_enabled = _identity_flags(identity)
    except Exception:
        skill_org_admin = False
        org_chat_creation_enabled = False
    return replace(context, feature_flags={
        **flags,
        "skill_org_admin": skill_org_admin is True,
        "skill_chat_creation_enabled": flags.get("skill_chat_creation_enabled") is True
            and org_chat_creation_enabled is True,
    })


async def refresh_context(executor, context, registry):
    """Recheck the same membership rules as OrgContext, without role invention.

    Scope originates at the authenticated entrypoint. Conversation ownership and
    org membership are read again after waiting; unknown/failed reads fail closed.
    PermissionChecker is used only for existing, explicitly declared codes.
    """
    import asyncio
    from services.permissions.checker import PermissionChecker
    snapshot = thaw(context.authorization_snapshot)
    flags = dict(context.feature_flags)
    flags["mcp_connector_test_readonly_enabled"] = False
    skill_org_admin = False
    org_chat_creation_enabled = False
    try:
        identity = await asyncio.to_thread(_check_identity, executor, context)
        skill_org_admin, org_chat_creation_enabled = _identity_flags(identity)
        from .mcp_org import connector_is_enabled
        flags["mcp_connector_test_readonly_enabled"] = (
            flags.get("mcp_connectors_enabled") is True
            and await asyncio.to_thread(
                connector_is_enabled, executor.db, context.org_id,
                actor_user_id=context.actor_user_id,
            )
        )
        context = replace(context, feature_flags=flags)
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
        flags["mcp_connector_test_readonly_enabled"] = False
        snapshot["access_denied_reason"] = "identity_or_authorization_unavailable"
    chat_creation_enabled = (
        context.feature_flags.get("skill_chat_creation_enabled") is True
        and org_chat_creation_enabled is True
    )
    from .resource_access import resource_boundary
    return replace(
        context,
        authorization_snapshot=snapshot,
        feature_flags={**flags,
            "skill_org_admin": skill_org_admin is True,
            "skill_chat_creation_enabled": chat_creation_enabled},
        resource_access=resource_boundary(executor).as_dict(),
    )


def _identity_flags(value):
    # Existing injected identity checkers may return only the admin boolean.
    if isinstance(value, tuple) and len(value) == 2:
        return value
    return value, True


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

    skill_org_admin = False
    skill_chat_creation_enabled = True
    if context.org_id:
        organization = row("organizations", "status,features", id=context.org_id)
        if organization.get("status") != "active":
            raise PermissionError("organization_inactive")
        skill_chat_creation_enabled = from_features(organization.get("features")).chat_creation_enabled
        member = row("org_members", "status,role", org_id=context.org_id,
                     user_id=context.actor_user_id)
        if member.get("status") != "active":
            raise PermissionError("organization_membership_required")
        skill_org_admin = member.get("role") in {"owner", "admin"}
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
    return skill_org_admin, skill_chat_creation_enabled


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
    from services.tools.result_payload import extension_of
    from services.tools.result import ToolResult
    if isinstance(value, ToolResult):
        value = value.raw
    extension = extension_of(value)
    if extension is not None:
        value = extension["raw"] or {}
    payloads = value.get("emit_payloads", []) if isinstance(value, dict) else getattr(value, "emit_payloads", [])
    ref = value.get("file_ref") if isinstance(value, dict) else getattr(value, "file_ref", None)
    paths = [p["workspace_path"] for p in payloads if isinstance(p, dict) and p.get("workspace_path")]
    if ref is not None:
        paths.append(ref.get("path") if isinstance(ref, dict) else ref.path)
    if not paths:
        return
    files = FileExecutor(get_settings().file_workspace_root, context.workspace_owner_id,
                         context.org_id, create_root=False)
    root = Path(files.workspace_root).resolve()
    for value in paths:
        if not isinstance(value, str) or not value:
            raise PermissionError("replay_resource_reference_invalid: 工具未执行")
        path = Path(value)
        target = path if path.is_absolute() else root / path
        try:
            target.resolve().relative_to(root)
        except ValueError:
            raise PermissionError("replay_resource_scope_mismatch: 工具未执行") from None
