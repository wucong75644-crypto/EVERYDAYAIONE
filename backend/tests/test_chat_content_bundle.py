"""Explicit input mappings, format independence and private no-provider replay."""
import copy
import hashlib
import json
from uuid import uuid4

import pytest

from services.handlers.chat_input_handoff import assemble, check_outputs, digest, line_starts, rebind_call
from services.handlers.chat import content_tools
from services.handlers.chat_content_bundle import bundle, verify
from services.handlers.chat_image_request import freeze_image_request, verify_frozen_request
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_model_bridge import call


PARAMETERS = {'type': 'object', 'properties': {'prompt': {'type': 'string'},
    'ratio': {'type': 'string', 'enum': ['1:1', '3:4']}, 'references': {'type': 'array'},
    'query': {'type': 'string'}, 'code': {'type': 'string'}}}


def mapping_for(entries, *, target='prompt'):
    text, parts = '', []
    for body, action, reason in entries:
        start = len(text); text += body + '\n'
        part = {'source': 0, 'start': start, 'end': len(text), 'action': action}
        if action == 'text':
            part['target'] = target
        else:
            part['reason'] = reason
        parts.append(part)
    return text, {'sources': [{'message_id': 'source', 'content_index': 0}], 'parts': parts}


@pytest.mark.parametrize('style', ['markdown', 'plain', 'renamed', 'reversed'])
def test_purpose_is_proposed_by_model_not_titles_numbers_or_order(style):
    entries = [('analysis', '识别为产品摄影'), ('content', '富士山'), ('constraint', '保留商标，不增添文字')]
    if style == 'reversed': entries.reverse()
    original, parts = '', []
    for i, (purpose, body) in enumerate(entries):
        label = ('## 任意标题' if style == 'markdown' else '不相关命名甲乙丙' if style == 'renamed' else '说明') + str(i) + '\n'
        start = len(original); original += label
        parts.append({'source': 0, 'start': start, 'end': len(original), 'action': 'exclude', 'reason': '本次不需要排版标记'})
        start = len(original); original += body + '\n'
        part = {'source': 0, 'start': start, 'end': len(original)-1}
        if purpose == 'analysis': part.update(action='exclude', reason='分析不参与此次执行')
        else: part.update(action='text', target='prompt', _purpose=purpose)
        parts.append(part)
        parts.append({'source': 0, 'start': len(original)-1, 'end': len(original),
                      'action': 'exclude', 'reason': '本次不需要排版换行'})
    # The model proposal, not document order, determines execution order.
    parts.sort(key=lambda p: {'content': 0, 'constraint': 1}.get(p.get('_purpose'), 2))
    for part in parts: part.pop('_purpose', None)
    mapping = {'sources': [{'message_id': 'source', 'content_index': 0}], 'parts': parts}
    outputs, proof = assemble(mapping, lambda _: {'text': original}, PARAMETERS)
    assert outputs['prompt'] == '富士山\n\n保留商标，不增添文字'
    replay, canonical = assemble(proof['mapping'], lambda _: {'text': original}, PARAMETERS)
    assert replay == outputs and canonical == proof


def test_same_source_has_different_justified_inputs_for_image_video_and_word():
    original, mapping = mapping_for([('分析过程', 'exclude', '本次不制作分析文档'),
                                     ('新品发布文案', 'text', ''), ('必须保留署名', 'text', '')])
    image, _ = assemble(mapping, lambda _: {'text': original}, PARAMETERS)
    video = copy.deepcopy(mapping)
    # A Word document explicitly containing the analysis includes that content.
    word = copy.deepcopy(mapping)
    word['parts'][0] = {k: v for k, v in word['parts'][0].items() if k != 'reason'}
    for part in word['parts']:
        part.update(action='text', target='chat_source_text')
    document, _ = assemble(word, lambda _: {'text': original}, PARAMETERS, file_input=True)
    assert '分析过程' not in image['prompt'] and '分析过程' in document['chat_source_text']
    assert assemble(video, lambda _: {'text': original}, PARAMETERS)[0] == image


def test_transformation_is_explicit_not_an_implicit_escape_from_exact_transfer():
    original = '用户要求总结的完整文章'
    mapping = {'sources': [{'message_id': 'source', 'content_index': 0}], 'parts': [
        {'source': 0, 'start': 0, 'end': len(original), 'action': 'value', 'target': 'prompt',
         'value': '摘要文本', 'reason': '按用户要求提炼'}]}
    with pytest.raises(ValueError, match='TEXT_MUST_BE_REFERENCED'):
        assemble(mapping, lambda _: {'text': original}, PARAMETERS)
    mapping['mode'] = 'adapted'
    with pytest.raises(ValueError, match='TRANSFORMATION_REASON_REQUIRED'):
        assemble(mapping, lambda _: {'text': original}, PARAMETERS)
    mapping['reason'] = '用户明确要求总结，输出不要求逐字一致'
    output, proof = assemble(mapping, lambda _: {'text': original}, PARAMETERS)
    assert output['prompt'] == '摘要文本' and proof['mapping']['mode'] == 'adapted'


def test_file_text_input_cannot_be_a_silently_coerced_object():
    mapping = {'mode': 'adapted', 'reason': '用户要求转换',
        'sources': [{'message_id': 'source', 'content_index': 0}], 'parts': [
            {'source': 0, 'start': 0, 'end': 2, 'action': 'value', 'target': 'chat_source_text',
             'value': {'body': '正文'}, 'reason': '转换'}]}
    with pytest.raises(ValueError, match='TEXT_TARGET_REQUIRED'):
        assemble(mapping, lambda _: {'text': '正文'}, PARAMETERS, file_input=True)


def test_explicit_separator_keeps_significant_indentation_and_gaps_are_not_ignored():
    original = 'if ready:\n    run()\n'
    mapping = {'sources': [{'message_id': 'source', 'content_index': 0}], 'parts': [
        {'source': 0, 'start': 0, 'end': 10, 'action': 'text', 'target': 'prompt'},
        {'source': 0, 'start': 10, 'end': len(original), 'action': 'text', 'target': 'prompt', 'separator': ''}]}
    assert assemble(mapping, lambda _: {'text': original}, PARAMETERS)[0]['prompt'] == original
    mapping['parts'][1]['start'] += 4
    with pytest.raises(ValueError, match='COVERAGE_MISSING'):
        assemble(mapping, lambda _: {'text': original}, PARAMETERS)


@pytest.mark.parametrize('fault', ['gap', 'overlap', 'hash', 'target', 'reason', 'rewrite', 'revision'])
def test_mapping_rejects_unaccounted_content_and_invalid_proposals(fault):
    original, mapping = mapping_for([('分析', 'exclude', '不用于执行'), ('正文', 'text', ''), ('约束', 'text', '')])
    if fault == 'gap': mapping['parts'].pop()
    elif fault == 'overlap': mapping['parts'][2]['start'] -= 1
    elif fault == 'hash': mapping['sources'][0]['sha256'] = '0' * 64
    elif fault == 'target': mapping['parts'][1]['target'] = 'fabricated'
    elif fault == 'reason': mapping['parts'][0].pop('reason')
    elif fault == 'rewrite': mapping['parts'][1].update(action='value', value='改写且删掉限制', reason='改写')
    else: mapping['parts'][2].update(action='superseded', superseded_by=2, reason='覆盖')
    with pytest.raises(ValueError, match='CHAT_INPUT_'):
        assemble(mapping, lambda _: {'text': original}, PARAMETERS)


def test_revision_is_explicit_and_cannot_silently_keep_both_old_and_new_values():
    mapping = {'sources': [{'message_id': 'old', 'content_index': 0}, {'message_id': 'new', 'content_index': 0}],
        'parts': [{'source': 0, 'start': 0, 'end': 3, 'action': 'superseded', 'target': 'ratio',
                   'superseded_by': 1, 'reason': '用户本轮明确替换'},
                  {'source': 1, 'start': 0, 'end': 3, 'action': 'value', 'target': 'ratio',
                   'value': '3:4', 'reason': '映射为工具枚举参数'}]}
    read = lambda s: {'text': '1:1' if s['message_id'] == 'old' else '3:4',
                       'context_revision': 1 if s['message_id'] == 'old' else 2}
    assert assemble(mapping, read, PARAMETERS)[0] == {'ratio': '3:4'}
    mapping['parts'][0]['action'] = 'value'; mapping['parts'][0]['value'] = '1:1'
    mapping['parts'][0].pop('superseded_by')
    with pytest.raises(ValueError, match='CONFLICT'):
        assemble(mapping, read, PARAMETERS)


def test_line_locations_and_resource_parameters_are_not_rewritten():
    source = '分析说明\n执行正文\n限制条件\n'
    mapping = {'sources': [{'message_id': 's', 'content_index': 0}], 'parts': [
        {'source': 0, 'first_line': 1, 'last_line': 1, 'action': 'exclude', 'reason': '只用于解释'},
        {'source': 0, 'first_line': 2, 'last_line': 3, 'action': 'text', 'target': 'prompt'}],
        'materials': [{'target': 'references', 'value': [{'message_id': 'upload', 'content_index': 1, 'role': '原图'}],
                       'reason': '用户明确指定'}]}
    outputs, _ = assemble(mapping, lambda _: {'text': source}, PARAMETERS)
    assert outputs['prompt'] == '执行正文\n限制条件\n'
    assert outputs['references'] == mapping['materials'][0]['value']
    with pytest.raises(ValueError, match='TARGET_CHANGED'):
        check_outputs(outputs, {**outputs, 'references': []})


def test_material_locator_shape_is_owned_by_the_real_tool_contract():
    original = '使用刚才找到的文件'
    mapping = {'sources': [{'message_id': 'source', 'content_index': 0}], 'parts': [
        {'source': 0, 'start': 0, 'end': len(original), 'action': 'exclude', 'reason': '请求句不是文件正文，定位由工具结果提供'}],
        'materials': [{'target': 'resource_ref', 'value': 'opaque-native-ref', 'reason': '沿用已发现资源'}]}
    parameters = {'properties': {'resource_ref': {'type': 'string'}}}
    outputs, _ = assemble(mapping, lambda _: {'text': original}, parameters)
    assert outputs == {'resource_ref': 'opaque-native-ref'}


def test_old_reads_do_not_expand_into_shared_analysis():
    original = '# 判断依据\n内部分析\n# 方案一\n正文\n# 方案二\n其它正文'
    quote, proof = bundle(original, '正文\n', scope='item')
    assert quote == '正文\n' and proof['scope'] == 'fragment'
    verify(original, quote, proof)
    with pytest.raises(ValueError): verify(original, quote + '改写', proof)


@pytest.mark.asyncio
@pytest.mark.parametrize('content_format', ['parts', 'json_parts', 'legacy_plain'])
@pytest.mark.parametrize('tool,target', [('generate_video', 'prompt'), ('code_execute', 'chat_source_text'), ('web_search', 'query')])
async def test_fresh_legacy_mapping_is_rejected_before_dispatch(monkeypatch, mock_db, tool, target, content_format):
    runtime, prepared, handler, request = case()
    prepared.content_controls_available = True
    message_id = str(uuid4())
    original, mapping = mapping_for([('分析说明', 'exclude', '不参与执行'), ('正文及约束', 'text', '')], target=target)
    mapping['sources'][0]['message_id'] = message_id
    mock_db.set_table_data('tasks', [{'id': request.task_id, 'user_id': request.user_id, 'org_id': None,
        'conversation_id': request.conversation_id, 'base_context_revision': 1}])
    stored_content = [{'type': 'text', 'text': original}]
    if content_format == 'json_parts': stored_content = json.dumps(stored_content, ensure_ascii=False)
    if content_format == 'legacy_plain': stored_content = original
    mock_db.set_table_data('messages', [{'id': message_id, 'conversation_id': request.conversation_id, 'org_id': None,
        'context_revision': 1, 'message_kind': 'conversation', 'status': 'completed', 'role': 'assistant',
        'content': stored_content}])
    handler.db = mock_db
    monkeypatch.setattr(content_tools, '_check_identity', lambda *_: None)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock())
    from services.agent.agent_result import AgentResult
    seen = []
    async def accept(calls, *_args, **kwargs):
        result = []
        for raw in calls:
            args = json.loads(raw['arguments']); seen.append(args)
            if tool == 'code_execute':
                namespace = {}; exec(args['code'], namespace)
                assert namespace['body'] == '正文及约束\n'
            else:
                assert args[target] == '正文及约束\n'
            assert 'chat_input' not in args
            original_args, source, expected = kwargs['chat_source_bindings'][raw['id']]
            assert rebind_call(mock_db, request.task_id, prepared.execution_context, tool, original_args, source) == expected
            result.append((raw, AgentResult('本地替身，无供应商调用'), False, '本地替身'))
        return result
    handler._execute_tool_calls.side_effect = accept
    arguments = {'chat_input': mapping}
    if tool == 'code_execute': arguments.update(code='body = chat_source_text', description='本地验证完整输入')
    await execute(runtime, prepared, handler, request, [call(tool, arguments, 'mapped')])
    assert not seen
    assert 'CONTENT_INPUT_PATH_RETIRED' in str(prepared.messages[-1])
    assert 'bindings' not in content_tools.checkpoint_state(runtime)














def test_actual_model_schema_exposes_one_content_reference_path():
    from services.tools.catalog import build_capability_catalog
    from services.agent.tool_args_validator import validate_tool_args
    for name in ('generate_image','generate_video','code_execute','web_search'):
        native=build_capability_catalog().require(name).to_schema()
        schema=content_tools.model_tools([native])[0]
        properties=schema['function']['parameters']['properties']
        assert not {'chat_input','chat_text_source','source_prompt'}.intersection(properties)
        assert ('content_source' in properties)==(name in content_tools.INPUT_TOOLS)
        if name in ('generate_image', 'generate_video'):
            description = schema['function']['description']
            assert 'content_source' in description
            assert all(field not in description for field in ('prompt', 'references', 'get_conversation_context'))
            assert 'plan_source' not in properties and 'plan_source' not in description
        if name=='generate_video':
            assert not validate_tool_args(name,{'content_source':{'artifact_id':'saved','sha256':'a'*64,'item_index':2}},[schema])[1]
    web=content_tools.model_tools([build_capability_catalog().require('web_search').to_schema()])
    assert validate_tool_args('web_search',{},web)[1]
    assert not validate_tool_args('web_search',{'query':'普通新查询'},web)[1]


def test_server_planner_schema_exposes_only_its_bound_plan_step():
    from services.tools.catalog import build_capability_catalog

    native = build_capability_catalog().require('generate_image').to_schema()
    server, = content_tools.model_tools([native], server_image_plan=True, selectable_source_images=True)
    parameters = server['function']['parameters']
    assert set(parameters['properties']) == {'plan_source'} and parameters['required'] == ['plan_source']
    assert 'content_source' not in server['function']['description']
    assert {'mode', 'prompt', 'plan_source'} <= set(native['function']['parameters']['properties'])


def test_coverage_validation_checks_items_after_the_seventeenth():
    original, mapping = mapping_for([(str(i), 'text', '') for i in range(20)])
    mapping['parts'][19]['source'] = True
    with pytest.raises(ValueError, match='CHAT_INPUT_PART_INVALID'):
        assemble(mapping, lambda _: {'text': original}, PARAMETERS)


@pytest.mark.parametrize('tool', ['generate_image', 'generate_video', 'code_execute'])
@pytest.mark.parametrize('mode', ['exact', 'adapted'])
def test_legacy_whole_part_uses_common_mapping_without_losing_original(monkeypatch, tool, mode):
    text = '原稿：保持产品结构。\n限制：不得出现水印。'
    _, proof = bundle(text, text)
    source = {'message_id': str(uuid4()), 'content_index': 0, 'text': text, 'mode': mode, 'bundle': proof}
    document = {'text': text, 'context_revision': 1, 'content_index': 0}
    monkeypatch.setattr(content_tools, 'source_text', lambda *_: {'messages': [document]})
    args = {'code': 'body = chat_source_text', 'description': '本地文件'} if tool == 'code_execute' else {'prompt': '改成春天风格'}
    if tool == 'generate_image': args['mode'] = 'text_to_image'
    result = rebind_call(None, None, None, tool, args, source)
    if tool == 'code_execute':
        namespace = {}; exec(result['code'], namespace)
        assert namespace['body'] == text
    else:
        assert text in result['prompt']
        if mode == 'adapted': assert '改成春天风格' in result['prompt']
    if tool == 'generate_image':
        assert result['source_prompt']['handoff']['version'] == 2


def test_legacy_proof_errors_distinguish_document_content_and_proof():
    text = '原文及限制'; _, proof = bundle(text, text)
    for body, prompt, saved, error in [
        (text+'变更', text, proof, 'CHAT_SOURCE_DOCUMENT_CHANGED'),
        (text, '局部正文', proof, 'CHAT_SOURCE_CONTENT_MISMATCH'),
        (text, text, {**proof, 'spans': []}, 'CHAT_SOURCE_PROOF_INVALID'),
    ]:
        with pytest.raises(ValueError, match=error): verify(body, prompt, saved)
