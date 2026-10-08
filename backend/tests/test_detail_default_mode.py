import pytest
from pydantic import ValidationError
from schemas.ecom_requirement import RequirementSettings
from schemas.detail_project import DetailProjectSettingsPatch


def test_default_mode_accepts_14_images():
    assert RequirementSettings(content_type="default", image_count=14).image_count == 14
    assert DetailProjectSettingsPatch(version=1, content_type="default", image_count=14).image_count == 14


@pytest.mark.parametrize("content_type,count", [("default", 7), ("main_image", 14), ("detail_page", 14)])
def test_generation_mode_rejects_invalid_count(content_type, count):
    with pytest.raises(ValidationError):
        RequirementSettings(content_type=content_type, image_count=count)
