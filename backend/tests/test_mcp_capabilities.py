"""Offline logical capabilities and the reserved executor security boundary."""
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from services.skills.contracts import SkillCatalogMetadata, SkillError
from services.skills.resolver import skill_tool_ceiling
from services.tools import ToolCall, ToolDispatcher, ToolExecutionService, ToolRegistry, build_legacy_catalog
from services.tools.mcp import MCPConnectorConfig, MCPConnectorState, transition
from tests.test_tool_execution import context
from tests.test_skill_runtime import Source, item, state, activate
from tests.test_skill_storage import PACKAGE, BODY, DOCUMENT, publication
from services.skills.storage import SkillStorage


def test_catalog_capabilities_are_explicit_and_registered():
    registry = build_legacy_catalog()
    assert all(s.capability for s in registry.specs())
    assert registry.capability_tools(['workspace.file.search']) == {'file_search'}
    with pytest.raises(ValueError, match='UNKNOWN_CAPABILITY'):
        registry.capability_tools(['crm.customer.read'])


def test_reviewed_mcp_capability_mapping_is_stable_while_global_flag_is_off():
    from services.tools.catalog import build_capability_catalog
    from services.tools.mcp_allowlist import TOOL_NAME
    registry = build_capability_catalog()
    assert registry.capability_tools(['test.sample.read']) == {TOOL_NAME}
    assert build_legacy_catalog().get(TOOL_NAME) is None


def test_capabilities_only_narrow_and_require_available_tools():
    metadata = SkillCatalogMetadata(allowed_capabilities=('workspace.file.search', 'workspace.file.delete'))
    assert skill_tool_ceiling(metadata, {'file_search', 'file_delete'}, {'file_search'}) == {'file_search'}
    metadata = metadata.model_copy(update={'allowed_tool_names': ('file_delete',)})
    assert not skill_tool_ceiling(metadata, {'file_search', 'file_delete'}, {'file_search'})
    with pytest.raises(SkillError, match='REQUIRED_CAPABILITY_UNAVAILABLE'):
        skill_tool_ceiling(SkillCatalogMetadata(required_capabilities=('workspace.file.delete',)),
                           {'file_delete'}, set())
    with pytest.raises(SkillError, match='UNKNOWN_CAPABILITY'):
        skill_tool_ceiling(SkillCatalogMetadata(allowed_capabilities=('unknown.read',)), {'file_search'}, {'file_search'})


def test_required_mcp_capability_requires_current_available_and_authorized_tool():
    from services.tools.mcp_allowlist import TOOL_NAME
    metadata = SkillCatalogMetadata(required_capabilities=('test.sample.read',))
    with pytest.raises(SkillError, match='REQUIRED_CAPABILITY_UNAVAILABLE'):
        skill_tool_ceiling(metadata, {TOOL_NAME}, {TOOL_NAME}, available_tool_names=set())
    assert skill_tool_ceiling(metadata, {TOOL_NAME}, {TOOL_NAME},
                              available_tool_names={TOOL_NAME}) == {TOOL_NAME}
    with pytest.raises(SkillError, match='REQUIRED_CAPABILITY_UNAVAILABLE'):
        skill_tool_ceiling(metadata, {TOOL_NAME}, set(), available_tool_names={TOOL_NAME})


@pytest.mark.parametrize('field,value', [('url', 'https://example.test'), ('token', 'fixture'),
    ('oauth_token', 'fixture'), ('connector_id', 'test-readonly'),
    ('handler_key', 'arbitrary'), ('executor_type', 'mcp'), ('server', 'remote'),
    ('skip_confirmation', True), ('confirmation_required', False)])
def test_skill_and_connector_reject_execution_configuration(field, value):
    with pytest.raises(ValidationError):
        SkillCatalogMetadata(**{field: value})
    connector_fields = {"connector_id": "crm", "profile_id": "reviewed", "display_name": "CRM"}
    if field == "connector_id":
        # Connector identity belongs to the platform connector contract; Skill
        # metadata is the boundary that must reject it.
        assert MCPConnectorConfig(**(connector_fields | {field: value})).connector_id == value
    else:
        with pytest.raises(ValidationError):
            MCPConnectorConfig(**(connector_fields | {field: value}))


def test_skill_rejects_url_capability_and_required_outside_allowed():
    for raw in [{'allowed_capabilities': ['https://example.test']},
                {'required_capabilities': ['workspace.file.delete'], 'allowed_capabilities': ['workspace.file.search']}]:
        with pytest.raises(ValidationError):
            SkillCatalogMetadata(**raw)


def test_unknown_capability_rejected_before_publication():
    document = DOCUMENT.replace('description: 报表说明', 'description: 报表说明\ncatalog:\n  allowed_capabilities: [unknown.read]')
    with pytest.raises(SkillError, match='UNKNOWN_CAPABILITY'):
        SkillStorage.validate_bytes(PACKAGE, publication(document, BODY), document.encode())


async def test_activation_and_replay_preserve_capability_ceiling():
    source = Source([item(tools=(), allowed_capabilities=('workspace.file.search',))])
    runtime = state(source)
    await runtime.initialize()
    assert (await runtime.activate(activate()))['ok']
    assert runtime.effective_allowed_tool_names == {'file_search'}
    restored = state(source, authorized_tool_names=set())
    await restored.initialize(runtime.checkpoint())
    assert not restored.effective_allowed_tool_names


async def test_unknown_capability_activation_fails_closed():
    runtime = state(Source([item(tools=(), allowed_capabilities=('unknown.read',))]))
    await runtime.initialize()
    assert (await runtime.activate(activate()))['code'] == 'SKILL_UNKNOWN_CAPABILITY'
    assert not runtime.active


async def test_mcp_skill_activation_is_blocked_when_connector_capability_is_unavailable():
    from services.tools.mcp_allowlist import TOOL_NAME
    source = Source([item(tools=(), required_capabilities=('test.sample.read',))])
    runtime = state(source, platform_tool_names={TOOL_NAME}, authorized_tool_names={TOOL_NAME},
                    available_tool_names=set())
    await runtime.initialize()
    assert (await runtime.activate(activate()))['code'] == 'SKILL_REQUIRED_CAPABILITY_UNAVAILABLE'
    assert not runtime.active


async def test_resume_rejects_changed_capability_to_tool_mapping(monkeypatch):
    from services.skills.runtime import SkillReplayError
    from services.tools.catalog import build_capability_catalog
    from services.tools.mcp_allowlist import TOOL_NAME

    source = Source([item(tools=(), required_capabilities=('test.sample.read',))])
    runtime = state(source, platform_tool_names={TOOL_NAME}, authorized_tool_names={TOOL_NAME},
                    available_tool_names={TOOL_NAME})
    await runtime.initialize()
    assert (await runtime.activate(activate()))['ok']
    checkpoint = runtime.checkpoint()
    current = build_capability_catalog()
    changed = ToolRegistry([
        replace(spec, capability='test.sample.changed') if spec.name == TOOL_NAME else spec
        for spec in current.specs()
    ])
    monkeypatch.setattr('services.tools.catalog.build_capability_catalog', lambda: changed)
    resumed = state(source, platform_tool_names={TOOL_NAME}, authorized_tool_names={TOOL_NAME},
                    available_tool_names={TOOL_NAME})
    with pytest.raises(SkillReplayError, match='SKILL_UNKNOWN_CAPABILITY'):
        await resumed.initialize(checkpoint)


async def test_mcp_cannot_bypass_policy_or_execute_injected_handler():
    spec = replace(build_legacy_catalog().require('search_knowledge'), executor_type='mcp')
    handler = AsyncMock()
    service = ToolExecutionService(ToolRegistry([spec]), ToolDispatcher({('mcp', spec.handler_key): handler}))
    disabled = await service.execute(ToolCall('disabled', spec.name, {}), context())
    assert disabled.decision.reason == 'feature_unavailable:mcp_connectors_enabled'
    enabled = context(feature_flags={'mcp_connectors_enabled': True})
    denied = await service.execute(ToolCall('denied', spec.name, {}), replace(enabled, authorized_tool_names=frozenset()))
    assert denied.decision.outcome == 'deny'
    allowed = await service.execute(ToolCall('allowed', spec.name, {}), enabled)
    assert allowed.decision.outcome == 'allow'
    with pytest.raises(RuntimeError, match='MCP_EXECUTOR_NOT_CONNECTED'):
        allowed.to_legacy()
    assert not allowed.execution.handler_started
    handler.assert_not_awaited()


def test_offline_state_machine_and_feature_gate():
    with pytest.raises(ValueError, match='FEATURE_DISABLED'):
        transition(MCPConnectorState.DISABLED, 'configure')
    configured = transition(MCPConnectorState.DISABLED, 'configure', enabled=True)
    assert configured == MCPConnectorState.CONFIGURED
    assert transition(configured, 'fail', enabled=True) == MCPConnectorState.ERROR
    assert transition(configured, 'disable') == MCPConnectorState.DISABLED
    with pytest.raises(ValueError, match='NOT_SUPPORTED'):
        transition(configured, 'connect', enabled=True)
