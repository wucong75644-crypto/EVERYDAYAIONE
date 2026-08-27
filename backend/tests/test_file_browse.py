from pathlib import Path
from types import SimpleNamespace

import pytest

from api.routes.file_browse import list_workspace
from core.exceptions import AppException


class _Executor:
    def __init__(self, target):
        self.target = target

    def resolve_safe_path(self, path: str):
        return self.target

    def get_cdn_url(self, path: str) -> None:
        return None


def _patch_workspace(monkeypatch, target) -> None:
    settings = SimpleNamespace(
        file_workspace_enabled=True,
        file_workspace_root=str(getattr(target, "parent", "/tmp")),
    )
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        "services.file_executor.FileExecutor",
        lambda **kwargs: _Executor(target),
    )


@pytest.mark.asyncio
async def test_list_workspace_returns_empty_response_for_empty_directory(
    monkeypatch,
    tmp_path,
):
    target = tmp_path / "empty"
    target.mkdir()
    _patch_workspace(monkeypatch, target)

    response = await list_workspace(
        ctx=SimpleNamespace(user_id="user-1", org_id=None),
        db=SimpleNamespace(),
        path="empty",
    )

    assert response.path == "empty"
    assert response.items == []
    assert response.total == 0


@pytest.mark.asyncio
async def test_list_workspace_returns_404_for_missing_directory(
    monkeypatch,
    tmp_path,
):
    target = tmp_path / "missing"
    _patch_workspace(monkeypatch, target)

    with pytest.raises(AppException) as exc_info:
        await list_workspace(
            ctx=SimpleNamespace(user_id="user-1", org_id=None),
            db=SimpleNamespace(),
            path="missing",
        )

    assert exc_info.value.code == "WORKSPACE_DIRECTORY_NOT_FOUND"
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_workspace_returns_503_for_storage_error(
    monkeypatch,
    tmp_path,
):
    class _UnavailableDirectory:
        def stat(self):
            raise OSError("storage unavailable")

    _patch_workspace(monkeypatch, _UnavailableDirectory())

    with pytest.raises(AppException) as exc_info:
        await list_workspace(
            ctx=SimpleNamespace(user_id="user-1", org_id="org-1"),
            db=SimpleNamespace(),
            path="reports",
        )

    assert exc_info.value.code == "WORKSPACE_DIRECTORY_UNAVAILABLE"
    assert exc_info.value.status_code == 503
