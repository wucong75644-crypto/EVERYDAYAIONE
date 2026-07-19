"""跨轮持久 Artifact 读取测试。"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.agent.runtime.artifacts.repository import (
    PersistentArtifactRepository,
)


def _repository(row):
    query = MagicMock()
    for method in (
        "select", "eq", "lte", "order", "range", "maybe_single",
    ):
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(data=row)
    db = MagicMock()
    db.table.return_value = query
    repository = PersistentArtifactRepository(
        db,
        conversation_id="conv-1",
        base_revision=9,
        org_id="org-1",
    )
    return repository, db, query


@pytest.mark.asyncio
async def test_reads_inline_content_with_scoped_query_and_cursor():
    repository, _, query = _repository({
        "id": "artifact-1",
        "storage_kind": "inline",
        "inline_content": {"rows": [{"platform": "淘宝"}]},
    })

    page = await repository.read(
        "artifact-1", cursor=0, max_tokens=256,
    )

    assert page is not None
    assert page.artifact_id == "artifact-1"
    assert "淘宝" in page.content
    query.eq.assert_any_call("conversation_id", "conv-1")
    query.eq.assert_any_call("org_id", "org-1")
    query.eq.assert_any_call("status", "ready")
    query.eq.assert_any_call("id", "artifact-1")
    query.lte.assert_called_once_with("context_revision", 9)


@pytest.mark.asyncio
async def test_reads_oss_json_content():
    repository, _, _ = _repository({
        "id": "artifact-2",
        "storage_kind": "oss",
        "storage_ref": {"object_key": "artifacts/task/artifact-2.json"},
    })
    body = MagicMock()
    body.read.return_value = json.dumps(
        {"detail": "完整分析结果"}, ensure_ascii=False,
    ).encode("utf-8")
    oss = MagicMock()
    oss.bucket.get_object.return_value = body

    with patch(
        "services.oss_service.get_oss_service",
        return_value=oss,
    ):
        page = await repository.read(
            "artifact-2", cursor=0, max_tokens=256,
        )

    assert page is not None
    assert "完整分析结果" in page.content
    oss.bucket.get_object.assert_called_once_with(
        "artifacts/task/artifact-2.json",
    )


@pytest.mark.asyncio
async def test_reads_backfilled_message_slice():
    repository, db, artifact_query = _repository({
        "id": "artifact-3",
        "storage_kind": "message_slice",
        "storage_ref": {"message_id": "message-1", "block_index": 0},
    })
    message_query = MagicMock()
    for method in ("select", "eq", "maybe_single"):
        getattr(message_query, method).return_value = message_query
    message_query.execute.return_value = SimpleNamespace(data={
        "conversation_id": "conv-1",
        "content": [{"type": "tool_step", "output": {"total": 42}}],
    })
    db.table.side_effect = [artifact_query, message_query]

    page = await repository.read(
        "artifact-3", cursor=0, max_tokens=256,
    )

    assert page is not None
    assert '"total":42' in page.content
    message_query.eq.assert_any_call("conversation_id", "conv-1")


@pytest.mark.asyncio
async def test_invalid_negative_message_slice_is_rejected():
    repository, _, _ = _repository({
        "id": "artifact-4",
        "storage_kind": "message_slice",
        "storage_ref": {"message_id": "message-1", "block_index": -1},
    })

    with pytest.raises(RuntimeError, match="ARTIFACT_MESSAGE_SLICE_INVALID"):
        await repository.read("artifact-4", cursor=0, max_tokens=256)
