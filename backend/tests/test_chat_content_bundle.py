"""Explicit input mappings, format independence and private no-provider replay."""
import copy
import hashlib
import json
from uuid import uuid4

import pytest

from services.handlers.chat_input_handoff import assemble, check_outputs, digest, line_starts
from services.handlers.chat import content_tools
from services.handlers.chat_content_bundle import bundle, verify
from services.handlers.chat_image_request import freeze_image_request, verify_frozen_request
from tests.test_chat_content_real_replay import real_replay
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
async def test_actual_loop_binds_generic_mapping_before_dispatch_and_saves_proof(monkeypatch, mock_db, tool, target, content_format):
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
            assert content_tools.bind_call(mock_db, request.task_id, prepared.execution_context, tool, original_args, source) == expected
            result.append((raw, AgentResult('本地替身，无供应商调用'), False, '本地替身'))
        return result
    handler._execute_tool_calls.side_effect = accept
    arguments = {'chat_input': mapping}
    if tool == 'code_execute': arguments.update(code='body = chat_source_text', description='本地验证完整输入')
    await execute(runtime, prepared, handler, request, [call(tool, arguments, 'mapped')])
    assert len(seen) == 1, prepared.messages[-1]
    assert runtime.checkpoint_base[content_tools.STATE]['bindings']['mapped']['proof']['version'] == 2
    canonical = copy.deepcopy(seen[0])
    runtime.checkpoint_base[content_tools.STATE] = copy.deepcopy(runtime.checkpoint_base[content_tools.STATE])
    await execute(runtime, prepared, handler, request, [call(tool, canonical, 'mapped')])
    assert len(seen) == 2 and seen[1] == canonical
    if tool == 'code_execute':
        assert seen[1]['code'].count('chat_source_text =') == 1


def real_mapping(replay, original):
    """Human-labelled oracle for the original case, never production classification."""
    import re
    location = original['source_prompt']
    row = next(row for row in replay.rows if row['id'] == location['message_id'])
    contents = json.loads(row['content']) if isinstance(row['content'], str) else row['content']
    text = contents[location['content_index']]['text']
    headers = list(re.finditer(r'(?m)^# 方案\d+[^\n]*', text))
    at = text.index(original['prompt'])
    selected = next(i for i, h in enumerate(headers) if h.start() <= at < (headers[i+1].start() if i+1 < len(headers) else len(text)))
    start = headers[selected].start()
    end = headers[selected+1].start() if selected+1 < len(headers) else len(text)
    # The original last item is followed by a document-level confirmation note.
    if selected+1 == len(headers):
        footer = text.rfind('\n---\n', start)
        if footer >= at + len(original['prompt']): end = footer
    shared = []
    for match in re.finditer(r'(?m)^- 统一(?:保留|替换)内容[^\n]*', text[:headers[0].start()]):
        shared.append((match.start(), match.end()))
    ranges = sorted([*shared, (start, end)])
    parts, cursor = [], 0
    expected = []
    for a, b in ranges:
        if cursor < a:
            parts.append({'source': 0, 'start': cursor, 'end': a, 'action': 'exclude',
                          'reason': '分析说明、确认问句或未选择的其他方案，不作为本次生成要求'})
        parts.append({'source': 0, 'start': a, 'end': b, 'action': 'text', 'target': 'prompt'})
        expected.append(text[a:b]); cursor = b
    if cursor < len(text):
        parts.append({'source': 0, 'start': cursor, 'end': len(text), 'action': 'exclude', 'reason': '未选方案或交互说明'})
    return {'sources': [{k: location[k] for k in ('message_id', 'content_index')}], 'parts': parts,
            'materials': [{'target': 'references', 'value': original['references'], 'reason': '沿用用户指定的原产品摄影参考图'}]}, '\n\n'.join(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize('reader', ['new', 'legacy', 'direct'])
@pytest.mark.parametrize('count', [1, 6])
async def test_real_mapping_preserves_requirements_without_analysis_at_provider_serialization(real_replay, reader, count):
    replay = real_replay
    originals = replay.arguments if count == 6 else replay.arguments[1:2]
    if reader != 'direct':
        ids = list(dict.fromkeys(a['source_prompt']['message_id'] for a in originals))
        read = (content_tools.READ, {'message_id': ids[0], 'for_mapping': True}) if reader == 'new' else ('get_conversation_context', {'message_ids': ids, 'for_mapping': True})
        await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [call(*read, 'read')])
    calls, expected = [], []
    for i, original in enumerate(originals):
        mapping, text = real_mapping(replay, original); expected.append(text)
        args = {k: copy.deepcopy(v) for k, v in original.items() if k not in {'prompt', 'source_prompt'}}
        args['chat_input'] = mapping
        calls.append(call('generate_image', args, f'mapped-{i}'))
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, calls)
    assert [a['prompt'] for a in replay.generation] == expected, str(replay.prepared.messages[-1])[:1500]
    from services.adapters.kie.image_adapter import KieImageAdapter
    recipes = [json.loads(a.inline_text) for a in replay.runtime.task_execution_state.artifacts if 'image.direct.recipe' in a.validations]
    original_digest = hashlib.sha256(__import__('base64').b64decode(replay.data['originals'][0]['raw_b64'])).hexdigest()
    for text, original, recipe in zip(expected, originals, recipes):
        assert original['prompt'] in text and '负面提示词' in text
        assert '识别模式' not in text and '判断依据' not in text
        assert recipe['normalized']['prompt'] == text and recipe['normalized']['aspect_ratio'] == '1:1'
        assert recipe['resolved'][0]['content_sha256'] == original_digest
        assert recipe['resolved'][0]['workspace_path'] == replay.data['originals'][0]['workspace_path']
        snapshot = freeze_image_request(recipe['normalized'], recipe['resolved'], origin={}, max_requests=count, max_credits=300)
        verify_frozen_request(snapshot)
        payload = KieImageAdapter(client=None, model=snapshot['model'])._build_input_params(text,
            ['https://local-replay.invalid/original.jpg'], '1:1', 'png', '1K')
        assert payload['prompt'] == text


@pytest.mark.asyncio
async def test_unmapped_partial_legacy_input_stops_without_guessing_more_or_less(real_replay):
    replay = real_replay
    args = copy.deepcopy(replay.arguments[1]); args.pop('source_prompt')
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [call('generate_image', args, 'unmapped')])
    assert replay.generation == []
    assert 'CHAT_INPUT_MAPPING_REQUIRED' in str(replay.prepared.messages)


@pytest.mark.asyncio
async def test_coverage_failure_is_saved_and_does_not_dispatch(real_replay):
    replay = real_replay
    original = replay.arguments[1]
    mapping, _ = real_mapping(replay, original)
    mapping['parts'] = [p for p in mapping['parts'] if p['action'] != 'text']
    args = {k: v for k, v in original.items() if k not in {'source_prompt', 'prompt'}}
    args['chat_input'] = mapping
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [call('generate_image', args, 'missing')])
    assert replay.generation == []
    assert 'CHAT_INPUT_COVERAGE_MISSING' in str(replay.prepared.messages)
    assert replay.saved[-1][content_tools.STATE]['source_selection_failed']
    assert replay.saved[-1][content_tools.STATE]['stop_message']


@pytest.mark.asyncio
async def test_native_source_failure_stays_specific_and_stops_automatic_fallback(real_replay):
    from services.agent.agent_result import AgentResult
    from services.tools import ToolCall
    from services.tools.catalog import build_capability_catalog
    from services.tools.policy import ToolPolicy
    from services.tools.result import ToolResult
    replay = real_replay
    original = replay.arguments[1]
    mapping, _ = real_mapping(replay, original)
    args = {k: v for k, v in original.items() if k not in {'source_prompt', 'prompt'}}
    args['chat_input'] = mapping
    async def rejected(calls, *_args, **_kwargs):
        raw, = calls
        arguments = json.loads(raw['arguments'])
        context = replay.prepared.execution_context
        decision = ToolPolicy(build_capability_catalog()).decide('generate_image', context, arguments)
        result = ToolResult.wrap(AgentResult('来源在最终核验时变化', status='error', error_message='CHAT_INPUT_SOURCE_CHANGED'),
                                 call=ToolCall(raw['id'], raw['name'], arguments), context=context, decision=decision)
        return [(raw, result, True, '来源变化')]
    replay.handler._execute_tool_calls.side_effect = rejected
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [call('generate_image', args, 'late-source')])
    assert replay.generation == []
    assert 'CHAT_INPUT_SOURCE_CHANGED' in replay.saved[-1][content_tools.STATE]['stop_message']


@pytest.mark.asyncio
@pytest.mark.parametrize('denied', ['system_role', 'personal_scope'])
async def test_native_handoff_cannot_bypass_chat_source_scope(real_replay, denied):
    from services.task_execution.image_resources import native_resolver
    replay = real_replay
    original = replay.arguments[1]
    mapping, _ = real_mapping(replay, original)
    args = {k: v for k, v in original.items() if k not in {'source_prompt', 'prompt'}}
    result, _ = content_tools.bind_mapping(replay.db, replay.task['id'], replay.prepared.execution_context,
                                          'generate_image', args, mapping)
    native = native_resolver(replay.handler, replay.request, replay.prepared.execution_context)
    if denied == 'system_role':
        row = next(row for row in replay.rows if row['id'] == mapping['sources'][0]['message_id'])
        row['role'] = 'system'; replay.db.set_table_data('messages', replay.rows)
    else:
        native.owner.personal_context_allowed = False
    with pytest.raises(PermissionError, match='CHAT_INPUT_SOURCE_DENIED'):
        native.verify_handoff(result['source_prompt']['handoff'])


def test_actual_model_schema_exposes_mapping_and_complete_target_contract():
    from services.tools.catalog import build_capability_catalog
    from services.agent.tool_args_validator import validate_tool_args
    registry = build_capability_catalog()
    for name in ('generate_image', 'generate_video', 'code_execute', 'web_search'):
        spec = registry.require(name).to_schema()
        schema = content_tools.model_tools([spec])[0]
        assert 'chat_input' in schema['function']['parameters']['properties']
        mapping_schema = schema['function']['parameters']['properties']['chat_input']
        targets = mapping_schema['properties']['parts']['items']['properties']['target']['enum']
        assert set(spec['function']['parameters']['properties']) - {'source_prompt', 'plan_source'} <= set(targets)
        if name == 'generate_image':
            original, mapping = mapping_for([('正文', 'text', '')])
            _, error = validate_tool_args(name, {'mode': 'text_to_image', 'chat_input': mapping}, [schema])
            assert not error


def test_unmapped_calls_keep_original_required_fields_and_discovery_is_on_demand():
    from services.tools.catalog import build_capability_catalog
    from services.agent.tool_args_validator import validate_tool_args
    web = build_capability_catalog().require('web_search').to_schema()
    assert 'chat_input' not in content_tools.model_tools([web], mapping_names=[])[0]['function']['parameters']['properties']
    selected = content_tools.model_tools([web], mapping_names=['web_search'])
    assert 'chat_input' in selected[0]['function']['parameters']['properties']
    assert validate_tool_args('web_search', {}, selected)[1]
    assert not validate_tool_args('web_search', {'query': '普通新查询'}, selected)[1]


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
    monkeypatch.setattr(content_tools, 'read_document', lambda *_: document)
    args = {'code': 'body = chat_source_text', 'description': '本地文件'} if tool == 'code_execute' else {'prompt': '改成春天风格'}
    if tool == 'generate_image': args['mode'] = 'text_to_image'
    result = content_tools.bind_call(None, None, None, tool, args, source)
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


def explicit_record_mapping(replay, original):
    """Human oracle for five-item record; not a production content classifier."""
    if len(replay.arguments) != 5:
        return real_mapping(replay, original)
    source = original['source_prompt']
    row = next(r for r in replay.rows if r['id'] == source['message_id'])
    text = json.loads(row['content'])[source['content_index']]['text']
    item = next(p for p in source['bundle']['spans'] if p['role'] == 'item')
    shared_start = text.index('**使用建议**')
    shared_end = text.index('\n\n', shared_start)
    ranges = sorted([(item['start'], item['end']), (shared_start, shared_end)])
    parts, expected, cursor = [], [], 0
    for start, end in ranges:
        if start > cursor:
            parts.append({'source': 0, 'start': cursor, 'end': start, 'action': 'exclude', 'reason': '其他方案及对话说明'})
        parts.append({'source': 0, 'start': start, 'end': end, 'action': 'text', 'target': 'prompt'})
        expected.append(text[start:end]); cursor = end
    if cursor < len(text): parts.append({'source': 0, 'start': cursor, 'end': len(text), 'action': 'exclude', 'reason': '交互确认问句'})
    return {'sources': [{k: source[k] for k in ('message_id', 'content_index')}], 'parts': parts,
        'materials': [{'target': 'references', 'value': original['references'], 'reason': '用户原产品参考图'}]}, '\n\n'.join(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize('reader', ['new', 'legacy', 'direct'])
@pytest.mark.parametrize('adapted', [False, True])
@pytest.mark.parametrize('all_items', [False, True])
async def test_record_entrypoints_exact_and_adapted_single_and_all(real_replay, reader, adapted, all_items):
    replay = real_replay
    originals = replay.arguments if all_items else replay.arguments[1:2]
    if reader != 'direct':
        mid = originals[0]['source_prompt']['message_id']
        name, args = (content_tools.READ, {'message_id': mid, 'for_mapping': True}) if reader == 'new' else ('get_conversation_context', {'message_ids': [mid], 'for_mapping': True})
        await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [call(name, args, 'read-map')])
    expected, calls = [], []
    for i, original in enumerate(originals):
        mapping, body = explicit_record_mapping(replay, original)
        if adapted:
            mapping.update(mode='adapted', reason='用户要求加入春日背景，其他沿用')
            # Explicit transformation retains all selected content, rather than
            # treating a short adaptation instruction as the whole input.
            kept = [p for p in mapping['parts'] if p['action'] == 'text']
            for part in kept:
                text = next(r for r in replay.rows if r['id'] == mapping['sources'][0]['message_id'])['content']
                text = json.loads(text) if isinstance(text, str) else text
                fragment = text[mapping['sources'][0]['content_index']]['text'][part['start']:part['end']]
                part.update(action='value', value=fragment+'\n补充要求：春日背景', reason='沿用原内容并加入用户新要求')
            # Each target has a single value; other selected spans remain exact
            # source text in the assembled transformed value with full lineage.
            first = kept[0]
            first['value'] = body+'\n补充要求：春日背景'
            for part in kept[1:]:
                part.update(action='superseded', superseded_by=mapping['parts'].index(first))
                part.pop('value')
            body += '\n补充要求：春日背景'
        args = {k: copy.deepcopy(v) for k,v in original.items() if k not in {'source_prompt','prompt'}}
        args['chat_input'] = mapping
        calls.append(call('generate_image', args, f'record-{i}')); expected.append(body)
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, calls)
    assert [a['prompt'] for a in replay.generation] == expected, str(replay.prepared.messages[-1])[:800]
    recipes = [json.loads(a.inline_text) for a in replay.runtime.task_execution_state.artifacts if 'image.direct.recipe' in a.validations]
    raw = __import__('base64').b64decode(replay.data['originals'][0]['raw_b64'])
    assert len(recipes) == len(originals)
    from services.adapters.kie.image_adapter import KieImageAdapter
    for original, body, recipe in zip(originals, expected, recipes):
        assert original['prompt'] in body
        assert recipe['normalized']['prompt'] == body
        assert recipe['resolved'][0]['content_sha256'] == hashlib.sha256(raw).hexdigest()
        payload = KieImageAdapter(client=None, model='nano-banana-pro')._build_input_params(body, ['https://local.invalid/original.png'], '1:1', 'png', '1K')
        assert payload['prompt'] == body


@pytest.mark.asyncio
async def test_actual_five_legacy_bundle_requests_require_explicit_mapping_before_dispatch(real_replay):
    replay = real_replay
    if len(replay.arguments) != 5:
        pytest.skip('Five-item historical bundle mismatch case only')
    calls = [call('generate_image', copy.deepcopy(args), f'old-bundle-{i}') for i,args in enumerate(replay.arguments)]
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, calls)
    assert replay.generation == []
    assert 'CHAT_INPUT_MAPPING_REQUIRED' in str(replay.prepared.messages)
    assert 'CHAT_SOURCE_BUNDLE_CHANGED' not in str(replay.prepared.messages)
