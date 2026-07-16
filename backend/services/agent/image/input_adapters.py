"""不同业务入口到 AI 帮写标准输入的适配器。"""

from pathlib import Path

from schemas.ecom_requirement import (
    RequirementAssistInput, RequirementImage, RequirementSettings,
)
from services.detail_project_service import DetailProjectService


class DetailProjectRequirementAdapter:
    """将详情项目草稿转换为共享 AI 帮写输入。"""

    def __init__(
        self,
        service: DetailProjectService,
        user_id: str,
        org_id: str | None,
    ) -> None:
        self.service = service
        self.user_id = user_id
        self.org_id = org_id

    def adapt(
        self,
        project_id: str,
        settings: RequirementSettings,
    ) -> RequirementAssistInput:
        project = self.service.get_ai_input_project(project_id)
        product_images: list[RequirementImage] = []
        reference_images: list[RequirementImage] = []
        for image in project["images"]:
            adapted = RequirementImage(
                id=str(image["id"]),
                original_url=image["original_url"],
                display_name=Path(image["workspace_path"]).name,
            )
            target = product_images if image["category"] == "product" else reference_images
            target.append(adapted)
        return RequirementAssistInput(
            user_id=self.user_id,
            org_id=self.org_id,
            source_type="detail_project",
            source_id=project_id,
            product_images=product_images,
            reference_images=reference_images,
            content_type=settings.content_type,
            platform=settings.platform,
            language=settings.language,
            aspect_ratio=settings.aspect_ratio,
            quality=settings.quality,
            image_count=settings.image_count,
            user_requirement=settings.requirement,
            project_version=project["version"],
        )
