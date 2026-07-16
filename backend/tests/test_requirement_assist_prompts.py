"""电商图 AI 帮写 Prompt 协议测试。"""

from schemas.ecom_requirement import RequirementAssistInput, RequirementImage
from services.agent.image.requirement_assist_prompts import (
    SYSTEM_PROMPT, build_context_prompt, build_multimodal_messages,
)


def _input() -> RequirementAssistInput:
    return RequirementAssistInput(
        user_id="user-1", org_id="org-1", source_type="detail_project", source_id="project-1",
        product_images=[RequirementImage(id="p1", original_url="https://cdn/p1.png", display_name="产品.png")],
        reference_images=[RequirementImage(id="r1", original_url="https://cdn/r1.png", display_name="参考.png")],
        content_type="main_image", platform="taobao", language="zh-CN", aspect_ratio="1:1",
        quality="1k", image_count=5, user_requirement="突出400页", project_version=2,
    )


def test_system_prompt_contains_fact_and_conflict_boundaries() -> None:
    assert "产品图片中可确认的事实 > 用户明确文字" in SYSTEM_PROMPT
    assert "冲突内容不得作为卖点" in SYSTEM_PROMPT
    assert "禁止复制参考商品" in SYSTEM_PROMPT


def test_context_prompt_preserves_settings_and_image_ids() -> None:
    prompt = build_context_prompt(_input())
    assert "目标平台：taobao" in prompt
    assert "后续生成数量：5" in prompt
    assert "突出400页" in prompt
    assert "id=p1" in prompt
    assert "id=r1" in prompt


def test_multimodal_messages_label_each_image_role() -> None:
    messages = build_multimodal_messages(_input())
    content = messages[1]["content"]
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert content[1]["text"].startswith("产品图 id=p1")
    assert content[2]["image_url"]["url"] == "https://cdn/p1.png"
    assert content[3]["text"].startswith("参考图 id=r1")
    assert content[4]["image_url"]["url"] == "https://cdn/r1.png"
