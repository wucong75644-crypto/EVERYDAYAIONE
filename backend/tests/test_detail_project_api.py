"""主图详情页路由契约测试。"""

from unittest.mock import MagicMock, patch

from api.routes.detail_project import (
    attach_detail_project_image,
    get_current_detail_project,
    get_detail_project_service,
    reorder_detail_project_images,
    update_detail_project,
    archive_detail_project,
)
from schemas.detail_project import (
    DetailImageAttachRequest, DetailImageOrderRequest, DetailProjectSettingsPatch,
)


def test_current_returns_empty_project() -> None:
    service = MagicMock()
    service.get_current.return_value = None
    service.db.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
    response = get_current_detail_project(service)
    assert response.success is True
    assert response.data == {"project": None}


def test_archive_reuses_owner_scoped_atomic_service():
    from uuid import uuid4
    service=MagicMock(); project=uuid4()
    assert archive_detail_project(project,service).data=={'project':None}
    service.archive.assert_called_once_with(str(project))


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


def test_explicit_task_routes_and_static_status_matching():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes.detail_project import router
    from uuid import uuid4
    app=FastAPI();app.include_router(router)
    service=MagicMock();project=str(uuid4())
    service.create.return_value={'id':project}
    service.attach_image.return_value={'id':project,'images':[]}
    app.dependency_overrides[get_detail_project_service]=lambda:service
    client=TestClient(app)
    with patch('services.detail_project_tasks.DetailProjectTasks') as tasks:
        tasks.return_value.status.return_value=[{'id':project,'completed_count':0}]
        tasks.return_value.list.return_value={'items':[],'next_cursor':None}
        assert client.get('/detail-projects/status',params={'ids':project}).status_code==200
        tasks.return_value.status.assert_called_once_with([project])
        assert client.get('/detail-projects?limit=51').status_code==422
        assert client.get('/detail-projects').json()['data']['next_cursor'] is None
    assert client.post('/detail-projects',json={'request_id':project}).json()['data']['project']['id']==project
    service.create.assert_called_once_with(project)
    assert client.post(f'/detail-projects/{project}/images',json={'workspace_path':'upload/a.png','category':'product'}).status_code==200
    service.attach_image.assert_called_once_with('upload/a.png','product',project)
