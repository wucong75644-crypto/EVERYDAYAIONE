"""电商图 AI 帮写 Schema 契约测试。"""

import pytest
from pydantic import ValidationError

from schemas.ecom_requirement import (
    ProductFacts, RequirementAssistInput, RequirementAssistResult, RequirementConflict,
    RequirementImage, RequirementSuggestion,
)


def _image(image_id: str) -> RequirementImage:
    return RequirementImage(id=image_id, original_url=f"https://example.com/{image_id}.png", display_name=image_id)


def _suggestion(suggestion_id: str) -> dict:
    return {
        "id": suggestion_id, "name": suggestion_id,
        "style_name": "清新自然风", "brief_markdown": "产品事实与视觉策略",
    }


def test_assist_input_accepts_nine_images_total() -> None:
    data = RequirementAssistInput(
        user_id="user-1", org_id=None, source_type="detail_project", source_id="project-1",
        product_images=[_image("product")],
        reference_images=[_image(f"reference-{index}") for index in range(8)],
        content_type="main_image", platform="taobao", language="zh-CN",
        aspect_ratio="1:1", quality="1k", image_count=5,
        user_requirement="清新自然", project_version=1,
    )
    assert len(data.product_images) + len(data.reference_images) == 9


def test_assist_input_rejects_more_than_nine_images() -> None:
    with pytest.raises(ValidationError, match="合计不能超过9张"):
        RequirementAssistInput(
            user_id="user-1", org_id=None, source_type="detail_project", source_id="project-1",
            product_images=[_image("product-1"), _image("product-2")],
            reference_images=[_image(f"reference-{index}") for index in range(8)],
            content_type="main_image", platform="taobao", language="zh-CN",
            aspect_ratio="1:1", quality="1k", image_count=5,
            user_requirement="", project_version=1,
        )


def test_result_requires_three_fixed_suggestion_ids() -> None:
    with pytest.raises(ValidationError, match="必须返回"):
        RequirementAssistResult(
            product_facts=ProductFacts(product_name="笔记本"),
            suggestions=[_suggestion("selling_point"), _suggestion("scene"), _suggestion("scene")],
        )


def test_conflict_requires_blocked_claims() -> None:
    with pytest.raises(ValidationError):
        RequirementConflict(
            field="页数", user_value="400页", confirmed_value="200页",
            message="待用户确认", blocked_claims=[],
        )


def test_result_accepts_valid_three_suggestions() -> None:
    result = RequirementAssistResult(
        product_facts=ProductFacts(product_name="笔记本", confirmed_attributes=["200页"]),
        suggestions=[
            RequirementSuggestion(**_suggestion("selling_point")),
            RequirementSuggestion(**_suggestion("scene")),
            RequirementSuggestion(**_suggestion("creative")),
        ],
    )
    assert [item.id for item in result.suggestions] == ["selling_point", "scene", "creative"]
