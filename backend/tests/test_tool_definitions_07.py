"""Full contracts captured at ca4c3d7e before definition ownership migration."""
import json
import hashlib
import importlib
import inspect
import itertools
import subprocess
import sys
from copy import deepcopy
from dataclasses import fields, is_dataclass, replace
from collections.abc import Mapping
from enum import Enum
from pathlib import Path

import pytest

from services.tools import build_legacy_catalog, validate_legacy_coverage

BASELINE = json.loads((Path(__file__).parent / 'fixtures/tool_catalog_07_baseline.json').read_text())
TASK_UPGRADE = json.loads((Path(__file__).parent / 'fixtures/scheduled_task_structured_schema.json').read_text())


def original_schemas(names, view):
    return [TASK_UPGRADE['schema'] if name == 'manage_scheduled_task' else BASELINE['schema_views'].get(view, {}).get(name) or BASELINE['specs'][name]['schema']
            for name in names]


def plain(value):
    if is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(plain(v) for v in value)
    return value.value if isinstance(value, Enum) else value


@pytest.fixture(scope='module')
def catalog():
    return build_legacy_catalog()


@pytest.mark.parametrize('name', BASELINE['specs'])
def test_full_spec_contract_unchanged(catalog, name):
    original = BASELINE['specs'][name]
    actual = plain(catalog.require(name))
    for key, value in original.items():
        if key not in {'source', 'definition_kind'}:
            if name == 'manage_scheduled_task' and key == 'effects':
                # User-authorized lifecycle upgrade after block 07: trusted chat
                # can now submit task definitions. Keep the frozen 07 baseline.
                value = [*value, 'task_definition']
            if name == 'manage_scheduled_task' and key == 'schema':
                value = TASK_UPGRADE['schema']
            assert actual[key] == value, (name, key)


@pytest.mark.parametrize('org', [None, 'org-a'])
def test_complete_helpers_handlers_and_schema_order(catalog, org):
    from config.chat_tools import get_chat_tools, get_core_tools, get_tools_for_mode, get_tools_by_names
    from services.tool_executor import ToolExecutor
    expected = BASELINE['helpers'][str(org)]
    view = 'helpers/' + str(org) + '/'
    assert get_chat_tools(org) == original_schemas(expected['chat'], view + 'chat')
    assert get_core_tools(org) == original_schemas(expected['core'], view + 'core')
    for mode in ('ask', 'auto', 'plan'):
        assert get_tools_for_mode(mode, org) == original_schemas(expected[mode], view + mode)
    assert get_tools_by_names(set(BASELINE['specs']), org) == original_schemas(expected['chat'], view + 'chat')
    executor = ToolExecutor(None, 'actor-a', 'conversation-a', org)
    assert sorted(executor._handlers) == BASELINE['handlers'][str(org)]
    if org:
        assert validate_legacy_coverage(catalog, public_schemas=get_chat_tools(org),
                                        handler_names=executor._handlers) == ()


@pytest.mark.parametrize('domain', BASELINE['phase'])
def test_old_phase_schema_variants_and_internal_visibility(domain):
    from config.phase_tools import build_domain_tools
    assert build_domain_tools(domain) == original_schemas(BASELINE['phase'][domain], 'phase/' + domain)


@pytest.mark.parametrize('name', BASELINE['specs'])
def test_risk_concurrency_cache_and_partial_validator_helpers(catalog, name):
    from config.chat_tools import get_safety_level, is_concurrency_safe
    from config.agent_tools import TOOL_SCHEMAS
    from services.agent.tool_result_cache import ToolResultCache
    expected = BASELINE['specs'][name]
    assert get_safety_level(name).value == expected['risk_level']
    assert is_concurrency_safe(name) == expected['parallelizable']
    assert ToolResultCache.is_cacheable(name) == expected['cacheable']
    assert TOOL_SCHEMAS.get(name) == expected['legacy_validation_schema']


@pytest.mark.parametrize('module', BASELINE['modules'])
def test_old_imports_signatures_and_constant_values(module):
    current = importlib.import_module('config.' + module)
    for name, signature in BASELINE['modules'][module]['functions'].items():
        assert str(inspect.signature(getattr(current, name))) == signature, (module, name)
    for name, digest in BASELINE['modules'][module]['constants'].items():
        value = plain(getattr(current, name))
        if module == 'chat_tools' and name == 'TOOL_SYSTEM_PROMPT':
            # Authorized ST-26 changes only this obsolete scheduling guidance.
            # Restore the frozen old paragraph before checking every other byte.
            description = TASK_UPGRADE['schema']['function']['description']
            assert description in value
            value = value.replace(description, TASK_UPGRADE['legacy_guidance'])
        actual = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        assert actual == digest, (module, name)


def test_task_structured_extension_keeps_every_old_parameter_contract():
    current = deepcopy(TASK_UPGRADE['schema'])
    original = BASELINE['specs']['manage_scheduled_task']['schema']
    properties = current['function']['parameters']['properties']
    assert set(properties) - set(original['function']['parameters']['properties']) == {'definition', 'recipient'}
    properties.pop('definition')
    properties.pop('recipient')
    # Only guidance changes on old parameters; accepted names/types/enums remain exact.
    for name in ('action', 'description'):
        properties[name]['description'] = original['function']['parameters']['properties'][name]['description']
    current['function']['description'] = original['function']['description']
    assert current == original


@pytest.mark.parametrize('name', [n for n, s in BASELINE['specs'].items() if s['schema']])
def test_all_json_parameters_and_existing_coercions(catalog, name):
    from services.agent.tool_args_validator import validate_tool_args
    def sample(prop):
        if prop.get('enum'):
            return prop['enum'][0]
        if prop.get('type') == 'object':
            return {k: sample(v) for k, v in prop.get('properties', {}).items()}
        if prop.get('type') == 'array':
            return [sample(prop.get('items', {}))]
        return {'integer': 1, 'number': 1.0, 'boolean': True}.get(prop.get('type'), 'contract')
    original = BASELINE['specs'][name]['schema']
    params = original['function']['parameters']
    full = {k: sample(v) for k, v in params.get('properties', {}).items()}
    required = {k: full[k] for k in params.get('required', [])}
    coerced = {k: json.dumps(v) if isinstance(v, (bool, int, dict)) else v for k, v in full.items()}
    for args in (full, required, coerced):
        assert validate_tool_args(name, deepcopy(args), [catalog.require(name).to_schema()]) == \
            validate_tool_args(name, deepcopy(args), [original])


@pytest.fixture(scope='module')
def baseline_catalog():
    from services.tools import ToolRegistry, ToolSpec, ToolAvailability, ToolPolicyRules, Exposure
    definitions = []
    for values in BASELINE['specs'].values():
        values = deepcopy(values)
        values['availability'] = ToolAvailability(**values['availability'])
        values['policy_rules'] = ToolPolicyRules(**values['policy_rules'])
        values['exposure'] = Exposure(values['exposure'])
        definitions.append(ToolSpec(**values))
    return ToolRegistry(definitions)


@pytest.mark.parametrize('org,domain,personal,mode,execution,flags', itertools.product(
    [None, 'org-a'], ['general', 'erp'], [True, False], ['ask', 'auto', 'plan'],
    ['interactive', 'scheduled', 'preflight'], [True, False],
))
def test_entire_context_matrix_preserves_authorization(catalog, baseline_catalog, org, domain, personal, mode, execution, flags):
    from services.tools import ToolContext, ToolPolicy, LegacyAdvertisement
    context = ToolContext(
        actor_user_id='actor-a', workspace_owner_id='actor-a' if personal else 'group-owner',
        org_id=org, personal_context_allowed=personal, context_scope='user' if personal else 'channel',
        agent_domain=domain, permission_mode=mode, execution_mode=execution, task_id='task-a',
        feature_flags={k: flags for k in ('file_workspace_enabled', 'sandbox_enabled', 'crawler_enabled')},
        authorized_tool_names=frozenset(BASELINE['specs']),
        authorization_snapshot={'version': 1, 'allowed_tools': list(BASELINE['specs'])},
    )
    def result(registry):
        resolved = registry.resolve(context, policy=ToolPolicy(registry),
                                    advertisement=LegacyAdvertisement(BASELINE['specs']))
        return sorted(resolved.allowed), sorted(resolved.advertised), dict(resolved.denied)
    assert result(catalog) == result(baseline_catalog)


@pytest.mark.parametrize('name', BASELINE['planner'])
def test_planner_uses_spec_and_preserves_snapshot_contract(catalog, name):
    from services.planner import CapabilityRegistry
    spec = catalog.require(name)
    current = CapabilityRegistry.from_tool_schemas([spec.to_schema()]).as_dict()[name]
    expected = BASELINE['planner'][name]
    assert set(current) == set(expected) | {'input_schema'}
    for key, value in expected.items():
        if key not in {'execution_modes', 'supports_readonly_preflight'}:
            assert current[key] == value, (name, key)
    assert current['input_schema'] == spec.to_schema()['function']['parameters']
    assert current['execution_modes'] == list(spec.policy_rules.execution_modes)
    assert current['supports_readonly_preflight'] == ('preflight' in spec.policy_rules.execution_modes)
    assert CapabilityRegistry.from_names([name]).as_dict()[name] == current
    changed = spec.to_schema()
    changed['function']['parameters'] = {'type': 'object', 'properties': {'invented': {'type': 'string'}}}
    assert CapabilityRegistry.from_tool_schemas([changed]).as_dict()[name] == current


def test_planner_preflight_and_frozen_authorization_agree(catalog, monkeypatch):
    from core.config import get_settings
    from services.planner import CapabilityRegistry, PlanCandidate, PlanStep, PlannerFramework
    from services.scheduler.scheduled_task_workflow import preflight_allowed_tool_names, validate_plan, ScheduledExecutionPolicy
    settings = get_settings()
    for flag in ('file_workspace_enabled', 'sandbox_enabled', 'crawler_enabled'):
        monkeypatch.setattr(settings, flag, True)
    names = preflight_allowed_tool_names('org-a')
    capabilities = CapabilityRegistry.from_specs(s for s in catalog.specs() if s.core)
    assert names == {name for name, d in capabilities.as_dict().items() if d['supports_readonly_preflight']}
    assert not names & {'restore_file', 'image_agent', 'manage_scheduled_task', 'file_delete'}
    for name in names:
        candidate = PlanCandidate(target={}, input_contract={}, output_contract={},
                                  steps=(PlanStep('s1', 'read', (name,)),), candidate_tools=(name,))
        release = PlannerFramework(capabilities).release(candidate, execution_mode='preflight')
        assert release.tool_policy['version'] == 1
        assert release.tool_policy['allowed_tools'] == [name]
    raw = {'objective': 'read', 'allowed_tools': ['web_search'],
           'steps': [{'tools': ['web_search'], 'intent': 'read'}]}
    _, policy = validate_plan(raw, available_tools=names, timeout_sec=120)
    assert ScheduledExecutionPolicy.from_dict(policy.as_dict(), timeout_sec=120).as_dict() == policy.as_dict()
    assert set(policy.as_dict()) == {'version', 'allowed_tools', 'required_tools', 'tool_timeout_sec',
                                    'erp_step_timeout_sec', 'final_reserve_sec', 'allow_empty_result'}


@pytest.mark.parametrize('name,args,domain', [
    ('file_delete', {'files': ['uploads/approved.csv']}, 'general'),
    ('trigger_erp_sync', {'sync_type': 'order'}, 'erp'),
])
def test_prior_confirmation_binding_remains_valid(catalog, baseline_catalog, name, args, domain):
    from services.tools import ToolContext, ToolPolicy, ToolConfirmation
    context = ToolContext(actor_user_id='actor-a', workspace_owner_id='actor-a', org_id='org-a',
                          context_scope='user', personal_context_allowed=True, confirmation_available=True,
                          agent_domain=domain, permission_mode='ask',
                          execution_mode='interactive', conversation_id='c1', call_id='call1',
                          feature_flags={'file_workspace_enabled': True})
    before = ToolPolicy(baseline_catalog).decide(name, context, args)
    after = ToolPolicy(catalog).decide(name, context, args)
    assert before.outcome == after.outcome == 'require_confirmation'
    assert before.confirmation_binding == after.confirmation_binding
    approval = ToolConfirmation(binding=before.confirmation_binding, status='approved')
    assert ToolPolicy(catalog).decide(name, context, args, confirmation=approval).outcome == 'allow'


def test_definition_projections_cannot_mutate_runtime_or_other_requests(catalog, monkeypatch):
    from config import chat_tools, tool_domains, agent_tools
    from services.agent.tool_result_cache import ToolResultCache
    monkeypatch.setitem(chat_tools._SAFETY_LEVELS, 'file_delete', chat_tools.SafetyLevel.SAFE)
    monkeypatch.setitem(tool_domains.TOOL_DOMAINS, 'erp_execute', tool_domains.ToolDomain.GENERAL)
    monkeypatch.setitem(agent_tools.TOOL_SCHEMAS, 'file_delete', {})
    monkeypatch.setattr(chat_tools, '_CONCURRENT_SAFE_TOOLS', {'file_delete'})
    assert chat_tools.get_safety_level('file_delete') == chat_tools.SafetyLevel.DANGEROUS
    assert not chat_tools.is_concurrency_safe('file_delete')
    assert not ToolResultCache.is_cacheable('file_delete')
    assert not tool_domains.can_access('erp_execute', 'general')
    schema = chat_tools.get_chat_tools('org-a')[0]
    schema['function']['parameters']['properties'].clear()
    assert chat_tools.get_chat_tools('org-a')[0] == BASELINE['specs']['erp_info_query']['schema']
    assert build_legacy_catalog().require('file_delete').to_legacy_validation_schema() != {}
    with pytest.raises(TypeError):
        catalog.require('erp_api_search').schema_variants['erp_search']['function']['name'] = 'other'


def test_cache_eligibility_does_not_infer_from_concurrency(catalog, monkeypatch):
    from services.tools import ToolRegistry
    from services.agent.tool_result_cache import ToolResultCache
    from config import chat_tools
    spec = replace(catalog.require('code_execute'), cacheable=False, parallelizable=True)
    changed = ToolRegistry([spec])
    monkeypatch.setattr('services.tools.catalog.definition_registry', lambda: changed)
    monkeypatch.setattr(chat_tools, 'definition_registry', lambda: changed)
    assert chat_tools.is_concurrency_safe('code_execute')
    assert not ToolResultCache.is_cacheable('code_execute')


def test_each_schema_resource_has_exactly_one_spec(catalog):
    from services.tools.definitions import erp_schemas, erp_local_schemas, file_schemas, code_schemas, crawler_schemas
    for group, schemas in (
        ('erp_tools', erp_schemas.build_erp_tools()),
        ('erp_local_tools', erp_local_schemas.build_local_tools()),
        ('file_tools', file_schemas.build_file_tools()),
        ('code_tools', code_schemas.build_code_tools()),
        ('crawler_tools', crawler_schemas.build_crawler_tools()),
    ):
        names = [s['function']['name'] for s in schemas]
        assert len(names) == len(set(names))
        assert set(names) == {s.name for s in catalog.specs() if group in s.catalog_groups}
        assert all(schema == catalog.require(schema['function']['name']).to_schema() for schema in schemas)


def test_legacy_diagnostic_imports_are_compatible_projections(catalog):
    from services.tools.legacy import _COMPATIBILITY, _EFFECTS, _INTERNAL_SOURCES, legacy_policy_rules
    assert _COMPATIBILITY == {n: tuple(s['compatibility_notes']) for n, s in BASELINE['specs'].items()
                              if s['compatibility_notes']}
    assert set(_EFFECTS) == {'code_execute', 'file_analyze', 'restore_file', 'manage_scheduled_task',
                            'fetch_all_pages', 'get_conversation_context'}
    assert all(value == catalog.require(name).effects for name, value in _EFFECTS.items())
    assert _INTERNAL_SOURCES == {name: BASELINE['specs'][name]['source'] for name in _INTERNAL_SOURCES}
    assert legacy_policy_rules('file_search', frozenset({'file_search'})) == catalog.require('file_search').policy_rules


@pytest.mark.parametrize('first', [
    'config.chat_tools', 'config.agent_tools', 'config.erp_tools', 'config.common_tools',
    'config.tool_domains', 'services.planner.registry', 'services.tools',
    'services.agent.tool_executor', 'services.scheduler.scheduled_task_workflow',
])
def test_fresh_process_import_orders_have_no_initialization_cycle(first):
    script = '''
import importlib, sys
importlib.import_module(sys.argv[1])
from config.chat_tools import get_chat_tools
from services.tools import build_tool_catalog
from services.tool_executor import ToolExecutor
assert len(get_chat_tools('org-a')) == 33
assert len(build_tool_catalog().specs()) == 35
assert len(ToolExecutor(None, 'actor-a', 'c1', 'org-a')._handlers) == 35
'''
    run = subprocess.run([sys.executable, '-c', script, first], text=True, capture_output=True, timeout=30)
    assert run.returncode == 0, run.stderr
