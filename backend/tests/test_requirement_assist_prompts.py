"""原图、参考图、人工编辑与用户文字实际进入同一次模型请求。"""
import json
from schemas.ecom_requirement import RequirementAssistInput, RequirementAssistResult, RequirementImage, RequirementRevision
from services.agent.image.requirement_assist_prompts import SYSTEM_PROMPT,build_context_prompt,build_multimodal_messages

def _input():
    return RequirementAssistInput(
        user_id="u",org_id=None,source_type="detail_project",source_id="p",
        product_images=[RequirementImage(id="p1",original_url="https://cdn/product.png",display_name="产品",position=2)],
        reference_images=[RequirementImage(id="r1",original_url="https://cdn/reference.png",display_name="风格",position=1)],
        content_type="default",platform="taobao",language="zh-CN",aspect_ratio="1:1",quality="1k",
        image_count=14,user_requirement="  红金风格\n别加元宝  ",project_version=1)

def test_original_text_is_preserved_and_settings_do_not_need_model_copy():
    data=_input()
    context=json.loads(build_context_prompt(data))
    assert context["用户需求原文"]==data.user_requirement
    assert context["任务设置"]["后续生成数量"]==14
    assert context["图片角色"]==[
        {"位置":1,"角色":"参考图","id":"r1","名称":"风格"},
        {"位置":2,"角色":"产品图","id":"p1","名称":"产品"}]
    assert "允许贴合产品的合理推断" in SYSTEM_PROMPT
    assert "不生成三套方案" in SYSTEM_PROMPT

def test_images_are_actual_multimodal_input_and_keep_global_position():
    messages=build_multimodal_messages(_input())
    assert messages[0]["role"]=="system"
    content=messages[1]["content"]
    assert "参考图 id=r1" in content[1]["text"]
    assert content[2]["image_url"]["url"]=="https://cdn/reference.png"
    assert "产品图 id=p1" in content[3]["text"]
    assert content[4]["image_url"]["url"]=="https://cdn/product.png"

def test_update_passes_manual_draft_latest_supplement_and_skipped_items():
    data=_input()
    data.revision=RequirementRevision(
        draft=RequirementAssistResult(product_description="人工修改：普通印刷"),
        supplement="尺寸20×14×3cm，米白背景",skipped_questions=["封面材质？"])
    context=json.loads(build_context_prompt(data))
    assert context["当前人工编辑草稿"]["product_description"]=="人工修改：普通印刷"
    assert context["用户补充（按时间排序，最后的更正优先）"]=="尺寸20×14×3cm，米白背景"
    assert context["用户已跳过的问题"]==["封面材质？"]
