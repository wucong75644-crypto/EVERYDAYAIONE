"""New positional protocol preserves content, identity and original package behavior."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agent.image.ecommerce_planner.assembly import assemble_prompts
from services.agent.image.ecommerce_planner.position_delivery import (
    POSITION_VERSION, POSITION_PROFILE, single_prose, validate_outline, visible_copy)
from services.agent.image.ecommerce_planner.prompt_resources import resources, resource_versions, plan_resources, wrapper
from services.agent.image.ecommerce_planner.contracts import text_hash
from tests.test_ecommerce_minimal_delivery import fixture


def setup(count=3, kind='detail_page', refs=3):
    service, evidence, _, _, _ = fixture(count, kind, refs)
    service.delivery_version = POSITION_VERSION
    evidence['visual_direction'] = {'visual_spec': evidence['visual_direction'],
        'shared_rules': '可爱造型字呼应商品轮廓；保留原色，照明来自真实遮挡。',
        'outline': [f'本张任务{i}，image_1整体与可见细节，新增信息并承接前后。' for i in range(1, count + 1)]}
    evidence['input_snapshot']['messages'][0]['parts'][0]['text'] = (
        '## 用户原始输入（留档）\n12款\n## 当前人工确认资料\n6款一套\nAI合理推断，经用户审核采用\n'
        '页数/材质未确认，不作事实。要可爱风格的背景，文字要和产品贴合的设计。')
    bodies, _ = resources(kind, POSITION_VERSION)
    state = {'draft': None, 'receipts': []}

    async def persist(row, lease, stage, before, after):
        assert before == state['draft']
        state['draft'] = deepcopy(after)
    service._persist_format = AsyncMock(side_effect=persist)
    async def save_attempt(*args, draft=None, **kwargs):
        state['draft'] = deepcopy(draft)
    service._save_attempt = AsyncMock(side_effect=save_attempt)

    def rpc(name, params):
        assert name == 'finish_ecom_position_attempt'
        assert params['p_expected_draft'] == state['draft']
        state['draft'] = deepcopy(params['p_draft'])
        state['receipts'].append(deepcopy(params))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data={'outcome': 'saved'}))
    service.scope = SimpleNamespace(rpc=rpc)
    async def call(row, lease, stage, prompt, messages, *, position=None):
        body = json.loads(messages[1]['content'][-1]['text'])
        pos = body['current_position']
        return f'【本张任务】第{pos}张；image_1取真实正面，文字依轮廓分行，保留原印刷。', {'attempt_id': str(pos)}
    service._call = AsyncMock(side_effect=call)
    validate = lambda value: assemble_prompts(value, evidence['input_snapshot'],
        evidence['product_selling_points'], evidence['visual_direction'])
    return service, evidence, bodies, state, validate


async def run(service, evidence, bodies, validate):
    snapshot = evidence['input_snapshot']
    return await service._stage_images({'id': 'plan'}, 'lease', bodies[2],
        wrapper(3, snapshot['task_type'], POSITION_VERSION), evidence, snapshot['messages'],
        snapshot['references'], ['https://example.invalid/test'] * len(snapshot['references']), validate)


@pytest.mark.parametrize('kind', ['main_images', 'detail_page'])
@pytest.mark.parametrize('count,refs', [(1,1), (6,3), (4,7), (15,1)])
async def test_exact_tasks_context_shared_rules_and_actual_request(kind, count, refs):
    service, evidence, bodies, state, validate = setup(count, kind, refs)
    result, usage = await run(service, evidence, bodies, validate)
    assert len(result['images']) == service._call.await_count == count
    assert len(state['draft']['positions']) == count
    for i, call in enumerate(service._call.call_args_list, 1):
        content = call.args[4][1]['content']
        payload = json.loads(content[0]['text'])
        assert json.loads(content[-1]['text'])['current_position'] == i
        assert content[:-1] == service._call.call_args_list[0].args[4][1]['content'][:-1]
        assert payload['settings']['image_count'] == count
        assert len(payload['reference_inventory']) == refs
        assert payload['raw_user_texts'][0]['text'] == evidence['input_snapshot']['messages'][0]['parts'][0]['text']
    for item in result['images']:
        assert evidence['visual_direction']['shared_rules'] in item['request_text']
        assert evidence['visual_direction']['outline'][0] not in item['request_text']
        assert '6款一套' in item['request_text'] and '留档' in item['request_text']
        assert item['references'] == evidence['input_snapshot']['references']
        assert item['request_text'] in item['scheme_markdown']
        assert text_hash(item['request_text']) == item['request_text_sha256']
    assert usage['local_draft'] == state['draft']
    assert result['generation_validation'] == 'pending'


async def test_connection_failure_and_restart_reuse_valid_positions_without_shifting():
    service, evidence, bodies, state, validate = setup(4)
    original = service._call.side_effect
    async def failing(*args, **kwargs):
        payload = json.loads(args[4][1]['content'][-1]['text'])
        if payload['current_position'] == 3:
            raise ConnectionError('connection interrupted')
        return await original(*args, **kwargs)
    service._call.side_effect = failing
    with pytest.raises(ConnectionError):
        await run(service, evidence, bodies, validate)
    first = deepcopy(state['draft']['positions'])
    assert first['1']['prompt'] and first['2']['prompt'] and first['3'] == {}
    evidence['stage_drafts'] = {'3': deepcopy(state['draft'])}
    service._call = AsyncMock(side_effect=original)
    result, _ = await run(service, evidence, bodies, validate)
    assert service._call.await_count == 1
    assert state['draft']['positions']['1'] == first['1']
    assert state['draft']['positions']['2'] == first['2']
    assert [item['position'] for item in result['images']] == [1,2,3,4]


async def test_duplicate_draft_repairs_current_position_before_sealing():
    service, evidence, bodies, state, validate = setup(2)
    first = 'image_1真实正面，保留原印刷；第一张完整设计。'
    service._call = AsyncMock(side_effect=[(first,{'attempt_id':'1'}), (first,{'attempt_id':'2'}),
        ('image_1真实细节，保留原印刷；第二张不同设计。',{'attempt_id':'3'})])
    result, _ = await run(service, evidence, bodies, validate)
    assert service._call.await_count == 3
    assert result['images'][0]['design']['prompt'] == first
    assert state['receipts'][0]['p_draft']['positions']['2'] == {}
    assert len(state['draft']['attempt_outputs']) == 3


async def test_six_model_calls_overlap_and_results_keep_position_order():
    service, evidence, bodies, state, validate = setup(6)
    all_started = asyncio.Event()
    started = set()

    async def provider(*args, position):
        started.add(position)
        if len(started) == 6:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), 1)
        await asyncio.sleep((6 - position) * .01)
        return f'image_1第{position}张独立正文。', {'attempt_id': str(position)}

    service._call = AsyncMock(side_effect=provider)
    result, _ = await run(service, evidence, bodies, validate)
    assert [receipt['p_usage']['position'] for receipt in state['receipts']] == [6, 5, 4, 3, 2, 1]
    assert [image['position'] for image in result['images']] == list(range(1, 7))
    assert not result['planning_review_scope']['triggered_reviews']


async def test_targeted_review_runs_only_after_signal_and_repairs_only_affected_position():
    service, evidence, bodies, state, validate = setup(3)
    review_inputs = []
    calls = []

    async def provider(*args, position):
        details = json.loads(args[4][1]['content'][-1]['text'])
        calls.append(position)
        if position == 0:
            review_inputs.append(details)
            report = {'repair_positions': [1] if len(review_inputs) == 1 else [], 'reason': '具体的跨稿检查结果'}
            return json.dumps(report), {'attempt_id': f'review{len(review_inputs)}'}
        if position == 1 and calls.count(1) == 1:
            return json.dumps({'prompt': 'image_1第一张待核对正文。',
                'review_signal': {'positions': [1, 2], 'reason': '第1张和第2张表达同一结构'}}), {'attempt_id': 'first'}
        return f'image_1第{position}张完整正文。', {'attempt_id': str(position)}

    service._call = AsyncMock(side_effect=provider)
    result, _ = await run(service, evidence, bodies, validate)
    assert result['status'] == 'ready'
    assert calls == [1, 2, 3, 0, 1, 0]
    assert all(set(entry['final_drafts']) == {'1', '2'} for entry in review_inputs)
    assert len(result['planning_review_scope']['triggered_reviews']) == 2


async def test_repeated_cross_draft_problem_stops_after_bounded_repairs():
    service, evidence, bodies, state, validate = setup(2)
    calls = []

    async def provider(*args, position):
        calls.append(position)
        if position == 0:
            return '{"repair_positions":[1],"reason":"冲突仍存在"}', {'attempt_id': str(len(calls))}
        body = f'image_1第{position}张正文，第{len(calls)}次。'
        if calls == [1]:
            body = json.dumps({'prompt': body, 'review_signal': {
                'positions': [1, 2], 'reason': '两张描述同一部位但要求矛盾'}})
        return body, {'attempt_id': str(len(calls))}

    service._call = AsyncMock(side_effect=provider)
    with pytest.raises(ValueError, match='PLANNER_DELIVERY_REPAIR_EXHAUSTED'):
        await run(service, evidence, bodies, validate)
    assert calls.count(0) == 3
    assert calls.count(2) == 1
    evidence['stage_drafts'] = {'3': state['draft']}
    service._call.reset_mock()
    with pytest.raises(ValueError, match='PLANNER_DELIVERY_REPAIR_EXHAUSTED'):
        await run(service, evidence, bodies, validate)
    service._call.assert_not_awaited()


async def test_position_questions_are_consolidated_without_review():
    service, evidence, bodies, state, validate = setup(2)
    service._call = AsyncMock(side_effect=[
        ('{"questions":["请确认销售范围。"]}', {'attempt_id': '1'}),
        ('{"questions":["请确认销售范围。"]}', {'attempt_id': '2'}),
    ])
    result, _ = await run(service, evidence, bodies, validate)
    assert result == {'status': 'needs_input', 'questions': ['请确认销售范围。']}
    assert service._call.await_count == 2


async def test_invalid_delivery_exhaustion_survives_new_windows_without_more_paid_calls():
    from services.detail_page_recovery import plan_blocked
    service,evidence,bodies,state,validate=setup(2)
    service._call=AsyncMock(side_effect=[('image_1第一张完整正文。',{'attempt_id':'1'}),
        ('缺失素材身份',{'attempt_id':'2'}),('仍缺失素材身份',{'attempt_id':'3'}),('再次无身份',{'attempt_id':'4'})])
    with pytest.raises(ValueError,match='PLANNER_DELIVERY_REPAIR_EXHAUSTED'):
        await run(service,evidence,bodies,validate)
    assert service._call.await_count==4
    assert state['draft']['positions']['1']['prompt']=='image_1第一张完整正文。'
    evidence['stage_drafts']={'3':deepcopy(state['draft'])}
    service._call=AsyncMock(side_effect=AssertionError('exhausted draft must not create another paid call'))
    with pytest.raises(ValueError,match='PLANNER_DELIVERY_REPAIR_EXHAUSTED'):
        await run(service,evidence,bodies,validate)
    service._call.assert_not_awaited()
    assert plan_blocked({'status':'failed','recovery_state':{'last_error':{'code':'PLANNER_DELIVERY_REPAIR_EXHAUSTED'}}})


@pytest.mark.parametrize('wrap', [lambda text: text, lambda text: '```markdown\n'+text+'\n```',
    lambda text: json.dumps({'prompt':text}), lambda text: json.dumps({'prompts':[text]}),
    lambda text: json.dumps([text])])
def test_lossless_format_does_not_rewrite_prose(wrap):
    _, evidence, _, _, _ = setup(1, refs=1)
    text = 'image_1：正文\n准确文字“六款一套”，两行，左侧叠压非关键边缘。'
    assert single_prose(wrap(text), evidence['input_snapshot']) == text


def test_multi_position_or_invalid_identity_never_becomes_single_prose():
    _, evidence, _, _, _ = setup(1, refs=1)
    for raw in ['{"prompts":["image_1甲","image_1乙"]}', 'image_99甲', '只有标题']:
        with pytest.raises(ValueError): single_prose(raw, evidence['input_snapshot'])


@pytest.mark.parametrize('profile', [None, POSITION_PROFILE])
@pytest.mark.parametrize('kind', ['main_images', 'detail_page'])
def test_version_pin_and_original_professional_sections(profile, kind):
    bodies, _ = resources(kind, POSITION_VERSION, profile)
    legacy, _ = resources(kind, 'ecom-prompts.v4', 'jd.v1' if profile else None)
    for start, end in [('## 四、商品实际比例决定布局', '## 五、'),
        ('## 六、构图、配色、文案与自然照明共同规划', '## 七、'),
        ('## 七、文字与修饰服务商品主体', '## 八、')]:
        assert legacy[2][legacy[2].index(start):legacy[2].index(end)] in bodies[2]
    assert '## 二、从买家问题组织整页' in bodies[1]
    assert '### 6. 整页理解与选择检查' in bodies[1]
    assert legacy[0][legacy[0].index('# 商品理解与卖点拆解'):] in bodies[0]
    row = {'prompt_versions':resource_versions(POSITION_VERSION, profile),
        'input_snapshot':{'task_type':kind, 'platform':'jd' if profile else 'taobao'}}
    assert plan_resources(row)[0] == bodies
    row['prompt_versions']['assembly_version']='wrong'
    with pytest.raises(ValueError): plan_resources(row)


async def test_outline_stage_keeps_eight_sections_and_repairs_only_missing_field():
    service, evidence, bodies, _, _ = setup(2)
    value = deepcopy(evidence['visual_direction'])
    service._call.return_value = (json.dumps(value, ensure_ascii=False), {'attempt_id':'outline'})
    service._call.side_effect = None
    snapshot = evidence['input_snapshot']
    result, _ = await service._stage({'id':'plan'},'lease',2,bodies[1],wrapper(2,'detail_page',POSITION_VERSION),
        evidence,snapshot['messages'],snapshot['references'],['test']*len(snapshot['references']),
        lambda raw: validate_outline(raw,snapshot))
    assert result == value


async def test_missing_visual_section_repairs_only_that_section():
    service, evidence, bodies, state, _ = setup(2)
    value = deepcopy(evidence['visual_direction'])
    value['visual_spec'] = value['visual_spec'].split('## 8.')[0].strip()
    service._call = AsyncMock(side_effect=[(json.dumps(value), {'attempt_id':'visual'}),
        ('{"sections":["保留商品事实、字形和共同原则，构图随任务变化。"]}',{'attempt_id':'repair'})])
    snapshot=evidence['input_snapshot']
    result,_=await service._stage({'id':'plan'},'lease',2,bodies[1],wrapper(2,'detail_page',POSITION_VERSION),evidence,
        snapshot['messages'],snapshot['references'],['test']*len(snapshot['references']),lambda raw:validate_outline(raw,snapshot))
    assert service._call.await_count == 2
    assert result['shared_rules']==value['shared_rules']
    assert result['outline']==value['outline']
    assert result['visual_spec'].startswith(value['visual_spec'])


@pytest.mark.parametrize('key', ['visual_spec', 'shared_rules', 'outline'])
def test_all_visual_fields_reject_fixed_canvas_conflict(key):
    _, evidence, _, _, _ = setup(2)
    value=deepcopy(evidence['visual_direction'])
    if key=='outline':value[key][1]+='画布比例：9:16'
    else:value[key]+='画布比例：9:16'
    with pytest.raises(ValueError,match='FIXED_CANVAS_CONFLICT'):validate_outline(value,evidence['input_snapshot'])


@pytest.mark.parametrize('stage,key', [(1, 'product_selling_points'), (2, 'visual_direction')])
def test_singleton_stage_object_unwraps_without_changing_business_content(stage, key):
    service, evidence, _, _, _ = setup(2)
    value = evidence[key]
    plain, _ = service._page_candidate(json.dumps(value), stage, evidence)
    wrapped, audit = service._page_candidate(json.dumps([value]), stage, evidence)
    assert wrapped == plain
    assert audit['normalization'][0] == {'operation': 'singleton_object_array'}
    with pytest.raises(ValueError, match='JSON_OBJECT_REQUIRED'):
        service._page_candidate(json.dumps([value, value]), stage, evidence)


def test_top_level_prose_array_never_merges_distinct_positions():
    _, evidence, _, _, _ = setup(1)
    with pytest.raises(ValueError, match='SINGLE_PROMPT_REQUIRED'):
        single_prose(json.dumps(['image_1第一张', 'image_1第二张']), evidence['input_snapshot'])


async def test_reloaded_snapshot_keeps_identical_prefix_and_only_reissues_failed_slot():
    service, evidence, bodies, state, validate = setup(2)
    original = service._call.side_effect

    async def provider(*args, position):
        if position == 2:
            raise ConnectionError('failed position')
        return await original(*args, position=position)

    service._call = AsyncMock(side_effect=provider)
    with pytest.raises(ConnectionError):
        await run(service, evidence, bodies, validate)
    prefix = service._call.call_args_list[0].args[4][1]['content'][:-1]
    evidence['stage_drafts'] = {'3': deepcopy(state['draft'])}
    # Storage may reorder object fields, while preserving the order of lists.
    evidence = json.loads(json.dumps(evidence, sort_keys=True))
    service._call = AsyncMock(side_effect=original)
    await run(service, evidence, bodies, validate)
    assert service._call.await_count == 1
    assert service._call.call_args.args[4][1]['content'][:-1] == prefix


def test_numbered_outline_packaging_is_split_without_model_or_text_rewrite():
    service, evidence, _, _, _ = setup(3)
    original = deepcopy(evidence['visual_direction'])
    entries = ['第1屏｜整体。\n第一屏补充正文。', '第2屏｜局部。', '第3屏｜收尾。']
    original['outline'] = ['\n'.join(entries)]
    normalized, audit = service._page_candidate(json.dumps(original), 2, evidence)
    assert normalized == {**original, 'outline': entries}
    assert {'operation': 'split_numbered_outline'} in audit['normalization']
    assert validate_outline(normalized, evidence['input_snapshot'])['outline'] == entries
    original['outline'] = ['\n'.join(reversed(entries))]
    ambiguous, _ = service._page_candidate(json.dumps(original), 2, evidence)
    with pytest.raises(ValueError, match='OUTLINE_COUNT'):
        validate_outline(ambiguous, evidence['input_snapshot'])


@pytest.mark.parametrize('wrap', [lambda v: {'values': v}, lambda v: [{'values': v}],
    lambda v: {'values': [v]}, lambda v: {'outline': v}])
def test_outline_repair_accepts_unambiguous_complete_array(wrap):
    service, evidence, _, _, _ = setup(3)
    previous = deepcopy(evidence['visual_direction'])
    entries = ['第1屏｜整体', '第2屏｜局部', '第3屏｜收尾']
    result = service._page_merge_repair(2, previous, '', [['outline']], json.dumps(wrap(entries)), evidence)
    assert result == {**previous, 'outline': entries}


def test_outline_repair_preserves_valid_positions_and_rejects_wrong_label():
    service, evidence, _, _, _ = setup(3)
    previous = deepcopy(evidence['visual_direction'])
    previous['outline'] = ['第1屏｜原文']
    paths = [['outline', 1], ['outline', 2]]
    patch = {'outline': ['第2屏｜局部', '第3屏｜收尾']}
    assert service._page_merge_repair(2, previous, '', paths, json.dumps(patch), evidence)['outline'] == [
        previous['outline'][0], *patch['outline']]
    with pytest.raises(ValueError, match='UNLOCATABLE'):
        service._page_merge_repair(2, previous, '', paths, json.dumps({'outline': patch['outline'][::-1]}), evidence)


def test_visible_fact_copy_must_match_source_excerpt_before_rendering():
    _, evidence, _, _, _ = setup(1)
    snapshot = evidence['input_snapshot']
    facts = {'f1': '可见活页环与空白内页。'}
    value = {'prompt': 'image_1实物展示，标题「[[copy:1]]」。', 'copy': [{'text': '活页环', 'fact_id': 'f1'}]}
    assert visible_copy(json.dumps(value), snapshot, facts, True)[0] == 'image_1实物展示，标题「活页环」。'
    value['copy'][0]['text'] = '内页随心加减'
    with pytest.raises(ValueError, match='VISIBLE_COPY_EVIDENCE'):
        visible_copy(json.dumps(value), snapshot, facts, True)
    value['copy'] = []
    with pytest.raises(ValueError, match='VISIBLE_COPY_SLOTS'):
        visible_copy(json.dumps(value), snapshot, facts, True)
