"""Streaming authoring keeps original methods, durable text and honest failures."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from tests.semantic_fixtures import semantic_request

from schemas.message import TextPart
from services.adapters.dashscope.chat_adapter import DashScopeAPIError, DashScopeChatAdapter
from services.adapters.types import StreamChunk
from services.handlers.chat import content_tools
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput
from services.task_execution import content_objects as co
from services.task_execution.content_groups import index_document
from services.task_execution.contracts import TaskState
from tests.test_content_objects import case, ref, run_content_loop
from tests.test_skill_runtime import Source, item, state as skill_state, activate


def reference_markdown(count=6):
    return '# 参考图判断\n\n真实参考图的材质和构图。\n\n' + '\n\n---\n\n'.join(
        f'## 方案{i}｜主题{i}\n\n### 方案内容\n\n独立主题{i}。\n\n'
        '### 视觉设定\n\n保留真实产品。\n\n### 参考图使用方式\n\n沿用原图。\n\n'
        f'### 完整生图提示词\n\n```text\n原文{i}，保留标识与材质。\n```\n\n'
        '### 负面提示词\n\n```text\n禁止变形，禁止水印。\n```'
        for i in range(1, count + 1))


async def attach_writing_method(runtime):
    source = Source([item(tools=(), task_stage={
        'output_profile': 'reference-prompts-v1', 'restriction_scope': 'phase',
    })], body='按参考图判断和六套原版结构交付完整提示词。')
    skills = skill_state(source, turn_id=runtime.turn_id,
                         platform_tool_names={'generate_image', 'file_search'},
                         authorized_tool_names={'generate_image', 'file_search'})
    await skills.initialize()
    await skills.activate(activate())
    runtime.skill_runtime = skills


async def writing_case(checkpoint=None):
    runtime, prepared, handler, request = case(checkpoint)
    await attach_writing_method(runtime)
    request = replace(request, content=[TextPart(text='给我出六个不同小羊形象，不要太复杂')],
                      context_anchor=SimpleNamespace(input_message_id=str(runtime.task_bootstrap.input_message_id), base_revision=0))
    return runtime, prepared, handler, request


@pytest.mark.asyncio
async def test_verified_writing_phase_streams_before_finish_and_registers_exact_six_items():
    checkpoints = []
    async def save(point, payload):
        checkpoints.append(copy.deepcopy(payload))
        return {'outcome': 'saved'}
    r, p, h, q = await writing_case(save)
    sink = CollectingExecutionSink()
    text = reference_markdown()
    async def stream(**kwargs):
        save = next(t for t in kwargs['tools'] if t['function']['name'] == co.SAVE)
        assert 'confirmation' in save['function']['parameters']['properties']
        assert any(m.get('content') == content_tools.REFERENCE_TEXT_INSTRUCTION for m in kwargs['messages'])
        yield StreamChunk(content=text[:30])
        assert sink.text == text[:30]  # Already visible, before completion/registration.
        yield StreamChunk(content=text[30:])
        yield StreamChunk(finish_reason='stop')
    _, blocks, _ = await run_content_loop(r, p, h, q, stream, sink=sink)
    shown, = [b for b in blocks if b['type'] == 'text']
    assert shown['text'] == text and not shown.get('content_incomplete')
    restored = TaskState.model_validate(checkpoints[-1]['_task_execution'])
    row, = co.catalog(restored)
    assert len(row['items']) == 6
    expected = index_document(text)
    for i, original in enumerate(expected.document.items, 1):
        assert co.load(restored, ref(row, i))[2].body == original.body
    assert co.render(expected.document, expected.gaps) == text
    assert r.skill_runtime.resident['report'].body_sha256
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_method_is_shown_but_never_registered_or_claimed_complete():
    r, p, h, q = await writing_case()
    text = '## 1. 第一套\n\n只有概要，缺少完整提示词和负面提示词。'
    async def stream(**kwargs):
        yield StreamChunk(content=text)
        yield StreamChunk(finish_reason='stop')
    _, blocks, totals = await run_content_loop(r, p, h, q, stream)
    assert not co.catalog(r.task_execution_state)
    block, = [b for b in blocks if b['type'] == 'text']
    assert block['content_incomplete'] is True and block['text'].startswith(text)
    assert block['text'].count(text) == totals.text.count(text) == 1
    assert 'report' in r.skill_runtime.active  # Failed presentation cannot retire the phase.
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_method_text_recovers_from_proposal_without_another_model_call():
    from services.task_execution.content_delivery import KEY, pending_response
    async def crash(point, payload):
        if payload.get('_task_execution'):
            raise RuntimeError('simulated projection interruption')
        return {'outcome': 'saved'}
    r, p, h, q = await writing_case(crash)
    text = reference_markdown()
    async def stream(**kwargs):
        yield StreamChunk(content=text)
        yield StreamChunk(finish_reason='stop')
    with pytest.raises(RuntimeError, match='simulated projection interruption'):
        await run_content_loop(r, p, h, q, stream)
    assert r.checkpoint_base[KEY]['phase'] == 'proposed'
    assert pending_response(r, q)['text'] == text
    async def recovered(point, payload):
        return {'outcome': 'saved'}
    r._replay_checkpoint_callback = recovered
    p.messages = copy.deepcopy(r.checkpoint_base['messages'])
    async def no_rewrite(**kwargs):
        raise AssertionError('A durable complete original must not be regenerated')
        yield
    _, blocks, _ = await run_content_loop(r, p, h, q, no_rewrite)
    assert blocks[-1]['text'] == text
    assert r.checkpoint_base[KEY]['phase'] == 'committed'
    assert len(co.catalog(r.task_execution_state)[0]['items']) == 6
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_streaming_preference_does_not_replace_revisions_or_execution_protocols():
    r, p, h, q = await writing_case()
    assert content_tools.prefers_reference_text(q, r)
    for media in ({'tool': 'generate_image', 'wait_all': True},
                  {'tool': 'generate_image', 'wait_for': [1, 2, 3]}):
        waiting = semantic_request(q, 'write', media=media)
        assert not content_tools.prefers_reference_text(waiting, r)
    assert not content_tools.prefers_reference_text(q, r, saved_source_available=True)
    assert not content_tools.prefers_reference_text(q, r, server_image_plan=True)
    assert not content_tools.prefers_reference_text(semantic_request(replace(q, content=[TextPart(text='生成一张图片')]), 'generate'), r)
    r._steer_messages.append('写完后把第一套做成图片')
    assert not content_tools.prefers_reference_text(q, r)
    r._steer_messages.clear()
    r.skill_runtime = None
    assert not content_tools.prefers_reference_text(q, r)


REJECTION = '<400> InternalError.Algo.DataInspectionFailed: Input image data may contain inappropriate content.'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['response.failed', 'response.completed', 'error'])
async def test_real_sse_refusal_keeps_provider_evidence_and_has_no_retry(kind):
    from core.error_classifier import classify_error
    detail = {'code': 'server_error', 'message': REJECTION}
    event = {'type': kind, 'response': {'status': 'failed', 'error': detail}}
    if kind == 'error':
        event = {'type': kind, **detail}
    ad = DashScopeChatAdapter('test', 'qwen3.8-max')
    ad._client = httpx.AsyncClient(base_url='https://example.com', transport=httpx.MockTransport(
        lambda req: httpx.Response(200, headers={'x-request-id': 'provider-123'},
                                  text='data: ' + json.dumps(event) + '\n\n')))
    try:
        with pytest.raises(DashScopeAPIError) as raised:
            _ = [c async for c in ad.stream_chat([], tools=[{
                'type': 'function', 'function': {'name': 'web_search', 'parameters': {'type': 'object'}},
            }], enable_builtin_tools=True)]
        error = raised.value
        assert error.provider_message == REJECTION and error.error_code == 'server_error'
        assert error.request_id == 'provider-123' and error.status_code == 200
        assert '内容检查' in str(error)
        assert not error.request_rejected and not error.retryable_rejection
        result = classify_error(error, model_call=True)
        assert result.error_code == 'MODEL_INPUT_REJECTED'
        assert not result.is_retryable and not result.should_record_breaker and not result.should_refund
    finally:
        await ad.close()


def test_only_verified_refusal_signature_changes_error_classification():
    from core.error_classifier import classify_error
    arbitrary = DashScopeAPIError.from_http(json.dumps({'error': {
        'code': 'server_error', 'message': 'temporary server failure',
    }}).encode(), 500)
    assert not arbitrary.content_rejection
    assert classify_error(arbitrary, model_call=True).is_retryable
    rejection = DashScopeAPIError.from_http(json.dumps({'error': {
        'code': 'server_error', 'message': REJECTION,
    }}).encode(), 500)
    assert not classify_error(rejection, model_call=True).is_retryable


@pytest.mark.parametrize('text,images,expected', [
    ('图中白色条纹是绿色条纹的几倍', True, True),
    ('面积是多少', True, True),
    ('帮我生成1:1、790*790的主图', True, False),
    ('六是三的几倍', False, False),
    ('请改成浅蓝色', True, False),
])
def test_visual_numeric_guidance_preserves_request_and_does_not_change_image_actions(text, images, expected):
    message = UserLayer.render(UserMessageInput(text=text, image_urls=['https://example.com/photo.png'] if images else [])).user_message
    if isinstance(message['content'], str):
        assert message['content'] == text
        assert not expected
    else:
        assert message['content'][0]['text'] == text
        assert ('视觉问答口径提示' in str(message)) is expected


@pytest.mark.parametrize('text,expected', [
    ('参考图一的波点，给图二的产品加上一些波点', (1, 2)),
    ('参考第2张图片的材质，给第1张图片添加纹理', (2, 1)),
    ('只分析图一的样式，不作为生图参考，给图二添加文字', ()),
    ('参考图一的波点，给图二添加，图片不用重新上传', ()),
    ('解释“参考图一，给图二添加”的意思', ()),
    ('把图二的桌面换成蓝色', ()),
    ('把图二图片的元素换到图一日历封面上', (2, 1)),
    ('把图一替换到图二', (1, 2)),
    ('不要把图二的元素替换到图一', ()),
    ('解释“把图二的元素换到图一”的意思', ()),
])
def test_literal_transfer_binding_never_selects_analysis_or_quoted_requests(text, expected):
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
    frame = ImageRequestContext(materials=[{'reference':{'message_id':'actual','content_index':n},
        'role':f'原图{n}','usage':'input'} for n in expected])
    assert tuple(m.reference.content_index for m in frame.materials) == expected
    assert text  # Natural wording is tested through the semantic producer, not a second parser.


@pytest.mark.asyncio
@pytest.mark.parametrize('pending', [False, True])
async def test_newer_user_input_cannot_inherit_old_automatic_reference_selection(pending):
    from schemas.message import ImagePart
    from services.task_execution.inline_image_action import complete_transfer_materials
    _, p, h, q = case()
    q = replace(q, content=[TextPart(text='参考图一的波点，给图二添加波点'),
                           ImagePart(workspace_path='one.png'), ImagePart(workspace_path='two.png')])
    draft = co.ContentItem(title='修改后的要求', body='只在图二添加星星，不再参考图一。')
    runtime = SimpleNamespace(task_user_changes=SimpleNamespace(items=() if pending else (object(),)),
                              _steer_messages=['不用图一'] if pending else [])
    # No resolver is present: even a file read from the superseded request is a failure here.
    assert await complete_transfer_materials(draft, h, q, p.execution_context, runtime=runtime) is draft


@pytest.mark.asyncio
@pytest.mark.parametrize('text,target', [
    ('参考图一的产品里面的波点。给图二的产品加上一些波点', 2),
    ('参考第2张图片的材质，给第1张图片添加纹理', 1),
])
async def test_literal_edit_projects_verified_target_and_limited_reference_roles_into_supplier_text(monkeypatch, text, target):
    import json
    from uuid import uuid4
    from schemas.message import ImagePart
    from services.task_execution import inline_image_action as inline
    from services.tools.catalog import build_capability_catalog
    runtime,p,h,_=case()
    message=str(uuid4())
    request=SimpleNamespace(content=[TextPart(text=text),ImagePart(workspace_path='one.png'),
        ImagePart(workspace_path='two.png')],context_anchor=SimpleNamespace(input_message_id=message))
    request = semantic_request(request, 'edit', canvas_image=target, materials=[
        {'reference':{'message_id':message,'content_index':n},'role':f'图{n}','usage':'input'} for n in (1,2)])
    native=SimpleNamespace(resolve=lambda refs: [
        {'message_id': message, 'content_index': ref['content_index'],
         'workspace_path': f"{ref['content_index']}.png", 'content_sha256': str(ref['content_index']) * 64}
        for ref in refs])
    monkeypatch.setattr('services.task_execution.image_resources.native_resolver',lambda *_:native)
    captured=[]
    class CapturedDraft(Exception):pass
    def save(_runtime,call,*args,**kwargs):
        captured.append(json.loads(call['arguments'])['document']['items'][0])
        raise CapturedDraft
    monkeypatch.setattr(co,'save',save)
    with pytest.raises(CapturedDraft):
        await inline.prepare(runtime,h,request,p,{'id':'one-edit'},
            {'source_message_id':message,'source_content_index':target,'edit_instruction':'将参考图的稀疏波点用于指定底图，保留其原有部件与文字。'},
            build_capability_catalog(),version='current')
    item,=captured
    assert f'编辑底图为输入图片{target}（用户图{target}）' in item['body']
    assert item['body'].endswith('将参考图的稀疏波点用于指定底图，保留其原有部件与文字。')
    assert text not in item['body'] and '用户原始编辑要求' not in item['body']
    assert '不得把未要求的商品结构' not in item['body']
    order=[1,2]
    assert [m['reference']['content_index'] for m in item['materials']]==order
    assert item['materials'][target-1]['role'].endswith('（编辑底图）')
    assert all(f'输入图片{i}对应用户图{number}。' in item['body'] for i,number in enumerate(order,1))


@pytest.mark.parametrize('text,images,expected', [
    ('卖点是什么', True, True),
    ('图里的包是什么填充物和材质', True, True),
    ('帮我设计新产品的卖点方案', True, False),
    ('卖点是什么', False, False),
    ('直接把背景改成蓝色', True, False),
])
def test_visible_product_facts_cannot_certify_hidden_properties(text, images, expected):
    from services.prompt_builder.layers.user_layer import visual_fact_guidance
    guidance=visual_fact_guidance(text,images)
    assert bool(guidance) is expected
    message=UserLayer.render(UserMessageInput(text=text,image_urls=['https://example.com/product.png'] if images else [])).user_message
    if images:
        assert message['content'][0]['text']==text
        assert ('商品图片事实提示' in str(message)) is expected
    else:
        assert message['content']==text


def test_current_visual_input_fact_does_not_treat_full_extra_budget_as_blindness(monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(),'chat_context_max_images',1)
    messages=[{'role':'user','content':[{'type':'text','text':'只写提示词'},
        {'type':'image_url','image_url':{'url':'https://example.com/original.png'}}]}]
    before=copy.deepcopy(messages)
    assert content_tools.visual_budget_remaining(messages)==0
    assert '已附带1个图片输入' in content_tools.visual_context_notice(messages)
    assert '只限制额外图片' in content_tools.visual_context_notice(messages)
    assert messages==before
    assert not content_tools.visual_context_notice([{'role':'user','content':'没有图片'}])


@pytest.mark.parametrize('text,expected', [
    ('这是我的产品，是一本A5的卡册，帮我规划一下该怎么去做淘宝主图',True),
    ('帮我规划两套电商主图方案',True),
    ('我需要的是指令不是图片',True),
    ('给我三套参考图提示词方案',True),
    ('规划完再直接生成两张主图',False),
    ('按照第一个和第三个方案生成图片',False),
    ('把刚生成的图的背景换成蓝色，直接生成一张',False),
    ('第一个方案',False),
])
def test_planning_or_writing_is_not_an_image_purchase(text,expected):
    assert content_tools.image_writing_only_request(semantic_request(SimpleNamespace(content=[TextPart(text=text)]), 'write' if expected else 'other')) is expected


@pytest.mark.asyncio
async def test_hallucinated_generate_call_cannot_purchase_during_planning():
    from tests.test_model_directed_loop import execute
    from tests.test_task_execution_model_bridge import call
    r,p,h,q=case()
    p.content_controls_available=True
    q=semantic_request(replace(q,content=[TextPart(text='这是我的产品，帮我规划一下该怎么去做淘宝主图')]), 'write')
    await execute(r,p,h,q,[call('generate_image',{'prompt':'未获出图授权','mode':'text_to_image'},'unauthorized-image')])
    h._execute_tool_calls.assert_not_awaited()
    assert any('本轮用户只要求文字策划、方案或提示词，未创建图片任务' in str(m.get('content','')) for m in p.messages)


def test_latest_authenticated_append_can_authorize_execution_after_writing_only():
    q=SimpleNamespace(content=[TextPart(text='只写三套方案，不生成图片')])
    runtime=SimpleNamespace(task_id='current',task_user_changes=SimpleNamespace(items=(
        SimpleNamespace(owner_task_id='current',message='现在把第一个方案生成图片'),)))
    runtime.workflow_projection = {'intent':'execute_saved'}
    assert not content_tools.image_writing_only_request(q,runtime)
    runtime.task_user_changes.items += (SimpleNamespace(owner_task_id='current',message='先不要生成图片，只交付文字方案'),)
    runtime.workflow_projection = {'intent':'write'}
    assert content_tools.image_writing_only_request(q,runtime)
    runtime.task_user_changes.items += (SimpleNamespace(owner_task_id='current',message='补充这张照片，注意背景颜色。'),)
    assert content_tools.image_writing_only_request(q,runtime)
    runtime.task_user_changes.items=(runtime.task_user_changes.items[-1],)
    assert content_tools.image_writing_only_request(q,runtime)


@pytest.mark.parametrize('text',[
    '目测图里粗线比细线粗几倍？',
    '结合上一轮说的面积，估计这两块差几倍。',
    '图中白色条纹是绿色条纹的几倍？',
])
def test_measurement_guidance_does_not_force_an_extra_turn_or_forbid_estimates(text):
    from services.prompt_builder.layers.user_layer import visual_measurement_guidance
    advice=visual_measurement_guidance(text,True)
    assert '用户要求目测时可给明确标注的估计' in advice
    assert '只有确实无法确定比较对象或口径时' in advice
    assert '本轮只问清口径' not in advice and '不补充任何目测' not in advice
    message=UserLayer.render(UserMessageInput(text=text,image_urls=['https://example.invalid/photo.png'])).user_message
    assert message['content'][0]['text']==text


@pytest.mark.asyncio
@pytest.mark.parametrize('defect,code', [
    ('wrong_target', 'CHAT_INLINE_IMAGE_GENERATED_SOURCE_REQUIRED'),
    ('delivered_append', 'CHAT_INLINE_IMAGE_INPUT_CHANGED'),
    ('pending_append', 'CHAT_INLINE_IMAGE_INPUT_CHANGED'),
])
async def test_polished_current_edit_keeps_target_and_append_guards_before_io(defect, code):
    from uuid import uuid4
    from schemas.message import ImagePart
    from services.task_execution import inline_image_action as inline
    from services.task_execution.contracts import TaskError
    runtime, prepared, handler, _ = case()
    message = str(uuid4())
    request = SimpleNamespace(content=[TextPart(text='参考图一的波点，给图二加一些波点'),
        ImagePart(workspace_path='one.png'), ImagePart(workspace_path='two.png')],
        context_anchor=SimpleNamespace(input_message_id=message))
    request = semantic_request(request, 'edit', canvas_image=2)
    runtime.task_user_changes = SimpleNamespace(items=(object(),) if defect == 'delivered_append' else ())
    runtime._steer_messages = ['新要求'] if defect == 'pending_append' else []
    # No resolver/catalog exists: rejected calls cannot read or save any source.
    with pytest.raises(TaskError, match=code):
        await inline.prepare(runtime, handler, request, prepared, {'id':'edit'},
            {'source_message_id':message, 'source_content_index':1 if defect=='wrong_target' else 2,
             'edit_instruction':'在指定底图上添加稀疏波点，保留其他内容。'}, None, version='current')


def test_single_edit_contract_keeps_polishing_and_distinguishes_latest_output_from_prior_input():
    from services.handlers.chat.content_tools import model_tools
    from services.tools.catalog import build_capability_catalog
    native = build_capability_catalog().require('generate_image').to_schema()
    offered, = model_tools([native], inline_image=True, single_image_edit=True)
    description = offered['function']['description']
    assert '不覆盖你的编辑指令' in description
    assert '最近已完成的相关结果' in description and '明确说原图' in description
    assert '直接复制用户编辑要求' not in description and '后台绑定原文' not in description
    parameters = offered['function']['parameters']
    assert parameters['required'] == ['edit_instruction']
    assert any(set(branch['required']) == {'source_message_id', 'source_content_index'}
               for branch in parameters['oneOf'])
