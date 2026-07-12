"""主图详情页路由契约测试。"""

from unittest.mock import MagicMock, patch

from api.routes.detail_project import (
    attach_detail_project_image,
    get_current_detail_project,
    get_detail_project_service,
    reorder_detail_project_images,
    update_detail_project,
)
from schemas.detail_project import (
    DetailImageAttachRequest, DetailImageOrderRequest, DetailProjectSettingsPatch,
)


def test_current_returns_empty_project() -> None:
    service = MagicMock()
    service.get_current.return_value = None
    response = get_current_detail_project(service)
    assert response.success is True
    assert response.data == {"project": None}


def test_attach_returns_latest_project() -> None:
    service = MagicMock()
    service.attach_image.return_value = {"id": "project-1"}
    body = DetailImageAttachRequest(workspace_path="upload/a.png", category="product")
    response = attach_detail_project_image(body, service)
    assert response.data == {"project": {"id": "project-1"}}


def test_service_factory_uses_org_context() -> None:
    ctx = MagicMock(user_id="user-1", org_id="org-1")
    db = MagicMock()
    with patch("api.routes.detail_project.DetailProjectService") as service_cls:
        get_detail_project_service(ctx, db)
    service_cls.assert_called_once_with(db, "user-1", "org-1")


def test_update_settings_passes_version_and_patch() -> None:
    service = MagicMock()
    service.update_settings.return_value = {"id": "project-1", "version": 3}
    body = DetailProjectSettingsPatch(version=2, quality="2k")
    response = update_detail_project("project-1", body, service)
    assert response.data["project"]["version"] == 3
    service.update_settings.assert_called_once_with("project-1", 2, {"quality": "2k"})


def test_reorder_passes_complete_order() -> None:
    service = MagicMock()
    service.reorder_images.return_value = {"id": "project-1"}
    body = DetailImageOrderRequest(version=2, image_ids=["a", "b"])
    reorder_detail_project_images("project-1", body, service)
    service.reorder_images.assert_called_once_with("project-1", 2, ["a", "b"])
