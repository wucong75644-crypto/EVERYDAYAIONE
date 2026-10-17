"""JD page selection pins independent complete stages without changing old plans."""
from copy import deepcopy
import shutil

import pytest

from services.agent.image.ecommerce_planner import prompt_resources as prompts
from services.agent.image.ecommerce_planner.format_delivery import PROMPTS_VERSION


def row(platform='jd', pinned=True, kind='main_images'):
    return {'input_snapshot': {'platform': platform, 'task_type': kind},
        'prompt_versions': prompts.resource_versions(PROMPTS_VERSION, prompts.JD_PROFILE if pinned else None)}


@pytest.mark.parametrize('kind', ['main_images', 'detail_page'])
def test_jd_loads_independent_three_stage_resources_with_compatible_schema(kind):
    baseline, schema = prompts.resources(kind, PROMPTS_VERSION)
    jd, jd_schema = prompts.plan_resources(row(kind=kind))
    assert len(jd) == 3 and all(a != b for a, b in zip(jd, baseline))
    assert jd_schema == schema
    assert prompts.resource_versions(PROMPTS_VERSION)['resources_sha256'] != row()['prompt_versions']['resources_sha256']
    assert prompts.resources(kind, PROMPTS_VERSION)[0] == baseline


@pytest.mark.parametrize('platform', ['auto', 'taobao', 'tmall', 'pdd'])
def test_non_jd_selection_retains_default_package(platform):
    assert prompts.profile_for_platform(platform) is None
    assert prompts.plan_resources(row(platform, pinned=False)) == prompts.resources('main_images', PROMPTS_VERSION)


def test_old_jd_plan_retries_its_original_package_even_after_profile_rollout():
    original = row(pinned=False)
    assert prompts.plan_resources(original) == prompts.resources('main_images', PROMPTS_VERSION)
    assert 'prompt_profile' not in original['prompt_versions']


@pytest.mark.parametrize('change', ['profile', 'hash', 'platform'])
def test_invalid_pins_fail_instead_of_silently_falling_back(change):
    plan = deepcopy(row())
    if change == 'profile': plan['prompt_versions']['prompt_profile'] = 'jd.missing'
    elif change == 'hash': plan['prompt_versions']['resources_sha256'][0] = '0' * 64
    else: plan['input_snapshot']['platform'] = 'taobao'
    with pytest.raises(ValueError, match='PLANNER_RESOURCE_VERSION_MISMATCH'):
        prompts.plan_resources(plan)


def test_modified_jd_stage_is_rejected_before_planning(tmp_path, monkeypatch):
    saved = row()
    shutil.copytree(prompts.ROOT.parent / 'jd-v1', tmp_path / 'jd-v1')
    monkeypatch.setattr(prompts, 'ROOT', tmp_path / 'v3')
    path = tmp_path / 'jd-v1' / '02-visual-direction.md'
    path.write_text(path.read_text() + '\nmodified')
    with pytest.raises(ValueError, match='PLANNER_RESOURCE_VERSION_MISMATCH'):
        prompts.plan_resources(saved)


@pytest.mark.parametrize('kind', ['main_images', 'detail_page'])
@pytest.mark.parametrize('product', ['客户上传的记录用品', '客户上传的收藏收纳用品'])
@pytest.mark.parametrize('style', ['商务办公场景，背景有真实空间，不要促销装饰', '清爽自然的使用场景，保留产品原色'])
async def test_user_background_request_reaches_all_three_jd_stage_calls(kind, product, style):
    """Transport contract only; mocked replies do not prove AI style comprehension."""
    import json
    from tests.test_ecommerce_minimal_delivery import fixture, run_stage
    service, evidence, _, output, _ = fixture(2, kind)
    raw = f'  {product}\n{style}  '
    snapshot = evidence['input_snapshot']
    snapshot['platform'] = 'jd'
    snapshot['messages'] = [{'source_id': 'project:test', 'parts': [{'content_index': 0, 'text': raw}]}]
    bodies, schema = prompts.plan_resources(row(kind=kind))
    evidence['product_schema'] = schema
    replies = [json.dumps(evidence['product_selling_points'], ensure_ascii=False),
        evidence['visual_direction'], json.dumps(output, ensure_ascii=False)]
    for stage, reply in enumerate(replies, 1):
        service._call.reset_mock()
        service._call.return_value = (reply, {'attempt_id': f'a{stage}'})
        await run_stage(service, evidence, bodies, stage, lambda value: value)
        sent = service._call.call_args.args[-1]
        assert sent[0]['content'].startswith(bodies[stage - 1])
        payload = json.loads(sent[1]['content'][0]['text'])
        assert payload['raw_user_texts'] == [{'source_ref': 'text_1', 'text': raw}]
        assert payload['settings']['platform'] == 'jd'
        assert payload['settings']['task_type'] == kind
        assert [ref['source_ref'] for ref in payload['reference_inventory']] == [
            f'image_{n}' for n in range(1, len(snapshot['references']) + 1)]
        if stage > 1:
            assert payload['product_selling_points'] == evidence['product_selling_points']
        if stage == 3:
            assert payload['visual_direction'] == evidence['visual_direction']
