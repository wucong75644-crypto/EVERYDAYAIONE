"""任务资源清单与文件工具隔离测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.agent.file_analysis_service import _validate_resource_scope
from services.agent.file_tool_mixin import FileToolMixin
from services.handlers.resource_manifest import (
    ResourceAsset,
    ResourceManifest,
    build_resource_manifest,
)
from schemas.message import FilePart


def _query(data):
    query = MagicMock()
    for method in ("select", "eq", "in_"):
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(data=data)
    return query


def _manifest() -> ResourceManifest:
    return ResourceManifest(
        task_id="task-1",
        input_message_id="input-1",
        source="task_attachment_refs",
        assets=(
            ResourceAsset(
                asset_id="asset-1",
                attachment_set_id="set-1",
                name="本次销售.csv",
                workspace_path="上传/企微/本次销售.csv",
                mime_type="text/csv",
                size=20,
                url="https://cdn/current.csv",
            ),
        ),
    )


def test_manifest_uses_frozen_task_attachment_refs() -> None:
    refs = _query([{
        "attachment_id": "asset-1",
        "attachment_set_id": "set-1",
        "turn_id": "turn-1",
        "input_message_id": "input-1",
    }])
    assets = _query([{
        "id": "asset-1",
        "conversation_id": "conv-1",
        "org_id": "org-1",
        "canonical_name": "本次销售.csv",
        "workspace_path": "上传/企微/本次销售.csv",
        "detected_mime_type": "text/csv",
        "size": 20,
        "url": "https://cdn/current.csv",
        "status": "ready",
    }])
    db = MagicMock()
    db.table.side_effect = [refs, assets]

    manifest = build_resource_manifest(
        db,
        task_id="task-1",
        input_message_id="input-1",
        conversation_id="conv-1",
        turn_id="turn-1",
        org_id="org-1",
        input_content=[],
    )

    assert manifest.source == "task_attachment_refs"
    assert manifest.allowed_paths == {"上传/企微/本次销售.csv"}


def test_web_input_message_is_immutable_fallback() -> None:
    db = MagicMock()
    db.table.return_value = _query([])

    manifest = build_resource_manifest(
        db,
        task_id="task-web",
        input_message_id="input-web",
        conversation_id="conv-web",
        turn_id="turn-web",
        org_id=None,
        input_content=[{
            "type": "file",
            "url": "https://cdn/web.csv",
            "workspace_path": "上传/web.csv",
            "name": "web.csv",
            "mime_type": "text/csv",
        }],
    )

    assert manifest.source == "input_message"
    assert manifest.assets[0].asset_id == "input-web:0"


def test_filepart_reexport_preserves_asset_id() -> None:
    part = FilePart(
        url="https://cdn/file.csv",
        name="file.csv",
        mime_type="text/csv",
        asset_id="asset-1",
    )

    assert part.model_dump(exclude_none=True)["asset_id"] == "asset-1"


@pytest.mark.asyncio
async def test_file_search_defaults_to_current_manifest() -> None:
    owner = MagicMock(spec=FileToolMixin)
    owner.resource_manifest = _manifest()
    owner.conversation_id = "conv-1"
    owner.org_id = "org-1"
    owner._search_manifest = FileToolMixin._search_manifest.__get__(owner)
    executor = MagicMock()
    executor._format_size.return_value = "20 B"
    executor.resolve_safe_path.side_effect = FileNotFoundError

    result = await FileToolMixin._file_search(
        owner, executor, {}, MagicMock(),
    )

    assert result.status == "success"
    assert "本次销售.csv" in result.summary
    executor.file_list_entries.assert_not_called()


def test_file_analyze_rejects_historical_workspace_path(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    old_file = workspace / "旧数据.csv"
    old_file.parent.mkdir()
    old_file.write_text("a,b\n1,2")
    owner = SimpleNamespace(resource_manifest=_manifest())
    executor = SimpleNamespace(workspace_root=str(workspace))

    result = _validate_resource_scope(
        owner, executor, {}, str(old_file), "旧数据.csv",
    )

    assert result is not None
    assert result.error_message == "RESOURCE_PATH_NOT_IN_MANIFEST"


def test_workspace_scope_requires_explicit_argument(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    old_file = workspace / "旧数据.csv"
    old_file.parent.mkdir()
    old_file.write_text("a,b\n1,2")
    owner = SimpleNamespace(resource_manifest=_manifest())
    executor = SimpleNamespace(workspace_root=str(workspace))

    result = _validate_resource_scope(
        owner,
        executor,
        {"scope": "workspace"},
        str(old_file),
        "旧数据.csv",
    )

    assert result is None
