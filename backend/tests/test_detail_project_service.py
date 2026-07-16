"""主图详情页 Service 单元测试。"""

from unittest.mock import MagicMock, patch

import pytest

from core.exceptions import AppException
from services.detail_project_service import DetailProjectService


def _service(tmp_path):
    db = MagicMock()
    with patch("services.detail_project_service.get_settings") as settings:
        settings.return_value.file_workspace_root = str(tmp_path)
        return DetailProjectService(db, "00000000-0000-0000-0000-000000000001", None)


def _db_cursor(service):
    conn = service.db.pool.connection.return_value.__enter__.return_value
    cursor = conn.cursor.return_value.__enter__.return_value
    return conn, cursor


def test_validate_rejects_missing_image(tmp_path) -> None:
    service = _service(tmp_path)
    with pytest.raises(AppException) as exc:
        service._validate_workspace_image("missing.png")
    assert exc.value.code == "DETAIL_IMAGE_NOT_FOUND"


def test_validate_accepts_png(tmp_path) -> None:
    from PIL import Image

    service = _service(tmp_path)
    target = service.executor.resolve_safe_path("valid.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2)).save(target, "PNG")
    service._validate_workspace_image("valid.png")


def test_serialize_marks_missing(tmp_path) -> None:
    service = _service(tmp_path)
    result = service._serialize_image({"workspace_path": "gone.png"})
    assert result["status"] == "missing"
    assert result["original_url"] is None


def test_get_current_returns_project_with_images(tmp_path) -> None:
    service = _service(tmp_path)
    project_query = MagicMock()
    project_query.select.return_value = project_query
    project_query.eq.return_value = project_query
    project_query.order.return_value = project_query
    project_query.limit.return_value = project_query
    project_query.execute.return_value.data = [{"id": "project-1"}]
    image_query = MagicMock()
    image_query.select.return_value = image_query
    image_query.eq.return_value = image_query
    image_query.order.return_value = image_query
    image_query.execute.return_value.data = [
        {"id": "image-1", "workspace_path": "missing.png"}
    ]
    service.db.table.side_effect = [project_query, image_query]
    project = service.get_current()
    assert project["id"] == "project-1"
    assert project["images"][0]["status"] == "missing"


def test_get_current_does_not_create_empty_draft(tmp_path) -> None:
    service = _service(tmp_path)
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.order.return_value = query
    query.limit.return_value = query
    query.execute.return_value.data = []
    service.db.table.return_value = query
    assert service.get_current() is None


def test_get_ai_input_project_requires_product_image(tmp_path) -> None:
    service = _service(tmp_path)
    project = {
        "id": "project-1",
        "images": [{"category": "reference", "status": "ready", "original_url": "https://cdn/ref.png"}],
    }
    with patch.object(service, "_require_project", return_value=project):
        with pytest.raises(AppException) as exc:
            service.get_ai_input_project("project-1")
    assert exc.value.code == "DETAIL_PRODUCT_IMAGE_REQUIRED"


def test_get_ai_input_project_rejects_missing_image(tmp_path) -> None:
    service = _service(tmp_path)
    project = {
        "id": "project-1",
        "images": [{"category": "product", "status": "missing", "original_url": None}],
    }
    with patch.object(service, "_require_project", return_value=project):
        with pytest.raises(AppException) as exc:
            service.get_ai_input_project("project-1")
    assert exc.value.code == "DETAIL_IMAGE_NOT_READY"


def test_get_ai_input_project_returns_ready_images(tmp_path) -> None:
    service = _service(tmp_path)
    project = {
        "id": "project-1",
        "images": [{"category": "product", "status": "ready", "original_url": "https://cdn/p.png"}],
    }
    with patch.object(service, "_require_project", return_value=project):
        assert service.get_ai_input_project("project-1") is project


def test_attach_calls_atomic_function_and_returns_current(tmp_path) -> None:
    service = _service(tmp_path)
    target = service.executor.resolve_safe_path("valid.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.new("RGB", (2, 2)).save(target, "PNG")
    conn = service.db.pool.connection.return_value.__enter__.return_value
    cursor = conn.cursor.return_value.__enter__.return_value
    with patch.object(service, "get_current", return_value={"id": "project-1"}):
        result = service.attach_image("valid.png", "product")
    assert result == {"id": "project-1"}
    assert "attach_detail_project_image" in cursor.execute.call_args.args[0]
    conn.commit.assert_called_once()


def test_attach_maps_duplicate_error(tmp_path) -> None:
    service = _service(tmp_path)
    with patch.object(service, "_validate_workspace_image"):
        service.db.pool.connection.side_effect = RuntimeError("DETAIL_IMAGE_DUPLICATE")
        with pytest.raises(AppException) as exc:
            service.attach_image("valid.png", "product")
    assert exc.value.code == "DETAIL_IMAGE_DUPLICATE"


def test_reorder_rejects_duplicate_ids(tmp_path) -> None:
    service = _service(tmp_path)
    with pytest.raises(AppException) as exc:
        service.reorder_images("project-1", 1, ["image-1", "image-1"])
    assert exc.value.code == "DETAIL_IMAGE_ORDER_INVALID"


def test_update_settings_without_changes_returns_project(tmp_path) -> None:
    service = _service(tmp_path)
    with patch.object(service, "_require_project", return_value={"id": "project-1"}):
        result = service.update_settings("project-1", 1, {})
    assert result == {"id": "project-1"}


def test_update_settings_executes_versioned_update(tmp_path) -> None:
    service = _service(tmp_path)
    conn, cursor = _db_cursor(service)
    cursor.fetchone.return_value = {"id": "project-1"}
    with patch.object(service, "get_current", return_value={"id": "project-1", "version": 2}):
        result = service.update_settings("project-1", 1, {"quality": "2k"})
    assert result["version"] == 2
    assert "version = version + 1" in cursor.execute.call_args.args[0]
    conn.commit.assert_called_once()


def test_update_settings_detects_version_conflict(tmp_path) -> None:
    service = _service(tmp_path)
    _, cursor = _db_cursor(service)
    cursor.fetchone.return_value = None
    with pytest.raises(AppException) as exc:
        service.update_settings("project-1", 1, {"quality": "2k"})
    assert exc.value.code == "DETAIL_PROJECT_VERSION_CONFLICT"


def test_remove_image_compacts_order_and_bumps_version(tmp_path) -> None:
    service = _service(tmp_path)
    conn, cursor = _db_cursor(service)
    cursor.fetchone.side_effect = [{"id": "project-1"}, {"sort_order": 1}]
    with patch.object(service, "get_current", return_value={"id": "project-1"}):
        service.remove_image("project-1", "image-1", 1)
    sql_calls = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("sort_order=sort_order-1" in sql for sql in sql_calls)
    assert any("version=version+1" in sql for sql in sql_calls)
    conn.commit.assert_called_once()


def test_update_category_bumps_version(tmp_path) -> None:
    service = _service(tmp_path)
    _, cursor = _db_cursor(service)
    cursor.fetchone.side_effect = [{"id": "project-1"}, {"id": "image-1"}]
    with patch.object(service, "get_current", return_value={"id": "project-1"}):
        service.update_category("project-1", "image-1", 1, "reference")
    assert any("SET category=" in call.args[0] for call in cursor.execute.call_args_list)


def test_reorder_reinserts_images_in_requested_order(tmp_path) -> None:
    service = _service(tmp_path)
    _, cursor = _db_cursor(service)
    cursor.fetchone.return_value = {"id": "project-1"}
    cursor.fetchall.side_effect = [
        [{"id": "a"}, {"id": "b"}],
        [
            {"id": "a", "workspace_path": "a.png", "category": "product", "created_at": "now"},
            {"id": "b", "workspace_path": "b.png", "category": "reference", "created_at": "now"},
        ],
    ]
    with patch.object(service, "get_current", return_value={"id": "project-1"}):
        service.reorder_images("project-1", 1, ["b", "a"])
    inserts = [call for call in cursor.execute.call_args_list if "INSERT INTO detail_project_images" in call.args[0]]
    assert len(inserts) == 2
    assert inserts[0].args[1][0] == "b"
