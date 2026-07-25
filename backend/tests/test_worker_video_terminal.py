"""视频 Worker 终态能力合同。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import VideoPart
from services.handlers.video_handler import VideoHandler
from services.worker_media_tasks import WorkerMediaTasks


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/172_worker_video_terminal.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/172_worker_video_terminal_rollback.sql"
).read_text()


def test_migration_is_worker_only_atomic_terminal() -> None:
    assert "CREATE FUNCTION worker_commit_video_terminal" in SQL
    assert "session_user <> 'everydayai_worker'" in SQL
    assert "type = 'video'" in SQL
    assert "version <> p_expected_version" in SQL
    assert "atomic_refund_credits" in SQL
    assert "close_generation_turn" in SQL
    assert "GRANT EXECUTE ON FUNCTION worker_commit_video_terminal" in SQL
    assert "GRANT SELECT ON TABLE" not in SQL
    assert "DROP FUNCTION worker_commit_video_terminal" in ROLLBACK


def test_repository_maps_terminal_snapshot() -> None:
    db = MagicMock()
    db.rpc.return_value.execute.return_value = SimpleNamespace(
        data={
            "outcome": "committed",
            "task": {"id": "task-1"},
            "message": {"id": "message-1"},
        },
    )
    repository = WorkerMediaTasks(db)

    result = repository.commit_video_terminal(
        "external-1",
        4,
        "completed",
        [{"type": "video", "url": "https://asset"}],
    )

    assert result and result["message"]["id"] == "message-1"
    db.rpc.assert_called_once_with(
        "worker_commit_video_terminal",
        {
            "p_external_task_id": "external-1",
            "p_expected_version": 4,
            "p_status": "completed",
            "p_content": [{"type": "video", "url": "https://asset"}],
            "p_error_code": None,
            "p_error_message": None,
        },
    )


def test_repository_rejects_version_conflict() -> None:
    db = MagicMock()
    db.rpc.return_value.execute.return_value = SimpleNamespace(
        data={"outcome": "version_conflict"},
    )

    assert WorkerMediaTasks(db).commit_video_terminal(
        "external-1", 4, "failed", [], "TIMEOUT", "任务超时"
    ) is None


def _task() -> dict:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "external_task_id": "external-1",
        "version": 4,
        "conversation_id": "22222222-2222-2222-2222-222222222222",
        "placeholder_message_id": "33333333-3333-3333-3333-333333333333",
        "client_task_id": "client-1",
        "user_id": "44444444-4444-4444-4444-444444444444",
        "org_id": None,
        "model_id": "video-model",
        "credits_locked": 12,
        "request_params": {},
    }


def _message(status: str, content: list[dict]) -> dict:
    return {
        "id": "33333333-3333-3333-3333-333333333333",
        "conversation_id": "22222222-2222-2222-2222-222222222222",
        "role": "assistant",
        "content": content,
        "status": status,
        "created_at": "2026-07-25T12:00:00+00:00",
    }


@pytest.mark.asyncio
@patch("services.handlers.video_handler.WorkerMediaTasks")
async def test_worker_success_pushes_done_and_releases_slot(repository_type) -> None:
    content = [{"type": "video", "url": "https://asset", "duration": 10}]
    repository_type.return_value.commit_video_terminal.return_value = {
        "outcome": "committed",
        "message": _message("completed", content),
    }
    handler = VideoHandler(MagicMock())
    handler._push_ws_message = AsyncMock()
    handler._maybe_fanout_to_wecom = AsyncMock()
    handler._schedule_worker_metric = MagicMock()
    handler._release_worker_slot = AsyncMock()

    message = await handler._on_worker_complete(
        _task(), [VideoPart(url="https://asset", duration=10)]
    )

    assert message.status.value == "completed"
    handler._push_ws_message.assert_awaited_once()
    handler._release_worker_slot.assert_awaited_once_with(_task())


@pytest.mark.asyncio
@patch("services.handlers.video_handler.WorkerMediaTasks")
async def test_worker_failure_pushes_error_and_releases_slot(repository_type) -> None:
    content = [{"type": "text", "text": "模型超时"}]
    repository_type.return_value.commit_video_terminal.return_value = {
        "outcome": "failed",
        "message": _message("failed", content),
    }
    handler = VideoHandler(MagicMock())
    handler._push_ws_message = AsyncMock()
    handler._schedule_worker_metric = MagicMock()
    handler._extract_failure_knowledge = AsyncMock()
    handler._release_worker_slot = AsyncMock()

    message = await handler._on_worker_error(
        _task(), "TIMEOUT", "模型超时",
    )

    assert message.status.value == "failed"
    assert message.error and message.error.code == "TIMEOUT"
    handler._push_ws_message.assert_awaited_once()
    handler._release_worker_slot.assert_awaited_once_with(_task())
