"""单份草稿和页面默认14张/单类型15张契约。"""
import pytest
from pydantic import ValidationError
from schemas.ecom_requirement import RequirementAssistInput, RequirementAssistResult, RequirementImage, RequirementSettings
from schemas.detail_project import DetailProjectSettingsPatch

def _image(name):
    return RequirementImage(id=name,original_url=f"https://cdn/{name}.png",display_name=name)

def _input(**changes):
    fields=dict(user_id="u",org_id=None,source_type="detail_project",source_id="p",
        product_images=[_image("product")],reference_images=[_image(f"r{i}") for i in range(8)],
        content_type="default",platform="taobao",language="zh-CN",aspect_ratio="1:1",quality="1k",
        image_count=14,user_requirement="",project_version=1)
    fields.update(changes)
    return RequirementAssistInput(**fields)

def test_nine_input_images_are_independent_from_fourteen_output_images():
    data=_input()
    assert data.image_count==14
    assert len(data.product_images)+len(data.reference_images)==9

def test_rejects_more_than_nine_input_images():
    with pytest.raises(ValidationError,match="合计不能超过9张"):
        _input(product_images=[_image("p1"),_image("p2")])

@pytest.mark.parametrize("content,count",[("default",14),("main_image",15),("detail_page",15)])
def test_page_modes_and_counts_supported(content,count):
    assert RequirementSettings(content_type=content,image_count=count).image_count==count
    assert _input(content_type=content,image_count=count).image_count==count

def test_single_draft_accepts_inference_and_no_mandatory_questions():
    data=RequirementAssistResult(product_description="产品信息",selling_points=[
        {"feature":"红金配色","benefit":"送礼有仪式感","benefit_basis":"inferred"}])
    assert data.selling_points[0].benefit_basis=="inferred"
    assert data.supplement_questions==[]

def test_old_three_scheme_output_is_rejected():
    with pytest.raises(ValidationError):
        RequirementAssistResult(product_description="产品",suggestions=[{}, {}, {}])

def test_more_than_three_or_unskippable_questions_rejected():
    question={"question":"尺寸？","why":"补充规格","can_skip":True}
    with pytest.raises(ValidationError):
        RequirementAssistResult(product_description="产品",supplement_questions=[question]*4)
    with pytest.raises(ValidationError):
        RequirementAssistResult(product_description="产品",supplement_questions=[{**question,"can_skip":False}])

def test_original_and_adopted_draft_share_persistence_length_bound():
    raw="原文"*1200
    assert RequirementSettings(requirement=raw).requirement==raw
    assert DetailProjectSettingsPatch(version=1,requirement=raw).requirement==raw
    with pytest.raises(ValidationError):
        RequirementSettings(requirement="字"*10001)
