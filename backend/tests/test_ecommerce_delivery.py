"""Single execution text, scoped repairs and bounded completed-reply decoding."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.agent.image.ecommerce_planner.contracts import validate_images, text_hash
from services.agent.image.ecommerce_planner.delivery import decode_delivery, DELIVERY_VERSION
from services.agent.image.ecommerce_planner.prompt_resources import wrapper
from services.agent.image.ecommerce_planner.recovery import PlannerRecoveryError
from tests.test_ecommerce_recovery_postgres import professional_outputs
from tests.test_ecommerce_workflow import planner

TAIL = "\n\n继续完善这组商品主图\n\n- 优化五张图的差异化分工\n- 挑选最适合做首图的方案"


def draft(count=1):
    ref = {'asset_id': str(uuid4()), 'role': 'product'}
    ref['source_id'] = ref['asset_id']
    product, visual, value = professional_outputs(ref)
    image = value['images'][0]
    image['scheme_markdown'] = image['scheme_markdown'].split('\n## 完整生图提示词')[0]
    value['images'] = [{**deepcopy(image), 'position': i, 'name': f'图片{i}',
        'positive_prompt': image['positive_prompt'] + f'\n本张任务：展示{i}'} for i in range(1, count + 1)]
    snapshot = {'image_count': count, 'references': [ref],
        'target_size': {'aspect_ratio': '1:1', 'resolution': '1K'}}
    evidence = {'input_snapshot': snapshot, 'product_selling_points': product, 'visual_direction': visual}
    return value, evidence, [ref]


@pytest.mark.parametrize('count', [1, 5, 15])
def test_single_copy_assembles_complete_display_and_identical_execution(count):
    value, evidence, _ = draft(count)
    saved = validate_images(value, evidence['input_snapshot'], assemble_display=True)
    assert len(saved['images']) == count
    for original, image in zip(value['images'], saved['images']):
        assert image['positive_prompt'] == original['positive_prompt']
        assert image['references'] == original['references']
        assert image['scheme_markdown'].endswith('## 负面提示词\n\n' + original['negative_prompt'])
        assert image['request_text'] == original['positive_prompt'] + '\n\n负面提示词：' + original['negative_prompt']
        assert image['request_text_sha256'] == text_hash(image['request_text'])
    # The persisted full format still passes the original strict validator.
    legacy = {**saved, 'images': [{k: v for k, v in image.items()
        if k not in {'item_id', 'request_text', 'request_text_sha256'}} for image in saved['images']]}
    assert validate_images(legacy, evidence['input_snapshot'])['status'] == 'ready'


def test_completed_object_advisory_does_not_change_json_strings():
    original = {'text': '正文中的 }、{、引号" 和换行\n不能改写'}
    output = json.dumps(original, ensure_ascii=False)
    value, audit = decode_delivery(output + TAIL)
    assert value == original
    assert audit['normalization'] == 'advisory_tail'
    assert audit['json_sha256'] == text_hash(output)
    assert audit['delivery_version'] == DELIVERY_VERSION


@pytest.mark.parametrize('output', [
    '说明\n{}', '```json\n{}\n```', '{"x":1', '{}\n{}',
    '{"x":1,"x":2}', '{"x":{"y":1,"y":2}}', '{"x":NaN}',
    '{}\n继续完善主图\n- 优化方案，当前审核不通过', '{}\nstatus=failed',
    '{}\n继续完善主图\n- 优化并替换参考图', '{}\n继续完善主图\n- 优化' + '长' * 513,
    '{}\n继续完善主图，这套方案不合格', '{}\n继续完善主图\n- 查看不可执行的方案',
    '{}\n继续完善主图\n' + '\n'.join('- 查看方案' for _ in range(13)),
])
def test_ambiguous_or_incomplete_output_never_becomes_ready(output):
    with pytest.raises(ValueError):
        decode_delivery(output)


@pytest.mark.parametrize('defect', ['unknown_reference', 'wrong_reference_order', 'blocked_review', 'missing_scheme_field'])
def test_single_copy_delivery_preserves_identity_completeness_and_review_gates(defect):
    value, evidence, refs = draft()
    value = deepcopy(value)
    if defect == 'unknown_reference':
        value['images'][0]['references'][0]['source_id'] = str(uuid4())
    elif defect == 'wrong_reference_order':
        second = {'asset_id': str(uuid4()), 'role': 'product'}
        second['source_id'] = second['asset_id']
        evidence['input_snapshot']['references'].append(second)
        value['images'][0]['references'] = [second, refs[0]]
        value['images'][0]['positive_prompt'] += '\n输入图片2—' + second['source_id']
    elif defect == 'blocked_review':
        value['review_records'][0]['conclusion'] = 'blocked'
    else:
        value['images'][0]['scheme_markdown'] = '## 方案内容\n\n## 视觉设定\n\n## 参考图使用方式\n未完整填写'
    decoded, _ = decode_delivery(json.dumps(value) + TAIL)
    with pytest.raises(ValueError):
        validate_images(decoded, evidence['input_snapshot'], assemble_display=True)


async def run_stage(service, evidence, refs):
    return await service._stage_images({'id': str(uuid4())}, 'lease', 'professional rules', wrapper(3),
        evidence, [{'text': '原始要求，发财风格'}], refs, ['https://example.invalid/product.png'],
        validator=lambda value: validate_images(value, evidence['input_snapshot'], assemble_display=True))


async def test_advisory_tail_passes_once_without_paid_repair():
    value, evidence, refs = draft(5)
    service = planner()
    service._call = AsyncMock(return_value=(json.dumps(value, ensure_ascii=False) + TAIL, {'user_credits': 2}))
    final, usage = await run_stage(service, evidence, refs)
    assert final['status'] == 'ready' and len(final['images']) == 5
    assert usage['normalization'] == 'advisory_tail' and usage['user_credits'] == 2
    service._call.assert_awaited_once()
    service._save_attempt.assert_not_awaited()


@pytest.mark.parametrize('defect', ['negative', 'wrong_position', 'missing_position'])
async def test_single_image_repair_keeps_other_images_and_receives_full_draft(defect):
    original, evidence, refs = draft(5)
    broken = deepcopy(original)
    if defect == 'negative':
        broken['images'][2]['negative_prompt'] = '错误负面稿'
    elif defect == 'wrong_position':
        broken['images'][2]['position'] = 15
    else:
        del broken['images'][2]['position']
    patch = {'images': [original['images'][2]], 'review_records': original['review_records']}
    service = planner()
    service._call = AsyncMock(side_effect=[(json.dumps(broken), {'user_credits': 1}),
        (json.dumps(patch), {'user_credits': 1})])
    saved, usage = await run_stage(service, evidence, refs)
    repair_messages = service._call.await_args_list[1].args[4]
    body = json.loads(repair_messages[1]['content'][0]['text'])
    assert body['previous_stage_three_output'] == broken
    assert body['repair_positions'] == [3]
    assert set(body['output_json_schema']['properties']) == {'images', 'review_records'}
    assert usage['repair_positions'] == [3]
    assert [image['positive_prompt'] for image in saved['images']] == [image['positive_prompt'] for image in original['images']]
    assert repair_messages[1]['content'][2]['image_url'] == 'https://example.invalid/product.png'
    assert service._save_attempt.await_count == 1


@pytest.mark.parametrize('invalid_patch', ['multiple_images', 'full_output', 'wrong_position', 'missing_field'])
async def test_patch_cannot_modify_unaffected_images_or_reset_retry_count(invalid_patch):
    original, evidence, refs = draft(5)
    broken = deepcopy(original)
    broken['images'][1]['negative_prompt'] = '错误负面稿'
    wrong_scope = {'images': [original['images'][1], original['images'][0]], 'review_records': original['review_records']}
    if invalid_patch == 'full_output':
        wrong_scope = deepcopy(original)
        wrong_scope['images'][0]['positive_prompt'] += '\n不允许改写的其他图片'
    elif invalid_patch == 'wrong_position':
        wrong_scope['images'] = [original['images'][0]]
    elif invalid_patch == 'missing_field':
        wrong_scope['images'] = [deepcopy(original['images'][1])]
        del wrong_scope['images'][0]['negative_prompt']
    correct = {'images': [original['images'][1]], 'review_records': original['review_records']}
    service = planner()
    service._call = AsyncMock(side_effect=[(json.dumps(broken), {}), (json.dumps(wrong_scope), {}), (json.dumps(correct), {})])
    saved, _ = await run_stage(service, evidence, refs)
    assert len(saved['images']) == 5 and service._call.await_count == 3
    body = json.loads(service._call.await_args_list[2].args[4][1]['content'][0]['text'])
    assert body['repair_positions'] == [2] and body['previous_stage_three_output'] == broken
    assert [image['positive_prompt'] for image in saved['images']] == [image['positive_prompt'] for image in original['images']]
    if invalid_patch in {'multiple_images', 'wrong_position'}:
        assert 'PLANNER_REPAIR_SCOPE_CHANGED' in body['validation_error']


async def test_syntax_repair_has_raw_first_draft_instead_of_starting_over():
    value, evidence, refs = draft()
    text = json.dumps(value)
    service = planner()
    service._call = AsyncMock(side_effect=[(text[:-1], {}), (text, {})])
    await run_stage(service, evidence, refs)
    body = json.loads(service._call.await_args_list[1].args[4][1]['content'][0]['text'])
    assert body['previous_stage_three_output'] == text[:-1]
    assert body['repair_positions'] == []


async def test_sequential_scoped_repairs_keep_previously_fixed_and_unaffected_images():
    original, evidence, refs = draft(5)
    broken = deepcopy(original)
    for position in (2, 4):
        broken['images'][position - 1]['negative_prompt'] = '缺少保真限制'
    replacements = [{'images': [original['images'][position - 1]], 'review_records': original['review_records']}
        for position in (2, 4)]
    service = planner()
    service._call = AsyncMock(side_effect=[(json.dumps(value), {}) for value in [broken, *replacements]])
    saved, _ = await run_stage(service, evidence, refs)
    last_body = json.loads(service._call.await_args_list[2].args[4][1]['content'][0]['text'])
    assert last_body['repair_positions'] == [4]
    assert last_body['previous_stage_three_output']['images'][1] == original['images'][1]
    assert [image['negative_prompt'] for image in saved['images']] == [image['negative_prompt'] for image in original['images']]
    assert service._call.await_count == 3 and service._save_attempt.await_count == 2


@pytest.mark.parametrize('stage', [1, 2, 3])
async def test_stage_three_idle_budget_is_bounded_by_parent_and_persisted_deadline(monkeypatch, stage):
    from services.adapters.base import StreamChunk
    service = planner()
    service.owner.execution_budget = SimpleNamespace(remaining=50)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=20)
    service._reserve.return_value['deadline'] = deadline.isoformat()
    requests = []
    async def stream(*args, **kwargs):
        yield StreamChunk(content='completed', prompt_tokens=10, completion_tokens=1)
    session = SimpleNamespace(stream_chat=stream, last_result=SimpleNamespace(status='completed', usage={}), close=AsyncMock())
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',
        lambda: SimpleNamespace(open_chat=lambda request: requests.append(request) or session))
    _, usage = await service._call({'id': str(uuid4())}, 'lease', stage, 'rules', [])
    request = requests[0]
    if stage == 3:
        assert request.timeout is None and request.idle_timeout == 30
        assert 0 < request.budget.remaining <= 15
        service.owner.execution_budget.remaining = 2
        assert request.budget.remaining <= 2
    else:
        assert request.idle_timeout is None and request.timeout <= 20
    assert usage['first_output_ms'] is not None and usage['output_characters'] == 9


async def test_insufficient_settlement_time_stops_before_provider(monkeypatch):
    service = planner()
    service._reserve.return_value['deadline'] = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    gateway = SimpleNamespace(open_chat=AsyncMock())
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway', lambda: gateway)
    with pytest.raises(PlannerRecoveryError, match='ECOM_PLAN_PARENT_BUDGET_EXHAUSTED'):
        await service._call({'id': str(uuid4())}, 'lease', 3, 'rules', [])
    gateway.open_chat.assert_not_called()
    assert service._finish.await_args.args[5] == 'rejected'


@pytest.mark.parametrize('terminal', [None, 'incomplete'])
async def test_complete_looking_json_without_completed_receipt_is_uncertain(monkeypatch, terminal):
    from services.adapters.base import StreamChunk
    service = planner()
    async def stream(*args, **kwargs):
        yield StreamChunk(content='{"status":"ready"}', prompt_tokens=10)
    session = SimpleNamespace(stream_chat=stream,
        last_result=SimpleNamespace(status=terminal, usage={}) if terminal else None, close=AsyncMock())
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',
        lambda: SimpleNamespace(open_chat=lambda request: session))
    with pytest.raises(PlannerRecoveryError, match='ECOM_PLAN_EXECUTION_UNCERTAIN'):
        await service._call({'id': str(uuid4())}, 'lease', 3, 'rules', [])
    service._reserve.assert_awaited_once()
    assert service._finish.await_args.args[5] == 'uncertain'
    assert service._finish.await_args.args[4]['output_characters'] > 0
    session.close.assert_awaited_once()
