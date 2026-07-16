"""详情项目到 AI 帮写标准输入的适配测试。"""

from unittest.mock import MagicMock

from schemas.ecom_requirement import RequirementSettings
from services.agent.image.input_adapters import DetailProjectRequirementAdapter


def test_detail_project_adapter_separates_product_and_reference_images() -> None:
    service = MagicMock()
    service.get_ai_input_project.return_value = {
        "id": "project-1",
        "version": 3,
        "images": [
            {
                "id": "product-1", "workspace_path": "上传/产品.png",
                "original_url": "https://cdn/product.png", "category": "product",
            },
            {
                "id": "reference-1", "workspace_path": "上传/参考.png",
                "original_url": "https://cdn/reference.png", "category": "reference",
            },
        ],
    }
    adapter = DetailProjectRequirementAdapter(service, "user-1", "org-1")

    result = adapter.adapt(
        "project-1",
        RequirementSettings(platform="taobao", image_count=5, requirement="清新自然"),
    )

    assert [image.id for image in result.product_images] == ["product-1"]
    assert [image.id for image in result.reference_images] == ["reference-1"]
    assert result.product_images[0].display_name == "产品.png"
    assert result.user_requirement == "清新自然"
    assert result.project_version == 3
    service.get_ai_input_project.assert_called_once_with("project-1")


def test_detail_project_adapter_preserves_org_and_settings() -> None:
    service = MagicMock()
    service.get_ai_input_project.return_value = {
        "id": "project-1", "version": 1,
        "images": [{
            "id": "product-1", "workspace_path": "product.png",
            "original_url": "https://cdn/product.png", "category": "product",
        }],
    }
    adapter = DetailProjectRequirementAdapter(service, "user-1", None)

    result = adapter.adapt(
        "project-1",
        RequirementSettings(content_type="detail_page", quality="2k", aspect_ratio="3:4"),
    )

    assert result.user_id == "user-1"
    assert result.org_id is None
    assert result.content_type == "detail_page"
    assert result.quality == "2k"
    assert result.aspect_ratio == "3:4"
