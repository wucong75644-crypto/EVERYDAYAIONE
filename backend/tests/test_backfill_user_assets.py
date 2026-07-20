"""历史用户资产回填脚本测试。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.backfill_user_assets import (
    BackfillStats,
    decode_content,
    load_checkpoint,
    media_parts,
    process_projection,
    project_row,
    run,
    save_checkpoint,
)
from scripts.backfill_user_assets_sql import SOURCE_QUERIES
from services.assets import AssetIdentityError


USER_ID = "00000000-0000-4000-8000-000000000001"
ORG_ID = "00000000-0000-4000-8000-000000000002"
ROW_ID = "00000000-0000-4000-8000-000000000003"
CONVERSATION_ID = "00000000-0000-4000-8000-000000000004"
MESSAGE_ID = "00000000-0000-4000-8000-000000000005"
CREATED_AT = "2026-07-20T10:00:00+00:00"
URL = "https://cdn.example.com/workspace/image.png"


def _base_row(**values):
    return {
        "id": ROW_ID,
        "created_at": CREATED_AT,
        "actor_user_id": USER_ID,
        "org_id": ORG_ID,
        "conversation_id": CONVERSATION_ID,
        **values,
    }


def test_decode_content_and_media_parts_accept_historical_shapes() -> None:
    content = json.dumps([
        {"type": "text", "text": "done"},
        {"type": "image_url", "image_url": {"url": URL}},
    ])
    assert decode_content(content)[0]["type"] == "text"
    assert media_parts(content) == [(
        1,
        {"type": "image", "image_url": {"url": URL}, "url": URL},
    )]
    assert [part["url"] for _, part in media_parts({
        "image_urls": [URL, f"{URL}?second=1"],
        "video_url": "https://cdn.example.com/workspace/video.mp4",
    })] == [
        URL,
        f"{URL}?second=1",
        "https://cdn.example.com/workspace/video.mp4",
    ]


def test_project_image_generation_uses_stable_generation_ref() -> None:
    projections = project_row("image_generations", _base_row(
        url=URL,
        model_id="00000000-0000-4000-8000-000000000006",
        prompt="test prompt",
    ))
    asset, ref = projections[0]
    assert asset.storage_scope == "user"
    assert asset.storage_owner_key == USER_ID
    assert asset.media_type == "image"
    assert ref.ref_key == f"image_generation:{ROW_ID}"
    assert ref.ref_kind == "image_generation"
    assert ref.source_generation_id == ROW_ID
    assert ref.prompt == "test prompt"


def test_project_task_preserves_multiple_content_indexes() -> None:
    row = _base_row(
        type="image",
        result_data=None,
        result={"image_urls": [URL, f"{URL}?v=2"]},
        request_params={"prompt": "two images"},
        model_id=None,
        assistant_message_id=MESSAGE_ID,
        scope_type="user",
    )
    projections = project_row("tasks", row)
    assert [ref.ref_key for _, ref in projections] == [
        f"task:{ROW_ID}:0",
        f"task:{ROW_ID}:1",
    ]
    assert [ref.content_index for _, ref in projections] == [0, 1]
    assert all(ref.source_task_id == ROW_ID for _, ref in projections)
    assert all(ref.source_message_id == MESSAGE_ID for _, ref in projections)


def test_project_user_message_maps_upload_and_nested_image() -> None:
    row = _base_row(
        content=[{
            "type": "image_url",
            "image_url": {"url": URL},
            "name": "uploaded.png",
        }],
        scope_type="user",
    )
    asset, ref = project_row("user_messages", row)[0]
    assert asset.name == "uploaded.png"
    assert ref.source_type == "upload"
    assert ref.source_kind == "web_upload"
    assert ref.ref_key == f"message:{ROW_ID}:0"


@pytest.mark.parametrize(
    ("mime_type", "expected_media_type"),
    (("image/jpeg", "image"), ("video/mp4", "video")),
)
def test_project_user_message_normalizes_historical_file_media_type(
    mime_type: str,
    expected_media_type: str,
) -> None:
    row = _base_row(
        content=[{
            "type": "file",
            "url": URL,
            "mime_type": mime_type,
        }],
        scope_type="user",
    )

    asset, _ = project_row("user_messages", row)[0]

    assert asset.media_type == expected_media_type
    assert asset.mime_type == mime_type


def test_project_attachment_rebuilds_channel_owner() -> None:
    row = _base_row(
        source_message_id=MESSAGE_ID,
        name="report.pdf",
        url="https://cdn.example.com/workspace/report.pdf",
        workspace_path="org/x/channels/wecom/file/report.pdf",
        storage_scope="channel",
        mime_type="application/pdf",
        size=20,
        corp_id="corp",
        external_chat_id="chat",
    )
    asset, ref = project_row("attachments", row)[0]
    assert asset.storage_scope == "channel"
    assert asset.storage_owner_key.startswith("channels/wecom/")
    assert len(asset.storage_owner_key) == len("channels/wecom/") + 24
    assert ref.ref_key == f"wecom:{ROW_ID}"
    assert ref.source_attachment_id == ROW_ID


def test_project_row_skips_missing_actor() -> None:
    assert project_row(
        "image_generations",
        _base_row(actor_user_id=None, url=URL),
    ) == []


def test_checkpoint_round_trip_and_validation(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    value = {
        "tasks": {"created_at": CREATED_AT, "id": ROW_ID},
    }
    save_checkpoint(path, value)
    assert load_checkpoint(path) == value
    assert not path.with_suffix(".json.tmp").exists()

    path.write_text('{"unknown": {"created_at": "x", "id": "y"}}')
    with pytest.raises(ValueError, match="cursor is invalid"):
        load_checkpoint(path)


def test_process_projection_counts_created_and_reused_assets() -> None:
    projection = project_row(
        "image_generations",
        _base_row(url=URL, model_id=None, prompt=None),
    )[0]
    registry = MagicMock()
    registry.db.conn.transaction.return_value.__enter__.return_value = None
    registry.register_ready_asset.side_effect = [
        {"asset_created": True, "ref_created": True},
        {"asset_created": False, "ref_created": False},
    ]
    identity = MagicMock(storage_provider="oss", storage_key="image.png")
    stats = BackfillStats()
    identities: set[tuple] = set()

    with patch(
        "scripts.backfill_user_assets.resolve_asset_identity",
        return_value=identity,
    ):
        process_projection(
            registry, projection, stats, identities, apply=True,
        )
        process_projection(
            registry, projection, stats, identities, apply=True,
        )

    assert stats.projected_refs == 2
    assert stats.assets_created == 1
    assert stats.assets_reused == 1
    assert stats.refs_created == 1
    assert stats.refs_reused == 1
    assert len(identities) == 1


def test_process_projection_classifies_conflict() -> None:
    projection = project_row(
        "image_generations",
        _base_row(url=URL, model_id=None, prompt=None),
    )[0]
    registry = MagicMock()
    registry.db.conn.transaction.return_value.__enter__.return_value = None
    registry.register_ready_asset.side_effect = RuntimeError(
        "USER_ASSET_REF_CONFLICT",
    )
    identity = MagicMock(storage_provider="oss", storage_key="image.png")
    stats = BackfillStats()

    with patch(
        "scripts.backfill_user_assets.resolve_asset_identity",
        return_value=identity,
    ):
        process_projection(
            registry, projection, stats, set(), apply=True,
        )

    assert stats.conflicts == 1
    assert stats.failures == 0
    assert stats.failure_reasons == {
        "image_task:USER_ASSET_REF_CONFLICT": 1,
    }


def test_process_projection_skips_unpersisted_url() -> None:
    projection = project_row(
        "image_generations",
        _base_row(url=URL, model_id=None, prompt=None),
    )[0]
    stats = BackfillStats()
    with patch(
        "scripts.backfill_user_assets.resolve_asset_identity",
        side_effect=AssetIdentityError("ASSET_URL_NOT_PERSISTED"),
    ):
        process_projection(
            MagicMock(), projection, stats, set(), apply=False,
        )

    assert stats.failures == 0
    assert stats.skipped == 1
    assert stats.skipped_reasons == {
        "image_task:ASSET_URL_NOT_PERSISTED": 1,
    }


def test_process_projection_drops_stale_historical_workspace_path() -> None:
    asset, ref = project_row("user_messages", _base_row(
        content=[{
            "type": "file",
            "url": URL,
            "workspace_path": "legacy/path/image.png",
        }],
        scope_type="user",
    ))[0]
    registry = MagicMock()
    registry.db.conn.transaction.return_value.__enter__.return_value = None
    registry.register_ready_asset.return_value = {
        "asset_created": True,
        "ref_created": True,
    }
    identity = MagicMock(storage_provider="oss", storage_key="image.png")
    stats = BackfillStats()
    with patch(
        "scripts.backfill_user_assets.resolve_asset_identity",
        side_effect=[
            AssetIdentityError("ASSET_WORKSPACE_URL_MISMATCH"),
            identity,
        ],
    ):
        process_projection(
            registry, (asset, ref), stats, set(), apply=True,
        )

    registered_asset = registry.register_ready_asset.call_args.args[0]
    assert registered_asset.workspace_path is None
    assert stats.normalized_workspace_paths == 1
    assert stats.failures == 0


def test_run_dry_run_never_writes_checkpoint(tmp_path: Path) -> None:
    conn = MagicMock()
    row = _base_row(url=URL)
    batches = {
        "image_generations": [[row], []],
        "tasks": [[]],
        "assistant_messages": [[]],
        "user_messages": [[]],
        "attachments": [[]],
    }

    def fake_fetch(_conn, source, _cursor, _batch_size):
        return batches[source].pop(0)

    with (
        patch(
            "scripts.backfill_user_assets.fetch_batch",
            side_effect=fake_fetch,
        ),
        patch(
            "scripts.backfill_user_assets.process_projection",
        ) as process,
        patch(
            "scripts.backfill_user_assets.count_orphans",
            return_value=0,
        ),
    ):
        stats = run(
            conn,
            apply=False,
            batch_size=10,
            checkpoint_path=tmp_path / "checkpoint.json",
        )

    assert stats.source_rows == 1
    assert process.call_count == 1
    assert conn.commit.call_count == 0
    conn.rollback.assert_called_once()
    assert not (tmp_path / "checkpoint.json").exists()


def test_run_does_not_advance_failed_batch_checkpoint(
    tmp_path: Path,
) -> None:
    conn = MagicMock()
    row = _base_row(url=URL)

    def fake_fetch(_conn, source, _cursor, _batch_size):
        return [row] if source == "image_generations" else []

    def fail_projection(_registry, _projection, stats, _identities, *, apply):
        assert apply is True
        stats.failures += 1

    checkpoint_path = tmp_path / "checkpoint.json"
    with (
        patch(
            "scripts.backfill_user_assets.fetch_batch",
            side_effect=fake_fetch,
        ),
        patch(
            "scripts.backfill_user_assets.process_projection",
            side_effect=fail_projection,
        ),
        patch(
            "scripts.backfill_user_assets.count_orphans",
            return_value=0,
        ),
    ):
        stats = run(
            conn,
            apply=True,
            batch_size=10,
            checkpoint_path=checkpoint_path,
            limit=1,
        )

    assert stats.failures == 1
    assert load_checkpoint(checkpoint_path) == {}
    conn.commit.assert_called_once()


def test_run_counts_projection_error_without_advancing_checkpoint(
    tmp_path: Path,
) -> None:
    conn = MagicMock()
    row = _base_row(
        content=[{"type": "file", "url": URL}],
        scope_type="channel",
        corp_id=None,
        external_chat_id=None,
    )
    seen = False

    def fake_fetch(_conn, source, _cursor, _batch_size):
        nonlocal seen
        if source == "user_messages" and not seen:
            seen = True
            return [row]
        return []

    checkpoint_path = tmp_path / "checkpoint.json"
    with (
        patch(
            "scripts.backfill_user_assets.fetch_batch",
            side_effect=fake_fetch,
        ),
        patch(
            "scripts.backfill_user_assets.count_orphans",
            return_value=0,
        ),
    ):
        stats = run(
            conn,
            apply=True,
            batch_size=10,
            checkpoint_path=checkpoint_path,
        )

    assert stats.failures == 1
    assert stats.skipped == 0
    assert stats.failure_reasons == {
        "user_messages:WECOM_CHANNEL_WORKSPACE_IDENTITY_MISSING": 1,
    }
    assert "user_messages" not in load_checkpoint(checkpoint_path)


def test_source_queries_use_typed_composite_checkpoint() -> None:
    assert set(SOURCE_QUERIES) == {
        "image_generations", "tasks", "assistant_messages",
        "user_messages", "attachments",
    }
    for sql in SOURCE_QUERIES.values():
        assert "%(cursor_at)s::timestamptz" in sql
        assert "%(cursor_id)s::uuid" in sql
        assert "ORDER BY" in sql
        assert "LIMIT %(batch_size)s" in sql
