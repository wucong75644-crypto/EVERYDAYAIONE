from tests.semantic_fixtures import semantic_request
"""Actual failed conversation shapes, not a model instructed to use document."""
import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from services.adapters.types import StreamChunk
from services.handlers.chat.outcome_builder import build_content_parts
from services.handlers.image_size_requirements import (
    ImageSizeError, resolve_size_requirement,
)
from services.task_execution import content_objects as co
from services.task_execution.content_groups import index_document
from services.task_execution.contracts import TaskState
from tests.test_content_objects import case, ref, run_content_loop

FIXTURE = json.loads(Path(__file__).with_name('fixtures').joinpath('chat_skill_failures_20261122.json').read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize('thinking', [False, True])
async def test_actual_plain_reply_is_saved_displayed_and_referenced_after_checkpoint(thinking):
    from schemas.message import ImagePart, TextPart
    saved = []
    async def checkpoint(point, payload):
        saved.append(copy.deepcopy(payload))
        return {'outcome': 'saved'}
    r, p, h, q = case(checkpoint)
    original = FIXTURE['plan']['response']
    q = replace(q, content=[TextPart(text=FIXTURE['plan']['user_text']),
                           ImagePart(name='product.png', workspace_path='product.png')],
                context_anchor=SimpleNamespace(input_message_id=str(r.task_bootstrap.input_message_id), base_revision=10))
    q = semantic_request(q, 'write', reference_usage='input')
    async def model(**kwargs):
        if thinking:
            yield StreamChunk(thinking_content='构思新的卡册方案。')
        for pos in range(0, len(original), 137):
            yield StreamChunk(content=original[pos:pos + 137])
        yield StreamChunk(finish_reason='stop')
    _, blocks, totals = await run_content_loop(r, p, h, q, model)
    texts = [part for part in build_content_parts(blocks, fallback_text=totals.text) if part.type == 'text']
    assert len(texts) == 1 and texts[0].text == original
    assert len(saved) > 0 and '_task_execution' in saved[-1]
    restored = TaskState.model_validate_json(json.dumps(saved[-1]['_task_execution']))
    row, = co.catalog(restored)
    assert texts[0].content_artifact_id == row['artifact_id']
    for index, expected in enumerate(FIXTURE['plan']['expected_items'], 1):
        _, _, item = co.load(restored, ref(row, index))
        assert item.body == expected['body']
        assert item.title == f'{index}. ' + expected['title']
        assert original.count(item.body) == 1
        if index in FIXTURE['plan']['selected_indices']:
            native, _ = co.compile_input(restored, 'generate_image', {}, ref(row, index))
            assert native['prompt'] == expected['body']
            assert native['references'] == [{'message_id': str(r.task_bootstrap.input_message_id),
                                             'content_index': 1, 'role': '原图参考'}]
    assert '需要我把其中某几款生成图片吗' not in co.load(restored, ref(row, 5))[2].body
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('question', ['解释这五个功能', '如何写提示词？', '这段回答是什么意思？'])
async def test_numbered_ordinary_chat_does_not_become_an_execution_artifact(question):
    from schemas.message import TextPart
    r, p, h, q = case()
    q = replace(q, content=[TextPart(text=question)])
    async def model(**kwargs):
        yield StreamChunk(content=FIXTURE['plan']['response'])
        yield StreamChunk(finish_reason='stop')
    _, blocks, _ = await run_content_loop(r, p, h, q, model)
    assert not co.catalog(r.task_execution_state)
    assert ''.join(b.get('text', '') for b in blocks if b['type'] == 'text') == FIXTURE['plan']['response']
    h._execute_tool_calls.assert_not_awaited()


def test_plain_index_is_explicit_and_keeps_constraints_instead_of_truncating_them():
    text = '## 1. 樱花\n\n完整提示词。\n\n---\n禁止水印。\n\n## 2. 海边\n\n完整正文。'
    assert index_document(text) is None
    group = index_document(text, allow_plain=True)
    assert group is not None and '禁止水印。' in group.document.items[0].body
    assert co.render(group.document, group.gaps) == text
    ordinary = '1. 樱花\n\n第一份完整正文。\n\n2. 海边\n\n第二份完整正文。'
    numbered = index_document(ordinary, allow_plain=True)
    assert numbered is not None and len(numbered.document.items) == 2
    assert co.render(numbered.document, numbered.gaps) == ordinary


@pytest.mark.parametrize('reversed_refs', [False, True])
def test_actual_multi_image_target_controls_canvas_independent_of_model_order(reversed_refs):
    source = str(uuid4())
    row = {'id': source, 'content': [
        {'type': 'text', 'text': FIXTURE['canvas']['user_text']},
        {'type': 'image'}, {'type': 'image'},
    ]}
    refs = [{'message_id': source, 'content_index': i, 'width': w, 'height': h,
             'aspect_ratio': f'{w}:{h}', 'content_sha256': str(i) * 64}
            for i, (w, h) in enumerate(FIXTURE['canvas']['dimensions'], 1)]
    if reversed_refs:
        refs.reverse()
    intent = {'mode': 'inherit_reference', 'canvas_reference': {'message_id':source, 'content_index':2}}
    args, target = resolve_size_requirement({'mode': 'image_to_image', 'prompt': '仅给目标产品添加波点。'}, refs, intent=intent)
    assert args['aspect_ratio'] == '3:4' and args['resolution'] == '1K'
    assert target['original_width'] == 1280 and target['original_height'] == 1707
    assert refs[target['reference_index']]['content_index'] == 2
    assert len(refs) == 2  # Style input is retained, not silently converted to analysis.
    with pytest.raises(ImageSizeError, match='IMAGE_CANVAS_REFERENCE_REQUIRED'):
        resolve_size_requirement({'mode': 'image_to_image', 'prompt': '添加波点。'},
                                 [x for x in refs if x['content_index'] == 1], intent=intent)


@pytest.mark.parametrize(('text', 'number'), [
    ('参考图一波点。给图二的产品加上一些波点', 2),
    ('把第2张图片改成蓝色', 2), ('以图二作为底图', 2),
    ('保留图一的关键文字，2027数字和Every single day counts.，然后去掉图一的所有元素，把图二图片的元素换到图一日历封面上', 1),
    ('把图一替换到图二', 2),
    ('把第2张图片的元素移到第1张图片上', 1),
    ('不要把图二的图案换到图一', None),
    ('把图二元素换到图一，再给图三添加花纹', None),
    ('参考图一和图二，做一张新图', None),
    ('给图一加波点，给图二加花纹', None),
    ('“给图二加波点”这句话是什么意思', None),
])
def test_target_binding_is_based_on_clear_user_words_not_style_order(text, number):
    from schemas.message import ImagePart, TextPart
    from services.task_execution.inline_image_action import current_attachment_edit
    request = SimpleNamespace(content=[TextPart(text=text),ImagePart(workspace_path='one.png'),ImagePart(workspace_path='two.png')],
                              context_anchor=SimpleNamespace(input_message_id='actual'))
    request = semantic_request(request, 'edit', canvas_image=number)
    target = current_attachment_edit(request)
    assert (target[2] if target else None) == number


def test_save_failure_budget_covers_mixed_errors_reads_and_recovery_but_resets_on_user_input():
    messages = [{'role': 'user', 'content': FIXTURE['plan']['selection']}]
    for index, code in enumerate(FIXTURE['save_failure_codes'][:3]):
        call_id = f'save-{index}'
        messages += [{'role': 'assistant', 'tool_calls': [{'id': call_id, 'function': {'name': co.SAVE}}]},
                     {'role': 'tool', 'tool_call_id': call_id, 'content': json.dumps({'ok': False, 'code': code})},
                     {'role': 'assistant', 'tool_calls': [{'id': f'read-{index}', 'function': {'name': 'read_conversation_text'}}]},
                     {'role': 'tool', 'tool_call_id': f'read-{index}', 'content': '{"ok":true}'}]
        assert co.save_repair_exhausted(copy.deepcopy(messages)) == (index == 2)
    # Checkpoint/restoration and duplicate delivery cannot grant another budget.
    assert co.save_repair_exhausted(json.loads(json.dumps(messages)))
    messages += [{'role': 'user', 'content': '我补充原图，请按新要求处理。'}]
    assert not co.save_repair_exhausted(messages)


@pytest.mark.asyncio
async def test_plain_reply_resumes_exact_original_after_registration_checkpoint_failure():
    from schemas.message import TextPart
    from services.conversation_commands import SafePoint
    from services.task_execution.content_delivery import KEY, pending_response
    r = None
    async def checkpoint(point, payload):
        if point == SafePoint.AFTER_TOOL:
            raise RuntimeError('simulated lost checkpoint')
        return {'outcome': 'saved'}
    r, p, h, q = case(checkpoint)
    q = replace(q, content=[TextPart(text=FIXTURE['plan']['user_text'])])
    q = semantic_request(q, 'write', reference_usage='input')
    async def original(**kwargs):
        yield StreamChunk(content=FIXTURE['plan']['response'])
        yield StreamChunk(finish_reason='stop')
    with pytest.raises(RuntimeError, match='simulated lost checkpoint'):
        await run_content_loop(r, p, h, q, original)
    assert not co.catalog(r.task_execution_state)
    proposal = copy.deepcopy(r.checkpoint_base)
    assert proposal[KEY]['phase'] == 'proposed'
    assert proposal[KEY]['projection_source'] == 'visible_text'
    assert pending_response(r, q)['text'] == FIXTURE['plan']['response']
    async def recovered_checkpoint(point, payload):
        return {'outcome': 'saved'}
    r._replay_checkpoint_callback = recovered_checkpoint
    p.messages = copy.deepcopy(proposal['messages'])
    async def forbidden_rewrite(**kwargs):
        raise AssertionError('Recovery must not ask the model to rewrite this visible reply')
        yield
    _, blocks, _ = await run_content_loop(r, p, h, q, forbidden_rewrite)
    assert blocks[-1]['text'] == FIXTURE['plan']['response']
    assert len(co.catalog(r.task_execution_state)) == 1
    assert r.checkpoint_base[KEY]['phase'] == 'committed'
    assert pending_response(r, q) is None
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('omitted_style', [False, True, 'file-alias', 'analysis'])
async def test_real_image_input_resolution_keeps_both_references_and_uses_target_canvas(monkeypatch, mock_db, tmp_path, omitted_style):
    from schemas.message import ImagePart, TextPart
    from PIL import Image
    from core.config import get_settings
    from services.agent.agent_result import AgentResult
    from services.file_executor import FileExecutor
    from services.task_execution.image_resources import create_resolver
    from services.tools.catalog import build_capability_catalog, build_tool_catalog
    from services.handlers.chat import content_tools
    from tests.test_model_directed_loop import execute
    r, p, h, q = case()
    ctx = p.execution_context
    settings = get_settings()
    monkeypatch.setattr(settings, 'file_workspace_root', str(tmp_path))
    monkeypatch.setattr(settings, 'chat_image_async_enabled', True)
    files = FileExecutor(str(tmp_path), ctx.actor_user_id, None)
    for index, size in enumerate(FIXTURE['canvas']['dimensions'], 1):
        Image.new('RGB', tuple(size), 'white').save(files.resolve_safe_path(f'input{index}.png'))
    source = str(r.task_bootstrap.input_message_id)
    text = FIXTURE['canvas']['user_text']
    q = replace(q, content=[TextPart(text=text), ImagePart(name='input1.png', workspace_path='input1.png'),
                           ImagePart(name='input2.png', workspace_path='input2.png')],
                context_anchor=SimpleNamespace(input_message_id=source, base_revision=0))
    q = semantic_request(q, 'edit', canvas_image=2, materials=[
        {'reference':{'message_id':source,'content_index':1},'role':'波点参考','usage':'input'},
        {'reference':{'message_id':source,'content_index':2},'role':'编辑底图','usage':'input'}])
    mock_db.set_table_data('messages', [{'id': source, 'role': 'user', 'status': 'completed',
        'message_kind': 'conversation', 'context_revision': None, 'org_id': None, 'conversation_id': r.conversation_id,
        'content': [part.model_dump(mode='json') for part in q.content]}])
    mock_db.set_table_data('conversations', [{'id': r.conversation_id, 'user_id': ctx.actor_user_id, 'org_id': None, 'source': 'web'}])
    mock_db.set_table_data('tasks', [{'id': r.task_id, 'user_id': ctx.actor_user_id, 'org_id': None,
        'conversation_id': r.conversation_id, 'input_message_id': source, 'base_context_revision': 0, 'request_params': {}}])
    h.db = mock_db
    r.workflow_projection = q.params['_ecom_workflow']
    h._actor_runtime = r
    r.task_image_resource_resolver = create_resolver(h, q, ctx)
    r.task_operation_store = SimpleNamespace(registry=build_capability_catalog())
    p.content_controls_available = True
    offered = content_tools.model_tools([build_tool_catalog().require('generate_image').to_schema()],
        inline_image=True, inline_only=True, current_images=co.current_source_images(q))
    p.content_visible_tools = {tool['function']['name']: tool['function'] for tool in offered}
    p.content_visible_tool_names = set(p.content_visible_tools)
    captured = []
    async def submit(batch, *args, **kwargs):
        recipes = [json.loads(a.inline_text) for a in r.task_execution_state.artifacts if 'image.direct.recipe' in a.validations]
        assert len(recipes) == 1
        recipe = recipes[0]
        assert recipe['normalized']['aspect_ratio'] == '3:4'
        assert recipe['normalized']['resolution'] == '1K'
        assert len(recipe['resolved']) == 2
        expected = ([(1280, 1707), (360, 360)] if omitted_style in (True, 'file-alias')
                    else [(360, 360), (1280, 1707)])
        assert [(ref['width'], ref['height']) for ref in recipe['resolved']] == expected
        captured.append(recipe)
        return [(call, AgentResult('本地验证，未调用供应商'), False, '本地验证') for call in batch]
    h._execute_tool_calls.side_effect = submit
    draft = {'title': '给图二加波点', 'body': '参考图一波点，在图二封面添加浅灰波点，保留星星和文字。',
             'materials': [{'reference': {'message_id': source, 'content_index': i},
                            'role': role, 'usage': 'input'} for i, role in [(1, '波点风格'), (2, '编辑底图')]], 'options': {}}
    if omitted_style is True or omitted_style == 'file-alias':
        draft['materials'] = draft['materials'][1:]
    if omitted_style == 'file-alias':
        from services.agent.file_id import compute_fid
        draft['materials'][0]['reference'] = {'file_id': compute_fid(None, 'input2.png')}
    if omitted_style == 'analysis':
        draft['materials'][0]['usage'] = 'analysis'
    await execute(r, p, h, q, [{'id': 'image', 'name': 'generate_image', 'arguments': json.dumps({'draft': draft})}])
    assert len(captured) == 1
    assert h._execute_tool_calls.await_count == 1


@pytest.mark.asyncio
async def test_actual_plain_actor_delivery_matches_browser_fixture():
    from schemas.message import TextPart, ImagePart
    from services.handlers.chat.actor_sink import ActorWebSink
    from tests.test_chat_actor_sink import _DB, _WebSocket, _delivery, _DeliveryStore
    r, p, h, q = case()
    q = replace(q, content=[TextPart(text=FIXTURE['plan']['user_text']), ImagePart(name='product.png', workspace_path='product.png')],
                context_anchor=SimpleNamespace(input_message_id=str(r.task_bootstrap.input_message_id), base_revision=0))
    q = semantic_request(q, 'write', reference_usage='input')
    events = _DeliveryStore()
    sink = ActorWebSink(_DB([]), _delivery(), r.cancellation_event, _WebSocket(), delivery_store=events)
    await sink.start()
    async def model(**kwargs):
        yield StreamChunk(content=FIXTURE['plan']['response'])
        yield StreamChunk(finish_reason='stop')
    _, blocks, _ = await run_content_loop(r, p, h, q, model, sink=sink)
    observed = []
    for kind, payload in events.events:
        if kind == 'message_chunk' or kind == 'content_block_add' and payload['block'].get('content_artifact_id'):
            value = copy.deepcopy(payload)
            if kind == 'content_block_add':
                value['block']['content_artifact_id'] = 'failure-canonical-artifact'
            observed.append({'event_type': kind, 'payload': value, 'delivery_seq': len(observed) + 1})
    assert observed == FIXTURE['wire_events']
    assert sink._text == FIXTURE['plan']['response'] == blocks[-1]['text']
    assert len([block for block in sink._blocks if block['type'] == 'text']) == 1


@pytest.mark.parametrize('text', [
    '仅作合成数据测试。请为虚构品牌纸屿的奶白PU活页手账本写5条不同主题的新品设计提示词。每条都要有主题名和80—120字完整生图正文。只交付文字，不生成图片、不联网。',
    '帮我写五条生图提示词，只输出文字。',
    '给我几个生成图片的提示词。',
])
def test_writing_image_prompt_text_does_not_authorize_image_execution(text):
    from schemas.message import TextPart
    from services.handlers.chat.content_tools import image_execution_request
    _, _, _, request = case()
    assert not image_execution_request(replace(request, content=[TextPart(text=text)]))


@pytest.mark.asyncio
async def test_structured_batch_keeps_requested_order_and_original_plan_identity():
    from tests.test_content_objects import registered, ref
    r, p, h, q, row = await registered()
    selections = [ref(row, 3), ref(row, 1)]
    originals = [co.load(r.task_execution_state, selection)[2] for selection in selections]
    compiled = [co.compile_input(r.task_execution_state, 'generate_image', {}, selection)[0] for selection in selections]
    assert [item['prompt'] for item in compiled] == [item.body for item in originals]
    assert [co.load(r.task_execution_state, selection)[2].body for selection in selections] == [item['prompt'] for item in compiled]
    assert [item['source_prompt']['content_object']['source'] for item in compiled] == selections
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('text', [
    '第一个和第三个帮我做成图片，改为蓝色',
    '第一个和第三个帮我做成图片，3:4',
    '第一个和第三个帮我做成图片，再写一个方案',
    '第一个和第三个帮我做成图片了吗？',
    '不要把第一个和第三个做成图片',
    '第一个和第三个是什么意思',
    '第一张和第三张帮我做成图片',
    '第一个和第三个再生成一次图片',
    '上一轮第一个和第三个帮我做成图片',
])
async def test_wording_alone_never_proposes_a_batch(text):
    from schemas.message import TextPart
    from services.handlers.chat import content_tools
    from tests.test_content_objects import registered
    r, p, h, q, row = await registered()
    request = replace(q, content=[TextPart(text=text)])
    assert not content_tools.selects_previous_item(request)
    assert not content_tools.image_execution_request(request)
    assert not hasattr(content_tools, 'explicit_image_batch')
    assert co.catalog(r.task_execution_state)[0]['artifact_id'] == row['artifact_id']
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_materials_and_unconsumed_changes_stay_on_the_model_led_path():
    from schemas.message import TextPart, ImagePart
    from services.handlers.chat import content_tools
    from services.task_execution.user_changes import UserChanges, UserChange
    from tests.test_content_objects import registered
    import hashlib
    r, p, h, q, row = await registered()
    q = replace(q, content=[TextPart(text='第一个和第三个帮我做成图片'), ImagePart(name='new.png', workspace_path='new.png')])
    original = r.task_execution_state.model_dump()
    assert not content_tools.selects_previous_item(q)
    message = '补充：先改第三条配色'
    r.task_user_changes = UserChanges(root_task_id=r.task_bootstrap.task_id, items=(
        UserChange(event_id=uuid4(), owner_task_id=r.task_bootstrap.task_id, message=message,
                   message_sha256=hashlib.sha256(message.encode()).hexdigest(), delivered=True),))
    assert content_tools.has_newer_image_input(r)
    assert r.task_execution_state.model_dump() == original
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_receipts_do_not_create_automatic_batch_submissions():
    from schemas.message import TextPart
    from services.handlers.chat import content_tools
    from tests.test_content_objects import registered
    r, p, h, q, _ = await registered()
    q = replace(q, content=[TextPart(text='第一个和第三个帮我做成图片')])
    r.task_image_completion = SimpleNamespace(event_artifact_ids=('delivered-image',))
    assert not content_tools.selects_previous_item(q)
    assert not hasattr(content_tools, 'explicit_image_batch')
    assert not r.task_execution_state.operations
    h._execute_tool_calls.assert_not_awaited()


def test_historical_image_edit_is_not_reinterpreted_as_a_current_attachment():
    from services.task_execution.inline_image_action import current_attachment_edit
    from schemas.message import TextPart
    request = semantic_request(SimpleNamespace(content=[TextPart(text='把第2张图片改成蓝色')],
        context_anchor=SimpleNamespace(input_message_id='actual')), 'edit')
    assert current_attachment_edit(request) is None
    assert request.params['_ecom_workflow']['intent'] == 'edit'


@pytest.mark.parametrize('fence', ['```', '~~~'])
def test_plain_list_index_does_not_invent_item_boundaries_inside_code(fence):
    text = f'以下是格式示例：\n\n{fence}text\n1. 标题\n\n示例段落一。\n\n2. 标题\n\n示例段落二。\n{fence}'
    assert index_document(text, allow_plain=True) is None
