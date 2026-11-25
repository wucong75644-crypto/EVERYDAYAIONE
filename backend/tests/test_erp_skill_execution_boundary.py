"""Regress the real ERP tool calls without borrowing image-plan contracts."""
import json
from types import SimpleNamespace

import pytest

from services.handlers.chat import content_tools, tool_argument_repairs as repairs
from services.tools.definitions.code_schemas import build_code_tools
from services.tools.model_contracts import offered_argument_error, project_model_tool


def offered(*, saved=False):
    tool, = content_tools.model_tools(build_code_tools(), saved_text_available=saved)
    return project_model_tool(tool)['function']


def test_erp_computation_offers_direct_code_without_unusable_source_selector():
    tool = offered()
    assert set(tool['parameters']['properties']) == {'code', 'description'}
    assert 'chat_source_text' not in tool['description']
    assert offered_argument_error('code_execute', {
        'code': "emit_chart({'series': []}, title='销售趋势')", 'description': '绘制销售趋势',
    }, tool['parameters']) is None
    # The actual failed call put its description inside a nonexistent source.
    rejected = json.loads(offered_argument_error('code_execute', {
        'code': 'print(1)', 'content_source': {'description': '绘制销售趋势'},
    }, tool['parameters']))
    assert rejected['submission_state'] == 'not_dispatched'
    assert rejected['allowed_fields'] == ['code', 'description']


def test_registered_skill_text_keeps_source_execution_and_exact_item_contract():
    tool = offered(saved=True)
    assert 'content_source' in tool['parameters']['properties']
    assert 'chat_source_text' in tool['description']
    args = {'code': 'print(chat_source_text)', 'description': '使用已登记原稿',
            'content_source': {'artifact_id': 'content-saved', 'item_index': 1}}
    assert offered_argument_error('code_execute', args, tool['parameters']) is None
    args['content_source'] = {'description': '绘制销售趋势'}
    assert offered_argument_error('code_execute', args, tool['parameters']) is not None


def test_source_failure_is_not_presented_as_parameter_failure_and_survives_recording():
    raw = json.dumps({'code': 'CONTENT_OBJECT_UNAVAILABLE_OR_CHANGED',
        'submission_state': 'not_dispatched', 'recovery': {'action': 'reload_source'},
        'message': '重新读取当前目录，使用选定条目的当前版本。'}, ensure_ascii=False)
    batch = repairs.Batch(repairs.Checkpoint(input_id='input', input_version='a'*64, counts={}, stopped=False),
                          {'call': raw}, {})
    runtime = SimpleNamespace(checkpoint_base={})
    messages, blocks = [], [{'type': 'tool_step', 'tool_call_id': 'call'}]
    batch.record(runtime, messages, blocks)
    assert json.loads(messages[0]['content'])['recovery']['action'] == 'reload_source'
    assert blocks[0]['status'] == 'error'
    assert 'CONTENT_OBJECT_UNAVAILABLE_OR_CHANGED' in blocks[0]['output']
    assert '重新读取当前目录' in blocks[0]['output']
    assert '参数未通过校验' not in blocks[0]['output']


def test_argument_failure_retains_field_reason_in_page_output():
    raw = offered_argument_error('code_execute', {'code': 'print(1)'}, offered()['parameters'])
    assert '$.description' in repairs.display_rejection(raw)
    assert '缺少必填字段' in repairs.display_rejection(raw)
    assert 'MODEL_TOOL_ARGUMENTS_INVALID' in repairs.display_rejection(raw)


def test_chart_contract_describes_injected_functions_and_real_kernel_supports_them():
    from services.sandbox.emit_protocol import install_emit_in_globals

    assert '全局作用域' in offered()['description']
    namespace, outputs = {}, []
    install_emit_in_globals(namespace, outputs)
    exec("emit_chart({'series': [{'type': 'bar', 'data': [1, 2]}]}, title='销售趋势')", namespace)
    assert len(outputs) == 1 and outputs[0]['kind'] == 'chart'
    assert outputs[0]['option']['series'][0]['data'] == [1, 2]
