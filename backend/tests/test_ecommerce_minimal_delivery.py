"""Minimal delivery keeps professional text and moves deterministic work to code."""
from copy import deepcopy
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from services.agent.image.ecommerce_planner.assembly import assemble_designs, assemble_prompts
from services.agent.image.ecommerce_planner.contracts import VISUAL_SECTIONS, validate_product
from services.agent.image.ecommerce_planner.format_delivery import (PROMPTS_VERSION, FormatError,
    decode_formatted, strict_object, normalize_visual, visual_sections, preserve_string_tokens)
from services.agent.image.ecommerce_planner.prompt_resources import resources, resource_versions, wrapper
from tests.ecommerce_design_fixtures import design_fixture
from tests.test_ecommerce_workflow import planner


def fixture(count=2, kind='main_images', refs=3):
    designs, evidence, references = design_fixture(count, kind, refs)
    bodies, wire = resources(kind, PROMPTS_VERSION)
    evidence['product_schema'] = wire
    evidence['stage_drafts'] = {}
    prompts = {'prompts': ['\n'.join([f'第{i}:image_1的商品正面放中央；image_2的搭扣细节放右下。',
        *[image[key] for key in ('scene','layout','props_and_decoration','lighting','text_layout',
            'product_preservation','execution_constraints','negative_additions')],
        '承接前屏主体识别，后屏展开细节，上下边界留白，关键商品在本屏内完整。'])
        for i, image in enumerate(designs['images'], 1)]}
    service = planner()
    service.page_execution = True
    service.delivery_version = PROMPTS_VERSION
    service._call = AsyncMock()
    service._persist_format = AsyncMock()
    validator = lambda value: assemble_prompts(value, evidence['input_snapshot'],
        evidence['product_selling_points'], evidence['visual_direction'])
    return service, evidence, bodies, prompts, validator


async def run_stage(service, evidence, bodies, stage, validator):
    snapshot = evidence['input_snapshot']
    return await service._page_stage({'id':'plan'}, 'lease', stage, bodies[stage-1],
        wrapper(stage,snapshot['task_type'],service.delivery_version), evidence,
        snapshot['messages'], snapshot['references'], ['https://invalid.example/image']*len(snapshot['references']), validator)


def test_null_extras_keys_and_nullable_defaults_do_not_rewrite_facts():
    service,evidence,_,_,_ = fixture()
    original = evidence['product_selling_points']
    raw = deepcopy(original)
    raw.pop('schema_version'); raw['facts'][0]['/statement']=raw['facts'][0].pop('statement')
    raw['facts'][0]['unknown_provider_field']=None
    raw['product'].pop('sale_scope')
    candidate,audit = service._page_candidate(json.dumps(raw),1,evidence)
    assert candidate == original
    assert {item['operation'] for item in audit['normalization']} == {
        'known_key','null_extra','fixed_metadata','nullable_default'}
    validate_product(candidate,service._page_schema(1,evidence),evidence['input_snapshot'])


@pytest.mark.parametrize('raw', ['{"prompts":[],"prompts":["x"]}', '{"x":NaN}', '{"x":1}尾话'])
def test_ambiguous_json_is_not_extracted_or_silently_overwritten(raw):
    with pytest.raises(ValueError): strict_object(raw)


def test_collision_and_nonempty_unknown_are_not_silently_removed():
    service,evidence,_,_,_ = fixture()
    schema = service._page_schema(1,evidence)
    raw = deepcopy(evidence['product_selling_points'])
    raw['facts'][0]['/statement']='不同事实'
    with pytest.raises(FormatError,match='COLLISION'): decode_formatted(raw,schema)
    raw['facts'][0].pop('/statement'); raw['facts'][0]['unknown']='不可丢弃'
    candidate,_=decode_formatted(raw,schema)
    assert candidate['facts'][0]['unknown']=='不可丢弃'
    with pytest.raises(ValueError): validate_product(candidate,schema,evidence['input_snapshot'])


def test_format_reoutput_cannot_change_quoted_text_numbers_or_order():
    preserve_string_tokens('{"prompts":["image_1原文",]}','{"prompts":["image_1原文"]}')
    for raw,changed in [('{"x":12,}','{"x":13}'), ('{"x":"原文",}','{"x":"改写"}'),
            ('{"a":"甲","b":"乙",}','{"b":"乙","a":"甲"}')]:
        with pytest.raises(FormatError): preserve_string_tokens(raw,changed)


def test_visual_titles_share_normalizer_with_extractor_and_preserve_all_bodies():
    _,evidence,_,_,_=fixture()
    visual=evidence['visual_direction']
    for i,title in enumerate(VISUAL_SECTIONS,1):
        visual=visual.replace(f'## {i}. {title}',f'### **{i}、 {title}**')
    assert normalize_visual(visual)==evidence['visual_direction']
    assert visual_sections(visual)[7]==visual_sections(evidence['visual_direction'])[7]
    with pytest.raises(FormatError): visual_sections(visual+'\n## 8. 统一规则与变化范围\n冲突规则')
    empty='\n'.join(f'## {i}. {title}' for i,title in enumerate(VISUAL_SECTIONS,1))
    with pytest.raises(FormatError): visual_sections(empty)


@pytest.mark.parametrize('kind,count',[('main_images',7),('detail_page',7),('main_images',15),('detail_page',15)])
def test_program_assembly_keeps_body_reference_order_settings_and_exact_request(kind,count):
    _,evidence,_,prompts,validator=fixture(count,kind)
    before=deepcopy(evidence)
    final=validator(prompts)
    assert len(final['images'])==count and final['review_records']==[]
    for i,image in enumerate(final['images'],1):
        assert image['position']==i and image['name']==('详情页' if kind=='detail_page' else '主图')+f'第{i}张'
        assert image['design']['prompt']==prompts['prompts'][i-1]
        assert prompts['prompts'][i-1] in image['request_text']
        assert image['references']==evidence['input_snapshot']['references']
        assert image['aspect_ratio']=='3:4' and '清晰度参数：2K' in image['request_text']
        assert image['request_text'] in image['scheme_markdown']
        assert hashlib.sha256(image['request_text'].encode()).hexdigest()==image['request_text_sha256']
        offsets=[image['request_text'].index(ref['source_id']) for ref in image['references']]
        assert offsets==sorted(offsets)
    assert evidence==before


@pytest.mark.parametrize('body',['image_99商品放中央','image_3是唯一商品','未说明商品来源'])
def test_undefined_or_style_only_references_do_not_reach_image_tool(body):
    _,_,_,prompts,validator=fixture(1)
    prompts['prompts']=[body]
    with pytest.raises(FormatError): validator(prompts)


async def test_only_missing_prompt_is_written_existing_prompt_is_byte_identical():
    service,evidence,bodies,prompts,validator=fixture(2)
    first=prompts['prompts'][0]
    service._call.side_effect=[(json.dumps({'prompts':[first]}),{'attempt_id':'a'}),
        (json.dumps({'prompts':[prompts['prompts'][1]]}),{'attempt_id':'b'})]
    final,usage=await run_stage(service,evidence,bodies,3,validator)
    assert final['images'][0]['design']['prompt']==first
    assert len(final['images'])==2 and usage['attempt_id']=='b'
    request=json.loads(service._call.await_args.args[-1][1]['content'][0]['text'])
    assert request['targets_in_order']==[['prompts',1]]
    assert service._call.await_count==2


async def test_missing_visual_section_only_is_repaired():
    service,evidence,bodies,_,_=fixture()
    original=evidence['visual_direction']
    incomplete=original.split('## 8.')[0]
    service._call.side_effect=[(incomplete,{'attempt_id':'a'}),
        (json.dumps({'sections':[visual_sections(original)[7]]}),{'attempt_id':'b'})]
    final,_=await run_stage(service,evidence,bodies,2,
        lambda v:service._validate_visual(v,VISUAL_SECTIONS,evidence['input_snapshot']))
    assert final==original
    request=json.loads(service._call.await_args.args[-1][1]['content'][0]['text'])
    assert request['targets_in_order']==[['sections',7]]


async def test_stage_one_unknown_extra_repair_preserves_product_analysis():
    service,evidence,bodies,_,_=fixture()
    raw=deepcopy(evidence['product_selling_points']);raw['facts'][0]['unknown']='额外内容'
    service._call.side_effect=[(json.dumps(raw),{'attempt_id':'a'}),('{"values":[null]}',{'attempt_id':'b'})]
    final,_=await run_stage(service,evidence,bodies,1,
        lambda v:validate_product(v,service._page_schema(1,evidence),evidence['input_snapshot']))
    assert final==evidence['product_selling_points']
    request=json.loads(service._call.await_args.args[-1][1]['content'][0]['text'])
    assert request['targets_in_order']==[['facts',0,'unknown']]


async def test_syntax_formatting_has_no_images_skill_or_professional_rewrite():
    service,evidence,bodies,prompts,validator=fixture(1)
    correct=json.dumps(prompts,ensure_ascii=False)
    malformed=correct[:-1]+',}'
    service._call.side_effect=[(malformed,{'attempt_id':'a'}),(correct,{'attempt_id':'b'})]
    final,_=await run_stage(service,evidence,bodies,3,validator)
    assert final['images'][0]['design']['prompt']==prompts['prompts'][0]
    call=service._call.await_args.args[-1]
    assert isinstance(call[1]['content'],str) and len(call[0]['content'])<500
    assert 'reference_inventory' not in call[1]['content']
    assert service._persist_format.await_args_list[0].args[-1]['format_attempted'] is True


async def test_saved_legacy_slash_fields_are_locally_completed_without_model_call():
    service,evidence,bodies,_,_=fixture(1)
    designs,old,_=design_fixture(1)
    evidence.update(old);evidence['product_schema']=resources()[1]
    service.delivery_version='ecom-design.v3'
    raw=deepcopy(designs);raw['images'][0]['/name']=raw['images'][0].pop('name')
    evidence['stage_drafts']={'3':{'output':raw,'validation_error':'old'}}
    final,usage=await run_stage(service,evidence,bodies,3,
        lambda v:assemble_designs(v,evidence['input_snapshot'],evidence['product_selling_points'],evidence['visual_direction']))
    assert final['status']=='ready' and 'local_draft' in usage
    service._call.assert_not_awaited()
    assert service._persist_format.await_count==1


async def test_formatting_attempt_flag_survives_restart_and_cannot_repeat_identical_draft():
    service,evidence,bodies,_,validator=fixture(1)
    evidence['stage_drafts']={'3':{'output':'{"prompts":["image_1 incomplete"',
        'format_version':'ecom-format.v1','format_attempted':True,'repair_round':0}}
    with pytest.raises(FormatError,match='EXHAUSTED'):
        await run_stage(service,evidence,bodies,3,validator)
    service._call.assert_not_awaited()


@pytest.mark.parametrize('stage',[2,3])
async def test_question_only_is_terminal_and_cannot_execute_partial_prompts(stage):
    service,evidence,bodies,_,validator=fixture(1)
    service._call.return_value=('{"questions":["图片与商品名称冲突，请确认是哪款？"]}',{'attempt_id':'a'})
    result,_=await run_stage(service,evidence,bodies,stage,validator)
    assert result=={'status':'needs_input','questions':['图片与商品名称冲突，请确认是哪款？']}
    service._save_attempt.assert_not_awaited()


def test_versioned_resources_keep_legacy_and_minimal_contracts_separate():
    old,_=resources('detail_page'); new,wire=resources('detail_page',PROMPTS_VERSION)
    assert 'page_plan' in wrapper(3,'detail_page') and '{"prompts"' in wrapper(3,'detail_page',PROMPTS_VERSION)
    assert 'schema_version' not in wire['required']
    assert resource_versions(PROMPTS_VERSION)['stage_three_delivery_version']==PROMPTS_VERSION
    for phrase in ('照明','商品保真','原图','文字','自检','上下边界','整页'):
        assert phrase in old[2] and phrase in new[2]


def test_990_null_extras_are_removed_without_a_second_product_analysis():
    service,evidence,_,_,_=fixture()
    original=deepcopy(evidence['product_selling_points'])
    original['facts']=[{**deepcopy(original['facts'][0]),'id':f'f{i}'} for i in range(1,11)]
    original['selling_points']=[{**deepcopy(original['selling_points'][0]),'id':f's{i}',
        'priority':'primary' if i==1 else 'secondary'} for i in range(1,6)]
    original['gaps']=[{'id':f'g{i}','topic':'规格','missing_information':'尺寸未提供',
        'impact':'optional','affected_selling_point_ids':['s1']} for i in range(4)]
    raw=deepcopy(original)
    for group,count in [('facts',374),('selling_points',440),('gaps',176)]:
        for i in range(count):raw[group][i%len(raw[group])][f'unknown_{i}']=None
    cleaned,audit=service._page_candidate(json.dumps(raw,ensure_ascii=False),1,evidence)
    assert cleaned==original and len(audit['normalization'])==990
    validate_product(cleaned,service._page_schema(1,evidence),evidence['input_snapshot'])
    service._call.assert_not_awaited()


def test_all_legacy_nested_slash_keys_including_detail_nullable_objects_are_lossless():
    service,evidence,_,_,_=fixture(7,'detail_page')
    raw,old,_=design_fixture(7,'detail_page',3);evidence.update(old)
    service.delivery_version='ecom-design.v3'
    def slash(value):
        if isinstance(value,dict):return {'/'+key:slash(child) for key,child in value.items()}
        if isinstance(value,list):return [slash(child) for child in value]
        return value
    normalized,_=service._page_candidate(slash(raw),3,evidence)
    assert normalized==raw
    assert assemble_designs(normalized,evidence['input_snapshot'],evidence['product_selling_points'],
        evidence['visual_direction'])['status']=='ready'


@pytest.mark.parametrize('suffix',['该方案不通过，请勿生图。','另一份方案：{"prompts":["不同内容"]}'])
def test_formatter_cannot_discard_conflicting_verdict_or_second_object(suffix):
    with pytest.raises(FormatError):preserve_string_tokens('{"x":"正文"}'+suffix,'{"x":"正文"}')


@pytest.mark.parametrize('kind',['format','partial'])
async def test_a_window_budget_rejection_does_not_spend_an_unexecuted_recovery_action(kind):
    from services.agent.image.ecommerce_planner.recovery import PlannerRecoveryError
    service,evidence,bodies,prompts,validator=fixture(2)
    correct=json.dumps(prompts)
    raw=correct[:-1]+',}' if kind=='format' else json.dumps({'prompts':prompts['prompts'][:1]})
    service._call.side_effect=[(raw,{'attempt_id':'a'}),PlannerRecoveryError('ECOM_PLAN_RETRY_EXHAUSTED')]
    with pytest.raises(PlannerRecoveryError):await run_stage(service,evidence,bodies,3,validator)
    saved=service._persist_format.await_args.args[-1]
    assert saved['format_attempted'] is False and saved['repair_round']==0
    evidence['stage_drafts']={'3':deepcopy(saved)}
    service._call=AsyncMock(return_value=(correct if kind=='format' else json.dumps({'prompts':prompts['prompts'][1:]}),{'attempt_id':'b'}))
    result,_=await run_stage(service,evidence,bodies,3,validator)
    assert len(result['images'])==2
    service._call.assert_awaited_once()


def test_visual_whole_markdown_fence_is_a_bounded_wrapper_only():
    _,evidence,_,_,_=fixture()
    assert normalize_visual('```markdown\n'+evidence['visual_direction']+'\n```')==evidence['visual_direction']


def test_surplus_output_does_not_replace_requested_positions_and_is_audited():
    service,evidence,_,prompts,_=fixture(2)
    value,audit=service._page_candidate({'prompts':prompts['prompts']+['未请求的额外作品']},3,evidence)
    assert value==prompts
    assert audit['normalization']==[{'path':['prompts'],'operation':'unrequested_positions','count':1}]


def test_fixed_metadata_and_nullable_defaults_do_not_reinterpret_legacy_business_output():
    service,evidence,_,_,_=fixture()
    service.delivery_version='ecom-design.v3'
    original=deepcopy(evidence['product_selling_points']);original['schema_version']='other-business-schema'
    with pytest.raises(FormatError,match='VERSION_CONFLICT'):service._page_candidate(original,1,evidence)
    original['schema_version']='product-selling-points.v3';original['product'].pop('sale_scope')
    candidate,_=service._page_candidate(original,1,evidence)
    with pytest.raises(ValueError):validate_product(candidate,service._page_schema(1,evidence),evidence['input_snapshot'])


def test_legacy_advisory_tail_compatibility_keeps_design_and_rejects_conflict():
    service,evidence,_,_,_=fixture()
    value,old,_=design_fixture(2);evidence.update(old);service.delivery_version='ecom-design.v3'
    raw=json.dumps(value,ensure_ascii=False)
    candidate,audit=service._page_candidate(raw+'\n后续建议：\n- 优化背景',3,evidence)
    assert candidate==value and audit['normalization'][0]['operation']=='legacy_advisory_tail'
    with pytest.raises(ValueError):service._page_candidate(raw+'\n这组方案不通过',3,evidence)


def test_primary_priority_repair_never_targets_whole_selling_point_text():
    from services.agent.image.ecommerce_planner.format_delivery import product_paths
    service,evidence,_,_,_=fixture()
    raw=deepcopy(evidence['product_selling_points']);raw['selling_points'][0]['priority']='secondary'
    assert product_paths(raw,service._page_schema(1,evidence),evidence['input_snapshot'])==[['selling_points',0,'priority']]


async def test_repair_retains_original_draft_and_prior_normalization_audit():
    service,evidence,bodies,prompts,validator=fixture(2)
    initial={'/prompts':prompts['prompts'][:1]}
    service._call.side_effect=[(json.dumps(initial),{'attempt_id':'a'}),
        (json.dumps({'prompts':prompts['prompts'][1:]}),{'attempt_id':'b'})]
    await run_stage(service,evidence,bodies,3,validator)
    saved=service._persist_format.await_args.args[-1]
    assert json.loads(saved['original_output'])==initial
    assert len(saved['audit_history'])==2
    assert saved['audit_history'][0]['normalization'][0]['operation']=='known_key'
    assert saved['audit_history'][1]['normalization']==[]


async def test_visual_prefix_and_other_sections_survive_targeted_canvas_repair():
    from services.agent.image.ecommerce_planner.assembly import fixed_canvas_conflict
    from services.agent.image.ecommerce_planner.format_delivery import render_visual
    service,evidence,bodies,_,_=fixture()
    prefix='本套以温暖红金为基调，严格保留商品的原有配色。'
    visual=prefix+'\n\n'+evidence['visual_direction']
    assert normalize_visual(visual).startswith(prefix+'\n\n')
    sections=visual_sections(visual)
    assert fixed_canvas_conflict('画布比例：1:1',evidence['input_snapshot'])
    broken_sections=sections.copy();broken_sections[3]+='\n画布比例：1:1'
    broken=render_visual(broken_sections,prefix)
    service._call.side_effect=[(broken,{'attempt_id':'a'}),(json.dumps({'sections':[sections[3]]}),{'attempt_id':'b'})]
    result,_=await run_stage(service,evidence,bodies,2,
        lambda v:service._validate_visual(v,VISUAL_SECTIONS,evidence['input_snapshot']))
    assert result==normalize_visual(visual)
    request=json.loads(service._call.await_args.args[-1][1]['content'][0]['text'])
    assert request['targets_in_order']==[['sections',3]]


async def test_extra_metadata_is_removed_by_targeted_field_repair_without_rewriting_prompts():
    service,evidence,bodies,prompts,validator=fixture(2)
    service._call.side_effect=[(json.dumps({**prompts,'display_name':'不用模型起名'}),{'attempt_id':'a'}),
        ('{"values":[null]}',{'attempt_id':'b'})]
    final,_=await run_stage(service,evidence,bodies,3,validator)
    assert [x['design']['prompt'] for x in final['images']]==prompts['prompts']
    request=json.loads(service._call.await_args.args[-1][1]['content'][0]['text'])
    assert request['targets_in_order']==[['display_name']]


async def test_mixed_questions_and_prompts_cannot_become_ready_by_deleting_questions():
    service,evidence,bodies,prompts,validator=fixture(1)
    service._call.return_value=(json.dumps({**prompts,'questions':['商品到底是哪一款？']}),{'attempt_id':'a'})
    with pytest.raises(FormatError,match='EXHAUSTED'):await run_stage(service,evidence,bodies,3,validator)
    service._call.assert_awaited_once()


async def test_misplaced_prompt_key_and_value_use_one_lossless_format_pass():
    service,evidence,bodies,prompts,validator=fixture(3)
    broken={'prompts':prompts['prompts'][:1],prompts['prompts'][1]:prompts['prompts'][2]}
    service._call.side_effect=[(json.dumps(broken),{'attempt_id':'a'}),(json.dumps(prompts),{'attempt_id':'b'})]
    final,_=await run_stage(service,evidence,bodies,3,validator)
    assert [x['design']['prompt'] for x in final['images']]==prompts['prompts']
    service._call.assert_awaited()
    request=service._call.await_args.args[-1][1]['content']
    assert isinstance(request,str) and 'original_draft' in json.loads(request)
    saved=service._persist_format.await_args.args[-1]
    assert saved['format_attempted'] and saved['repair_round']==0
    assert json.loads(saved['original_output'])==broken


def test_formatter_padding_is_restored_but_inner_text_changes_are_rejected():
    raw='{"prompts":["  image_1首图  "],"\\n\\nimage_1第二张":"image_1第三张"}'
    formatted='{"prompts":["image_1首图","image_1第二张","image_1第三张"]}'
    restored=preserve_string_tokens(raw,formatted)
    assert json.loads(restored)['prompts']==['  image_1首图  ','\n\nimage_1第二张','image_1第三张']
    with pytest.raises(FormatError):preserve_string_tokens(raw,formatted.replace('第二张','第 二张'))
