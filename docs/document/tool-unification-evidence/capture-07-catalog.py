"""Capture contracts from an unmodified checkout (no business IO)."""
import importlib
import inspect
import hashlib
import json
import subprocess
import sys
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from collections.abc import Mapping


def plain(value):
    if is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value.value if isinstance(value, Enum) else value


def capture(base=None):
    from config import chat_tools, phase_tools
    from services.tools import build_legacy_catalog
    from services.tool_executor import ToolExecutor
    from services.planner import CapabilityRegistry
    modules = {}
    for name in ('chat_tools', 'tool_domains', 'agent_tools', 'common_tools', 'erp_tools',
                 'erp_local_tools', 'file_tools', 'code_tools', 'crawler_tools'):
        module = importlib.import_module('config.' + name)
        modules[name] = {
            'constants': {k: plain(v) for k, v in vars(module).items()
                          if k.isupper() and isinstance(v, (str, set, frozenset, dict))},
            'functions': {k: str(inspect.signature(v)) for k, v in vars(module).items()
                          if inspect.isfunction(v) and not k.startswith('__')},
        }
    result = {
        'base': base or subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'specs': {s.name: plain(s) for s in build_legacy_catalog().specs()},
        'helpers': {str(org): {
            'chat': chat_tools.get_chat_tools(org), 'core': chat_tools.get_core_tools(org),
            **{mode: chat_tools.get_tools_for_mode(mode, org) for mode in ('ask', 'auto', 'plan')},
        } for org in (None, 'org-a')},
        'phase': {d: phase_tools.build_domain_tools(d) for d in ('erp', 'crawler', 'computer', 'unknown')},
        'handlers': {str(org): sorted(ToolExecutor(None, 'actor-a', 'conversation-a', org)._handlers)
                     for org in (None, 'org-a')},
        'planner': CapabilityRegistry.from_tool_schemas(chat_tools.get_chat_tools('org-a')).as_dict(),
        'modules': modules,
    }
    return compact(result)


def compact(result):
    """Store each complete schema once; repeated projections retain ordered names."""
    result['schema_views'] = {}
    for owner, values in [('helpers/' + org, helper) for org, helper in result['helpers'].items()]:
        for view, schemas in values.items():
            result['schema_views'][owner + '/' + view] = {
                s['function']['name']: s for s in schemas
                if s != result['specs'][s['function']['name']]['schema']
            }
            values[view] = [s['function']['name'] for s in schemas]
    for view, schemas in result['phase'].items():
        result['schema_views']['phase/' + view] = {
            s['function']['name']: s for s in schemas
            if s != result['specs'].get(s['function']['name'], {}).get('schema')
        }
        result['phase'][view] = [s['function']['name'] for s in schemas]
    for value in result['planner'].values():
        value.pop('input_schema')  # Identical to the full Spec parameters captured above.
    for module in result['modules'].values():
        module['constants'] = {name: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                               for name, value in module['constants'].items()}
    return result


if __name__ == '__main__':
    result = capture(sys.argv[2] if len(sys.argv) > 2 else None)
    Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
