"""Default chat search covers uploaded history and workspace, within grants."""
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.agent.tool_executor import ToolExecutor
from services.file_executor import FileExecutor
from services.handlers.resource_manifest import ResourceAsset, ResourceManifest
from services.tools.resource_access import ResourceAccessBoundary, ResourceRule
from tests.test_file_target_execution import fixture
from tests.test_tool_production_integration import invoke, tc, loop_for
from tests.tool_runtime_support import IdentityDB


def current(e, files, paths=()):
    e.resource_manifest = ResourceManifest('task1', 'input1', tuple(
        ResourceAsset(str(i), p.name, str(p.relative_to(files.workspace_root)), 'text/csv', 4, '')
        for i, p in enumerate(paths)), 'input_message')


@pytest.mark.parametrize('entry', ['legacy', 'chat', 'loop'])
@pytest.mark.parametrize('mode', ['ask', 'auto', 'plan'])
async def test_default_search_finds_current_history_and_workspace_once(fixture, monkeypatch, entry, mode):
    e, files, create = fixture
    e.permission_mode = mode
    fresh = create('上传/2026-09/销售本轮.csv')
    old = create('上传/2026-08/销售历史.csv')
    other = create('报表/销售汇总.csv')
    current(e, files, [fresh])
    outputs = await invoke(entry, e, [tc('file_search', {'keyword': '销售'}, 'search')], monkeypatch)
    text = str(outputs)
    for path in (fresh, old, other):
        assert text.count(str(path.relative_to(files.workspace_root))) == 1
    assert text.count('resource_ref:') == 3
    e.tool_confirmer.assert_not_awaited()


@pytest.mark.parametrize('args', [
    {'keyword': '6月23日各平台订单周同比_c7a21c.xlsx'},
    {'path': '6月23日各平台订单周同比_c7a21c.xlsx'},
])
async def test_captured_fresh_model_arguments_find_file_without_scope(fixture, args):
    e, files, create = fixture
    target = create('上传/2026-09/6月23日各平台订单周同比_c7a21c.xlsx')
    current(e, files)
    found = await e.execute('file_search', args)
    assert found.status == 'success' and target.name in found.summary
    assert 'resource_ref:' in found.summary
    assert target.exists()


async def test_explicit_current_restricts_but_does_not_poison_next_default(fixture):
    e, files, create = fixture
    fresh = create('上传/销售本轮.csv')
    create('上传/过去/销售历史.csv')
    current(e, files, [fresh])
    restricted = await e.execute('file_search', {'keyword': '销售', 'scope': 'current'})
    assert '销售本轮.csv' in restricted.summary and '销售历史.csv' not in restricted.summary
    default = await e.execute('file_search', {'keyword': '销售'})
    assert '销售本轮.csv' in default.summary and '销售历史.csv' in default.summary


async def test_default_full_name_does_not_select_current_before_history_duplicate(fixture):
    e, files, create = fixture
    fresh = create('上传/本轮/销售.csv')
    create('上传/历史/销售.csv')
    current(e, files, [fresh])
    result = await e.tool_runtime.execute('file_search', {'path': '销售.csv'})
    assert 'RESOURCE_AMBIGUOUS' in str(result.exception)
    assert not result.execution.handler_started
    assert all(p in str(result.exception) for p in ['上传/本轮/销售.csv', '上传/历史/销售.csv'])
    found = await e.execute('file_search', {'path': '销售.csv', 'scope': 'current'})
    assert found.status == 'success' and '上传/本轮/销售.csv' in found.summary


@pytest.mark.parametrize('execution', ['scheduled', 'preflight'])
async def test_noninteractive_default_does_not_expand_finite_grant(fixture, execution):
    e, files, create = fixture
    fresh = create('approved/销售本轮.csv')
    create('private/销售历史.csv')
    current(e, files, [fresh])
    e.execution_mode, e.task_id = execution, 'task1'
    e.allowed_tool_names = {'file_search'}
    e.tool_policy_snapshot = {'version': 1, 'allowed_tools': ['file_search']}
    # A grant to browse a directory still does not change the headless default
    # from current manifest to all authorized paths.
    e.resource_access_boundary = ResourceAccessBoundary((ResourceRule(('list',), directories=('.',)),), 'test', True)
    found = await e.execute('file_search', {'keyword': '销售'})
    assert '销售本轮.csv' in str(found) and '销售历史.csv' not in str(found)
    assert found.metadata['resource_scope'] == 'current'


async def test_interactive_finite_boundary_filters_default_search(fixture):
    e, files, create = fixture
    create('approved/销售.csv')
    create('private/销售.csv')
    current(e, files)
    e.resource_access_boundary = ResourceAccessBoundary((ResourceRule(('list',), paths=('approved/销售.csv',)),), 'test', True)
    found = await e.execute('file_search', {'keyword': '销售'})
    assert 'approved/销售.csv' in found.summary and 'private/' not in found.summary


async def test_default_search_is_owner_and_organization_isolated(fixture):
    from core.config import get_settings
    e, files, create = fixture
    create('销售本人.csv')
    current(e, files)
    for user, org in [('other-user', 'o1'), ('u1', 'other-org')]:
        foreign = FileExecutor(get_settings().file_workspace_root, user, org)
        from pathlib import Path
        (Path(foreign.workspace_root) / '销售机密.csv').write_text('private')
    result = await e.execute('file_search', {'keyword': '销售'})
    assert '销售本人.csv' in result.summary and '机密' not in result.summary


async def test_group_default_search_uses_group_owner_not_actor(fixture):
    from core.config import get_settings
    from pathlib import Path
    _, _, create = fixture
    create('销售个人.csv')
    files = FileExecutor(get_settings().file_workspace_root, 'channel-owner', 'o1')
    target = Path(files.workspace_root) / '销售群.csv'
    target.write_text('group')
    db = IdentityDB(scope_type='channel', source='wecom', scope_id='channel1')
    db.conversation['user_id'] = None
    e = ToolExecutor(db, 'u1', str(uuid4()), 'o1', workspace_user_id='channel-owner',
        context_scope='channel', personal_context_allowed=False,
        execution_scope=SimpleNamespace(actor_user_id='u1', workspace_owner_id='channel-owner'), channel_scope_id='channel1')
    current(e, files)
    found = await e.execute('file_search', {'keyword': '销售'})
    assert '销售群.csv' in found.summary and '销售个人.csv' not in found.summary


@pytest.mark.parametrize('approved', [False, True])
async def test_default_search_delete_still_requires_confirmation(fixture, approved):
    e, files, create = fixture
    target = create('上传/旧附件.csv')
    current(e, files)
    e.tool_confirmer.return_value = approved
    found = await e.execute('file_search', {'keyword': '旧附件'})
    reference = re.search(r'resource_ref: (\S+)', found.summary)[1]
    result = await e.tool_runtime.execute('file_delete', {'resource_refs': [reference]})
    assert result.execution.handler_started is approved
    assert target.exists() is not approved
    e.tool_confirmer.assert_awaited_once()
    assert e._handlers['file_delete'].await_count == int(approved)


async def test_new_model_loop_omitted_scope_reaches_real_handler(fixture):
    from services.agent.execution_budget import ExecutionBudget
    e, files, create = fixture
    create('上传/旧附件.csv')
    current(e, files)
    loop, ctx = loop_for(e)
    loop._stream_one_turn = AsyncMock(side_effect=[
        ({0: tc('file_search', {'keyword': '旧附件'}, 'search')}, '', 1, 1, 0),
        ({}, '找到了', 1, 1, 0),
    ])
    result = await loop.run([], e.tool_runtime.advertised(), [], ctx, ExecutionBudget(max_turns=4))
    assert result.text == '找到了'
    assert any(m['role']=='tool' and '旧附件.csv' in m['content'] for m in ctx.messages)


async def test_empty_current_result_cannot_mask_default_workspace_cache(fixture):
    from services.agent.tool_result_cache import ToolResultCache
    e, files, create = fixture
    create('上传/旧附件.csv')
    current(e, files)
    cache = ToolResultCache()
    empty = await e.tool_runtime.execute('file_search', {'keyword': '旧附件', 'scope': 'current'}, cache=cache)
    found = await e.tool_runtime.execute('file_search', {'keyword': '旧附件'}, cache=cache)
    assert empty.status == 'empty' and found.status == 'success'
    assert not found.execution.cached


async def test_direct_handler_ambiguity_reports_default_workspace(fixture):
    e, files, create = fixture
    create('a/报表.csv')
    create('b/报表.csv')
    current(e, files)
    result = await e._file_dispatch('file_search', {'path': '报表.csv'})
    assert result.status == 'error'
    assert result.metadata['error_code'] == 'RESOURCE_AMBIGUOUS'
    assert result.metadata['resource_scope'] == 'workspace'
