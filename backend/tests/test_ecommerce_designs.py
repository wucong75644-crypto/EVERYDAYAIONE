"""Both planning modes use a fixed server frame and one creative source."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.agent.image.ecommerce_planner.assembly import assemble_designs, FIDELITY
from services.agent.image.ecommerce_planner.contracts import LABELS, VISUAL_SECTIONS, validate_product, text_hash
from services.agent.image.ecommerce_planner.designs import DesignValidationError, validate_designs, apply_repair
from services.agent.image.ecommerce_planner.inputs import model_input, source_bindings, FixedSettings
from services.agent.image.ecommerce_planner.prompt_resources import resources, wrapper
from tests.ecommerce_design_fixtures import design_fixture
from tests.test_ecommerce_workflow import planner


@pytest.mark.parametrize("task_type", ["main_images", "detail_page"])
@pytest.mark.parametrize("count", [1, 5, 15])
def test_both_modes_preserve_all_design_and_bindings_in_the_final_image_request(task_type, count):
    value, evidence, refs = design_fixture(count, task_type, 3)
    before = deepcopy(value)
    saved = assemble_designs(value, evidence["input_snapshot"], evidence["product_selling_points"])
    assert value == before and len(saved["images"]) == count
    for original, item in zip(value["images"], saved["images"]):
        assert item["references"] == refs and item["design"] == original
        assert item["request_text"] == item["positive_prompt"] + "\n\n负面提示词：" + item["negative_prompt"]
        assert item["request_text_sha256"] == text_hash(item["request_text"])
        assert item["request_text"] == item["scheme_markdown"].split("## 完整生图提示词\n\n")[1].replace("\n\n## 负面提示词\n\n", "\n\n负面提示词：")
        assert "画布比例：3:4" in item["positive_prompt"] and "清晰度参数：2K" in item["positive_prompt"]
        assert "目标平台：淘宝" in item["positive_prompt"] and "新增文字语言：中文（简体）" in item["positive_prompt"]
        assert FIDELITY in item["request_text"]
        for key in ("scene", "layout", "lighting", "text_layout", "product_preservation", "execution_constraints", "negative_additions"):
            assert original[key] in item["request_text"]
        for number, ref in enumerate(refs, 1):
            assert f"输入图片{number}—{ref['asset_id']}—{ref['role']}" in item["request_text"]
        assert all(item["positive_prompt"].count(f"【{label}】") == 1 for label in LABELS)
        assert ("【上下边界与整页承接】" in item["request_text"]) == (task_type == "detail_page")
    assert saved["generation_validation"] == "pending"
    assert saved["page_assembly_validation"] == ("pending" if task_type == "detail_page" else "not_applicable")


def test_model_sees_fixed_settings_and_verbatim_text_without_technical_bindings():
    _, evidence, refs = design_fixture(15, "detail_page", 3)
    snapshot = evidence["input_snapshot"]
    before = deepcopy(snapshot)
    body = model_input(snapshot, snapshot["messages"], refs)
    assert body["raw_user_texts"] == [{"source_ref": "text_1", "text": snapshot["messages"][0]["parts"][0]["text"]}]
    assert body["settings"]["image_count"] == 15 and len(body["reference_inventory"]) == 3
    assert [ref["number"] for ref in body["reference_inventory"]] == [1, 2, 3]
    wire = json.dumps(body)
    assert all(ref["asset_id"] not in wire for ref in refs)
    assert snapshot["messages"][0]["message_id"] not in wire and snapshot == before
    bindings = source_bindings(snapshot)
    assert bindings["image_1"]["source_id"] == refs[0]["asset_id"]
    assert bindings["text_1"]["source_id"] == snapshot["messages"][0]["message_id"] + ":0"
    snapshot["target_size"]["reference_source_id"] = "private-server-binding"
    assert "private-server-binding" not in json.dumps(model_input(snapshot, snapshot["messages"], refs))


def test_common_visual_boundaries_are_copied_once_by_program_and_conflicting_canvas_is_rejected():
    value, evidence, _ = design_fixture(5)
    visual = evidence["visual_direction"]
    visual = visual[:visual.index("## 8. ")] + "## 8. 统一规则与变化范围\n保留红金主题；不得为统一风格把商品染成金色。"
    saved = assemble_designs(value, evidence["input_snapshot"], evidence["product_selling_points"], visual)
    assert all("保留红金主题；不得为统一风格把商品染成金色。" in image["request_text"] for image in saved["images"])
    with pytest.raises(ValueError, match="FIXED_CANVAS_CONFLICT"):
        planner()._validate_visual(visual + "\n画布比例为1:1。", VISUAL_SECTIONS, evidence["input_snapshot"])
    value["images"][0]["execution_constraints"] += "清晰度参数：4K。"
    with pytest.raises(DesignValidationError, match="FIXED_CANVAS_CONFLICT"):
        assemble_designs(value, evidence["input_snapshot"], evidence["product_selling_points"], visual)


@pytest.mark.parametrize("defect,code", [("source", "UNKNOWN_FACT_SOURCE"), ("style", "STYLE_REFERENCE_AS_FACT"),
    ("kind", "FACT_SOURCE_KIND_MISMATCH"), ("selling_point", "UNSUPPORTED_SELLING_POINT")])
def test_fact_aliases_cannot_replace_product_evidence_or_expand_conflicted_facts(defect, code):
    _, evidence, _ = design_fixture(1, ref_count=3)
    product = deepcopy(evidence["product_selling_points"])
    if defect == "selling_point":
        product["facts"][0]["status"] = "conflicted"
    else:
        product["facts"][0]["sources"][0]["source_ref"] = {"source": "image_9", "style": "image_3", "kind": "text_1"}[defect]
    _, schema = resources()
    with pytest.raises(ValueError, match=code):
        validate_product(product, schema, evidence["input_snapshot"])


def test_validation_collects_all_faulty_images_and_rejects_unknown_business_facts():
    value, evidence, _ = design_fixture(5)
    del value["images"][1]["lighting"]
    del value["images"][3]["text_layout"]
    with pytest.raises(DesignValidationError) as raised:
        validate_designs(value, evidence["input_snapshot"], evidence["product_selling_points"])
    assert {tuple(issue["path"]) for issue in raised.value.issues} == {("images", 1, "lighting"), ("images", 3, "text_layout")}
    value, evidence, _ = design_fixture()
    value["images"][0]["fact_ids"] = ["invented_fact"]
    with pytest.raises(DesignValidationError, match="UNSUPPORTED_IMAGE_FACT"):
        assemble_designs(value, evidence["input_snapshot"], evidence["product_selling_points"])


@pytest.mark.parametrize("defect", ["boundary", "coverage", "path", "missing_link", "wrong_ratio"])
def test_detail_page_dependencies_canvas_and_all_adjacent_boundaries_are_checked(defect):
    value, evidence, _ = design_fixture(5, "detail_page")
    if defect == "boundary":
        value["page_plan"]["boundaries"].pop()
    elif defect == "coverage":
        value["page_plan"]["coverage"][0]["positions"] = [1]
    elif defect == "path":
        value["page_plan"]["generation_path"] = "previous_generated_screen"
    elif defect == "missing_link":
        value["images"][2]["page_link"] = None
    else:
        value["images"][0]["scene"] += "画布采用1:1。"
    with pytest.raises(DesignValidationError):
        assemble_designs(value, evidence["input_snapshot"], evidence["product_selling_points"])


@pytest.mark.parametrize("task_type", ["main_images", "detail_page"])
async def test_three_stage_content_chain_is_serial_and_uses_complete_latest_rules(task_type):
    value, evidence, refs = design_fixture(15, task_type, 3)
    bodies, schema = resources(task_type)
    service = planner()
    service._call = AsyncMock(side_effect=[(json.dumps(evidence["product_selling_points"]), {}),
        (evidence["visual_direction"], {}), (json.dumps(value), {})])
    snapshot = evidence["input_snapshot"]
    messages = snapshot["messages"]
    urls = [f"https://example.invalid/{number}.png" for number in range(3)]
    first, _ = await service._stage({"id": str(uuid4())}, "lease", 1, bodies[0], wrapper(1, task_type),
        {"input_snapshot": snapshot, "product_schema": schema}, messages, refs, urls,
        validator=lambda v: validate_product(v, schema, snapshot))
    second, _ = await service._stage({"id": str(uuid4())}, "lease", 2, bodies[1], wrapper(2, task_type),
        {"input_snapshot": snapshot, "product_selling_points": first}, messages, refs, urls,
        validator=lambda v: service._validate_visual(v, __import__("services.agent.image.ecommerce_planner.contracts", fromlist=["VISUAL_SECTIONS"]).VISUAL_SECTIONS))
    final, _ = await service._stage_images({"id": str(uuid4())}, "lease", bodies[2], wrapper(3, task_type),
        {"input_snapshot": snapshot, "product_selling_points": first, "visual_direction": second}, messages, refs, urls,
        validator=lambda v: assemble_designs(v, snapshot, first))
    assert len(final["images"]) == 15
    sent = service._call.await_args_list
    assert [call.args[2] for call in sent] == [1, 2, 3]
    for index, call in enumerate(sent):
        assert bodies[index] in call.args[3]
        assert ("商品理解与卖点拆解", "商品套图视觉定义", "完整执行提示词的要求")[index] in bodies[index]
        content = call.args[4][1]["content"]
        assert [part["image_url"] for part in content if part["type"] == "input_image"] == urls
        body = json.loads(content[0]["text"])
        assert body["raw_user_texts"][0]["text"] == messages[0]["parts"][0]["text"]
        assert all(ref["asset_id"] not in content[0]["text"] for ref in refs)
        if index >= 1:
            assert body["product_selling_points"] == first
        if index == 2:
            assert body["visual_direction"] == second
    service._save_attempt.assert_not_awaited()


async def test_common_errors_are_repaired_together_without_sending_or_overwriting_unaffected_images():
    original, evidence, refs = design_fixture(15)
    broken = deepcopy(original)
    del broken["images"][1]["lighting"]
    del broken["images"][12]["lighting"]
    patch = {"patches": [{"path": ["images", index, "lighting"], "value": original["images"][index]["lighting"]} for index in (1, 12)],
        "review_records": original["review_records"]}
    service = planner()
    service._call = AsyncMock(side_effect=[(json.dumps(broken), {}), (json.dumps(patch), {})])
    final, _ = await service._stage_images({"id": str(uuid4())}, "lease", "rules", wrapper(3), evidence,
        evidence["input_snapshot"]["messages"], refs, ["url"],
        validator=lambda v: assemble_designs(v, evidence["input_snapshot"], evidence["product_selling_points"]))
    body = json.loads(service._call.await_args_list[1].args[4][1]["content"][0]["text"])
    assert {tuple(t["path"]) for t in body["repair_targets"]} == {("images", 1, "lighting"), ("images", 12, "lighting")}
    assert "previous_stage_three_output" not in body
    assert [i["design"] for i in final["images"]] == original["images"]
    saved = service._save_attempt.await_args.kwargs["draft"]
    assert saved["output"] == broken and len(saved["issues"]) == 2
    assert service._call.await_count == 2


def test_field_patch_cannot_touch_valid_design_or_remove_required_content():
    value, _, _ = design_fixture(5)
    issues = [{"path": ["images", 2, "lighting"], "code": "missing"}]
    for path, op in [(["images", 2], "replace"), (["images", 0, "lighting"], "replace"), (["images", 2, "lighting"], "remove")]:
        with pytest.raises(ValueError, match="SCOPE_CHANGED"):
            apply_repair(value, {"patches": [{"path": path, "value": "new", "op": op}], "review_records": value["review_records"]}, issues)


def test_extra_technical_fields_are_removed_in_scope_without_losing_creative_content():
    value, evidence, _ = design_fixture()
    original = deepcopy(value)
    value["images"][0]["references"] = [{"asset_id": "model-must-not-copy-this"}]
    with pytest.raises(DesignValidationError) as raised:
        validate_designs(value, evidence["input_snapshot"], evidence["product_selling_points"])
    updated = apply_repair(value, {"patches": [{"path": ["images", 0, "references"], "op": "remove", "value": None}],
        "review_records": value["review_records"]}, raised.value.issues)
    assert updated == original
    updated["images"][0]["lighting"] = "   "
    with pytest.raises(DesignValidationError, match="FIELD_EMPTY"):
        validate_designs(updated, evidence["input_snapshot"], evidence["product_selling_points"])


async def test_restart_repairs_the_saved_completed_draft_instead_of_replanning():
    original, evidence, refs = design_fixture(5)
    broken = deepcopy(original)
    del broken["images"][1]["text_layout"]
    issues = [{"path": ["images", 1, "text_layout"], "code": "missing"}]
    evidence["stage_drafts"] = {"3": {"output": broken, "issues": issues, "validation_error": "missing", "delivery_version": "ecom-design.v3"}}
    patch = {"patches": [{"path": issues[0]["path"], "value": original["images"][1]["text_layout"]}], "review_records": original["review_records"]}
    service = planner()
    service._call = AsyncMock(return_value=(json.dumps(patch), {}))
    final, _ = await service._stage_images({"id": str(uuid4())}, "lease", "rules", wrapper(3), evidence,
        evidence["input_snapshot"]["messages"], refs, ["url"], validator=lambda v: assemble_designs(v, evidence["input_snapshot"], evidence["product_selling_points"]))
    assert [i["design"] for i in final["images"]] == original["images"]
    service._call.assert_awaited_once()


def test_latest_source_and_runtime_versions_are_separate_and_do_not_default_counts():
    for mode in ("main_images", "detail_page"):
        bodies, schema = resources(mode)
        assert schema["properties"]["schema_version"]["const"] == "product-selling-points.v3"
        assert "新任务仅指定详情，默认 10" not in bodies[2]
        assert "新建任务未指定类型时，整体默认7" not in bodies[2]
        assert "光源、覆盖范围与必要遮挡" in bodies[2]
    source = Path(__file__).parents[1] / "config/ecommerce_planner_prompts/source-20261009/manifest.json"
    assert source.is_file()
    assert FixedSettings.model_validate({"task_type": "detail_page", "image_count": 7, "platform": "淘宝", "language": "中文", "aspect_ratio": "3:4", "resolution": "1K"}).image_count == 7


@pytest.mark.parametrize("task_type", ["main_images", "detail_page"])
@pytest.mark.parametrize("negative", ["", "不要重复绘制商品。"])
def test_clean_execution_keeps_model_design_without_internal_ids_or_extra_rules(task_type, negative):
    from services.agent.image.ecommerce_planner.assembly import CLEAN_ASSEMBLY_VERSION, NEGATIVE
    from services.task_execution.legacy_sources import native_plan_document
    value, evidence, refs = design_fixture(3, task_type, 3)
    for design in value["images"]:
        design["negative_additions"] = negative
    before = deepcopy(value)
    saved = assemble_designs(value, evidence["input_snapshot"], evidence["product_selling_points"],
        evidence["visual_direction"], assembly_version=CLEAN_ASSEMBLY_VERSION)
    assert value == before
    for design, item in zip(value["images"], saved["images"]):
        prompt = item["request_text"]
        assert item["assembly_version"] == CLEAN_ASSEMBLY_VERSION
        assert item["references"] == refs and item["design"] == design
        for field in ("scene", "layout", "props_and_decoration", "lighting", "text_layout",
                      "product_preservation", "execution_constraints"):
            assert design[field] in prompt
        assert item["negative_prompt"] == negative
        assert FIDELITY not in prompt and NEGATIVE not in prompt
        assert "整套共同边界" not in prompt and "第二阶段原文" not in prompt
        assert all(ref["source_id"] not in prompt and ref["role"] not in prompt for ref in refs)
        for number, use in enumerate(design["reference_usage"], 1):
            assert f"输入图片{use['number']}—{use['usage']}" in prompt
        assert item["request_text_sha256"] == text_hash(prompt)
        assert prompt == item["positive_prompt"] + "\n\n负面提示词：" + negative
        assert prompt == item["scheme_markdown"].split("## 完整生图提示词\n\n")[1].replace("\n\n## 负面提示词\n\n", "\n\n负面提示词：")
    # The existing saved-plan adapter accepts an absent *extra* negative rule
    # without manufacturing one, while preserving exact text and item IDs.
    document = json.loads(native_plan_document({"id": str(uuid4()), "plan_revision": 1,
        "status": "ready", "target_size": evidence["input_snapshot"]["target_size"],
        "stage_outputs": {"1": evidence["product_selling_points"], "2": evidence["visual_direction"]},
        "items": saved["images"]}))
    assert [image["negative_prompt"] for image in document["image_plans"]] == [negative] * 3
    assert [image["prompt"] for image in document["image_plans"]] == [image["positive_prompt"] for image in saved["images"]]


def test_legacy_assembly_stays_pinned_and_clean_wrapper_assigns_design_to_model():
    from services.agent.image.ecommerce_planner.assembly import ASSEMBLY_VERSION, CLEAN_ASSEMBLY_VERSION
    value, evidence, _ = design_fixture()
    args = (value, evidence["input_snapshot"], evidence["product_selling_points"], evidence["visual_direction"])
    implicit = assemble_designs(*args)
    explicit = assemble_designs(*args, assembly_version=ASSEMBLY_VERSION)
    assert implicit["images"][0]["request_text"] == explicit["images"][0]["request_text"]
    assert implicit["images"][0]["request_text_sha256"] == explicit["images"][0]["request_text_sha256"]
    legacy = wrapper(3)
    clean = wrapper(3, execution_only=True)
    assert "通用负面词" in legacy and "程序只绑定输入图片编号与固定参数" in clean
    assert "通用负面词" not in clean and "适用于本张" in clean
    assert wrapper(1) == wrapper(1, execution_only=True)
    assert wrapper(2) == wrapper(2, execution_only=True)
    with pytest.raises(ValueError, match="ASSEMBLY_VERSION_UNSUPPORTED"):
        assemble_designs(*args, assembly_version="future-unknown")


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [None, 'fixed-frame.v3', 'ecom-fixed-frame.v3', 'image-prompt.v1'])
async def test_resumed_stage_three_uses_pinned_assembly_without_rewriting_prior_stages(version):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from services.agent.image.ecommerce_planner.assembly import ASSEMBLY_VERSION, CLEAN_ASSEMBLY_VERSION
    from services.agent.agent_result import AgentResult
    value, evidence, refs = design_fixture()
    service = planner()
    query = Mock()
    query.execute.return_value = SimpleNamespace(data={'claimed': True, 'current_stage': 3,
        'stage_outputs': {'1': evidence['product_selling_points'], '2': evidence['visual_direction']}})
    service.scope = SimpleNamespace(rpc=lambda *a: query)
    service._stage = AsyncMock(side_effect=AssertionError('Completed stages must not be billed again'))
    captured = []
    async def stage(row, lease, original, integration, *args, validator):
        captured.append(integration)
        return validator(value), {}
    service._stage_images = stage
    service._save = AsyncMock()
    service._result = AsyncMock(return_value=AgentResult('saved'))
    row = {'id': str(uuid4()), 'plan_revision': 1, 'input_snapshot': evidence['input_snapshot'],
        'prompt_versions': {} if version is None else {'assembly_version': version}}
    bodies, schema = resources()
    result = await service.execute(row, refs, ['https://example.invalid/source'],
        evidence['input_snapshot']['messages'], schema, bodies)
    assert result is service._result.return_value
    service._stage.assert_not_awaited()
    final = service._save.await_args.args[3]
    assert final['assembly_version'] == (ASSEMBLY_VERSION if version == 'ecom-fixed-frame.v3' else version or ASSEMBLY_VERSION)
    assert (FIDELITY in final['images'][0]['request_text']) == (version != CLEAN_ASSEMBLY_VERSION)
    assert ('程序只绑定输入图片编号与固定参数' in captured[0]) == (version == CLEAN_ASSEMBLY_VERSION)


@pytest.mark.parametrize('with_legacy_null', [False, True])
def test_remove_patch_needs_no_replacement_value_and_keeps_every_valid_design_field(with_legacy_null):
    original, evidence, _ = design_fixture(3)
    value = deepcopy(original)
    value['images'][2]['reference_usage'][0]['note'] = '仅解释已存在的布局策略，不是新字段。'
    with pytest.raises(DesignValidationError) as raised:
        validate_designs(value,evidence['input_snapshot'],evidence['product_selling_points'])
    patch={'path':['images',2,'reference_usage',0,'note'],'op':'remove'}
    if with_legacy_null:patch['value']=None
    updated=apply_repair(value,{'patches':[patch],'review_records':original['review_records']},raised.value.issues)
    assert updated==original
    assert validate_designs(updated,evidence['input_snapshot'],evidence['product_selling_points'])==original
    assert value['images'][2]['reference_usage'][0]['note']  # No mutation of the evidence draft.


def test_replace_patch_still_requires_value_but_accepts_explicit_null():
    from pydantic import ValidationError
    from services.agent.image.ecommerce_planner.designs import FieldPatch
    with pytest.raises(ValidationError,match='REPLACEMENT_VALUE_REQUIRED'):
        FieldPatch.model_validate({'path':['page_plan'],'op':'replace'})
    assert FieldPatch.model_validate({'path':['page_plan'],'op':'replace','value':None}).value is None


async def test_real_remove_shape_finishes_the_original_stage_without_replanning():
    original,evidence,refs=design_fixture(3)
    broken=deepcopy(original)
    broken['images'][2]['reference_usage'][0]['note']='仅解释原来的布局。'
    repair={'patches':[{'path':['images',2,'reference_usage',0,'note'],'op':'remove'}],
            'review_records':original['review_records']}
    service=planner()
    service._call=AsyncMock(side_effect=[(json.dumps(broken),{}),(json.dumps(repair),{})])
    final,_=await service._stage_images({'id':str(uuid4())},'lease','rules',wrapper(3),evidence,
        evidence['input_snapshot']['messages'],refs,['url'],
        validator=lambda v:assemble_designs(v,evidence['input_snapshot'],evidence['product_selling_points']))
    assert [item['design'] for item in final['images']]==original['images']
    assert service._call.await_count==2
    assert service._save_attempt.await_count==1


@pytest.mark.parametrize('patch,valid',[
    ({'path':['images',0,'note'],'op':'remove'},True),
    ({'path':['images',0,'note'],'op':'remove','value':None},True),
    ({'path':['page_plan'],'op':'replace'},False),
    ({'path':['page_plan'],'op':'replace','value':None},True),
    ({'path':['page_plan'],'value':None},True),
])
def test_patch_advertised_schema_and_local_validation_agree(patch,valid):
    from jsonschema import Draft202012Validator
    from pydantic import ValidationError
    from services.agent.image.ecommerce_planner.designs import FieldPatch
    assert Draft202012Validator(FieldPatch.model_json_schema()).is_valid(patch) is valid
    if valid:FieldPatch.model_validate(patch)
    else:
        with pytest.raises(ValidationError):FieldPatch.model_validate(patch)


def test_incomplete_group_reports_count_and_schema_together_and_reviews_have_one_output_channel():
    from services.agent.image.ecommerce_planner.designs import repair_context
    original,evidence,_=design_fixture(3)
    broken=deepcopy(original)
    broken['images']=broken['images'][:1]
    del broken['images'][0]['execution_constraints']
    del broken['review_records']
    with pytest.raises(DesignValidationError) as raised:
        validate_designs(broken,evidence['input_snapshot'],evidence['product_selling_points'])
    assert {tuple(i['path']) for i in raised.value.issues}=={('images',),('review_records',)}
    targets=repair_context(broken,raised.value.issues)
    assert [t['path'] for t in targets]==[['images']]
    # Review records already have their own required top-level field in DesignRepair.
    fixed=apply_repair(broken,{'patches':[{'path':['images'],'value':original['images']}],
        'review_records':original['review_records']},raised.value.issues)
    assert validate_designs(fixed,evidence['input_snapshot'],evidence['product_selling_points'])==original
