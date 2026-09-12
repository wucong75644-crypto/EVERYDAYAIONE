"""Read-only source/coverage audit; writes only the report next to this script."""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from config.chat_tools import get_chat_tools
from services.tools import build_tool_catalog, validate_legacy_coverage
from services.tool_executor import ToolExecutor
from services.planner import CapabilityRegistry


ROOT = Path(__file__).resolve().parents[3]
BASE = 'ca4c3d7e6a89ef34412cf405efc2d4fb0c1350e2'
OUT = Path(__file__).resolve().parent


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def source_at(path):
    return git('show', f'{BASE}:{path}')


def function_map(source):
    result = {}
    def visit(node, prefix=''):
        for child in getattr(node, 'body', []):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                if not isinstance(child, ast.ClassDef):
                    result[name] = ast.dump(child, include_attributes=False)
                visit(child, name + '.')
    visit(ast.parse(source))
    return result


def main():
    subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', BASE, 'HEAD'], check=True)
    closures = ['2e8fdb2d', '8e74f57d', '0f65d72d', '6c0737ab', 'cdba58f9', 'ca4c3d7e']
    prerequisites = []
    for number, merge in enumerate(closures, 1):
        subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', merge, BASE], check=True)
        candidate = git('rev-parse', merge + '^2')
        assert git('rev-parse', merge + '^{tree}') == git('rev-parse', candidate + '^{tree}')
        assert (ROOT / f'docs/document/TOOL_UNIFICATION_ACCEPTANCE_{number:02}.md').exists()
        prerequisites.append({'block': number, 'main_merge': git('rev-parse', merge), 'accepted_candidate': candidate,
                              'ancestor': True, 'tree_equal': True})

    registry = build_tool_catalog()
    executor = ToolExecutor(None, 'actor-a', 'c1', 'org-a')
    issues = validate_legacy_coverage(registry, public_schemas=get_chat_tools('org-a'), handler_names=executor._handlers)
    assert not issues
    assert len(registry.specs()) == 35 and len(get_chat_tools('org-a')) == 33
    assert all(s.definition_kind == 'explicit' for s in registry.specs())
    assert len({s.handler_key for s in registry.specs()}) == 35
    inventory = []
    for s in registry.specs():
        handler = executor._handlers[s.handler_key]
        inventory.append({
            'name': s.name, 'source': s.source, 'groups': s.catalog_groups,
            'exposure': s.exposure.value, 'domain': s.domain, 'risk': s.risk_level,
            'parallelizable': s.parallelizable, 'cacheable': s.cacheable,
            'operation': s.policy_rules.operation, 'execution_modes': s.policy_rules.execution_modes,
            'plan_allowed': s.policy_rules.plan_allowed, 'effects': s.effects,
            'requires_org': s.availability.requires_org, 'personal_context': s.availability.requires_personal_context,
            'feature_flags': s.availability.feature_flags, 'core': s.core,
            'handler': handler.__module__ + '.' + handler.__qualname__, 'handler_key': s.handler_key,
            'schema_variants': list(s.schema_variants), 'compatibility_notes': s.compatibility_notes,
        })

    changed = git('diff', BASE, '--name-only').splitlines()
    protected = ('backend/services/agent/', 'backend/services/kuaimai/', 'backend/services/sandbox/',
                 'backend/services/handlers/', 'backend/services/media_tool_executor.py')
    assert [p for p in changed if p.startswith(protected)] == ['backend/services/agent/tool_result_cache.py']
    preserved = {}
    for path, exclusions in {
        'backend/services/agent/tool_result_cache.py': {'ToolResultCache.is_cacheable'},
        'backend/services/scheduler/scheduled_task_workflow.py': set(),
    }.items():
        before, after = function_map(source_at(path)), function_map((ROOT / path).read_text())
        assert all(after[name] == code for name, code in before.items() if name not in exclusions)
        preserved[path] = sorted(set(before) - exclusions)
    unchanged = [
        'backend/services/tools/policy.py', 'backend/services/tools/action_rules.py',
        'backend/services/tools/runtime.py', 'backend/services/tools/runtime_context.py',
        'backend/services/tools/execution.py', 'backend/services/tools/dispatcher.py',
        'backend/services/tools/legacy_handler.py', 'backend/services/tools/result.py',
        'backend/services/tools/result_payload.py', 'backend/services/tool_invocation_store.py',
        'backend/services/planner/contracts.py', 'backend/services/planner/validator.py',
        'backend/services/agent/tool_selector.py', 'backend/config/phase_tools.py',
        'backend/config/conversation_control_tools.py',
        'backend/services/scheduler/scheduled_task_change_adapter.py',
    ]
    for path in unchanged:
        assert (ROOT / path).read_text().strip() == source_at(path)
    # The semantic tag table only gains documentation of its separate role.
    path = 'backend/config/tool_registry.py'
    def without_doc(source):
        tree = ast.parse(source)
        tree.body = tree.body[1:]
        return ast.dump(tree, include_attributes=False)
    assert without_doc(source_at(path)) == without_doc((ROOT / path).read_text())
    for path in (ROOT / 'backend/services/tools/definitions').glob('*.py'):
        source = path.read_text()
        assert not re.search(r'from config\.(chat_tools|tool_domains|agent_tools|common_tools|erp_tools|file_tools|code_tools|crawler_tools)', source)

    production_calls = []
    for path in (ROOT / 'backend/services').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {'ToolExecutor', 'ToolLoopExecutor'}:
                production_calls.append({'path': str(path.relative_to(ROOT)), 'line': node.lineno, 'constructor': node.func.id})
    assert Counter(item['constructor'] for item in production_calls) == {'ToolExecutor': 2, 'ToolLoopExecutor': 1}
    baseline = json.loads((ROOT / 'backend/tests/fixtures/tool_catalog_07_baseline.json').read_text())
    descriptors = CapabilityRegistry.from_tool_schemas(get_chat_tools('org-a')).as_dict()
    differences = {name: {key: {'before': value, 'after': descriptors[name][key]}
                          for key, value in descriptor.items() if descriptors[name][key] != value}
                   for name, descriptor in baseline['planner'].items()}
    differences = {name: changes for name, changes in differences.items() if changes}
    assert all(set(changes) <= {'execution_modes', 'supports_readonly_preflight'} for changes in differences.values())

    new = git('ls-files', '--others', '--exclude-standard').splitlines()
    fingerprints = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                    for path in sorted(set(changed + new)) if path.startswith('backend/')}
    with tempfile.TemporaryDirectory(prefix='tool07-recapture-') as temporary:
        archive = subprocess.check_output(['git', '-C', str(ROOT), 'archive', BASE, 'backend'])
        subprocess.run(['tar', '-x', '-C', temporary], input=archive, check=True)
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(Path(temporary) / 'backend') + os.pathsep + environment.get('PYTHONPATH', '')
        recaptured = Path(temporary) / 'catalog.json'
        subprocess.run([sys.executable, str(OUT / 'capture-07-catalog.py'), str(recaptured), BASE],
                       cwd=ROOT, env=environment, check=True)
        assert (ROOT / 'backend/tests/fixtures/tool_catalog_07_baseline.json').read_bytes() == recaptured.read_bytes()
    report = {
        'base': BASE, 'head': git('rev-parse', 'HEAD'), 'branch': git('branch', '--show-current'),
        'tested_version': 'HEAD plus working-tree diff; exact backend files are fingerprinted below',
        'prerequisites': prerequisites, 'coverage_issues': issues, 'inventory': inventory,
        'public': 33, 'handler_only': 2, 'registered': 35, 'unregistered': 0, 'duplicates': 0,
        'definition_groups': dict(Counter(s.source.split('.')[-2] for s in registry.specs())),
        'production_constructors': production_calls, 'unchanged_sources': unchanged,
        'preserved_functions': preserved, 'semantic_selection_ast_unchanged': True,
        'baseline_recapture_identical': True, 'planner_descriptor_differences': differences,
        'source_fingerprints': fingerprints,
    }
    (OUT / '07-source-checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('PASS: 35 mappings; 0 missing/duplicate; 01–06 ancestry/tree; handler bodies and execution paths unchanged; baseline recapture identical')


if __name__ == '__main__':
    main()
