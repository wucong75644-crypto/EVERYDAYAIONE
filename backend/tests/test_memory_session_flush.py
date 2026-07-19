"""固定 revision Session Memory Flush 行为测试。"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from services.memory.l1_extractor import (
    L1ProposalResult,
    MemoryAtom,
    SceneSegment,
)
from services.memory.session_flush import SessionFlushResult, SessionFlushService


def _message(
    message_id: str,
    role: str,
    text: str,
    revision: int,
) -> dict:
    return {
        "id": message_id,
        "role": role,
        "content": [{"type": "text", "text": text}],
        "context_revision": revision,
    }


class _MemoryDB:
    def __init__(
        self,
        messages: list[dict],
        cursor: int = 0,
        *,
        log_rows: list[dict] | None = None,
        atom_rows: list[dict] | None = None,
    ):
        self.messages = messages
        self.cursor = cursor
        self.commit_calls = 0
        self.log_rows = log_rows or []
        self.atom_rows = atom_rows or []
        self.last_content: dict | None = None

    async def fetch(self, sql, *_args):
        if "FROM messages" in sql:
            return self.messages[:int(_args[-1])]
        if "FROM memory_session_logs" in sql:
            return self.log_rows
        if "FROM memory_atoms" in sql:
            return self.atom_rows
        raise AssertionError(f"unexpected SQL: {sql}")

    async def fetchrow(self, sql, *args):
        if "SELECT l1_cursor_revision" in sql:
            return {"l1_cursor_revision": self.cursor}
        if "commit_memory_session_flush" in sql:
            self.commit_calls += 1
            self.last_content = json.loads(args[6])
            expected_revision = int(args[3])
            through_revision = int(args[4])
            if self.cursor != expected_revision:
                return {
                    "result": {
                        "outcome": "stale",
                        "cursor_revision": self.cursor,
                    },
                }
            await asyncio.sleep(0)
            self.cursor = through_revision
            return {
                "result": {
                    "outcome": "committed",
                    "cursor_revision": through_revision,
                    "session_log_id": "log-1",
                },
            }
        raise AssertionError(f"unexpected SQL: {sql}")


def _candidate_proposal(content: str) -> L1ProposalResult:
    atom = MemoryAtom(
        content=content,
        type="persona",
        source_message_ids=["u1"],
        metadata={
            "kind": "preference",
            "scope": "long_term",
            "explicitness": "explicit",
            "evidence": [{"message_id": "u1", "quote": content}],
        },
    )
    return L1ProposalResult(
        success=True,
        decision="CANDIDATES",
        scenes=[SceneSegment(
            scene_name="通用会话记忆",
            message_ids=["u1"],
            memories=[atom],
        )],
    )


@pytest.mark.asyncio
async def test_no_memory_is_a_successful_receipt_and_advances_cursor():
    db = _MemoryDB([
        _message("u1", "user", "请介绍一下这个功能", 1),
        _message("a1", "assistant", "这是功能介绍", 1),
    ])
    service = SessionFlushService(db)

    with patch(
        "services.memory.session_flush.L1Extractor.propose",
        new=AsyncMock(return_value=L1ProposalResult(
            success=True,
            decision="NO_MEMORY",
        )),
    ):
        result = await service.flush(
            user_id="11111111-1111-1111-1111-111111111111",
            org_id="22222222-2222-2222-2222-222222222222",
            conversation_id="33333333-3333-3333-3333-333333333333",
            through_revision=1,
        )

    assert result.outcome == "committed"
    assert result.decision == "NO_MEMORY"
    assert result.session_log_id == "log-1"
    assert db.cursor == 1


@pytest.mark.asyncio
async def test_invalid_model_output_does_not_commit_or_advance_cursor():
    db = _MemoryDB([_message("u1", "user", "以后都用中文回答", 1)])
    service = SessionFlushService(db)

    with patch(
        "services.memory.session_flush.L1Extractor.propose",
        new=AsyncMock(return_value=L1ProposalResult(success=False)),
    ):
        result = await service.flush(
            user_id="11111111-1111-1111-1111-111111111111",
            org_id="22222222-2222-2222-2222-222222222222",
            conversation_id="33333333-3333-3333-3333-333333333333",
            through_revision=1,
        )

    assert result.outcome == "rejected"
    assert db.commit_calls == 0
    assert db.cursor == 0


@pytest.mark.asyncio
async def test_empty_fixed_window_does_not_advance_cursor():
    db = _MemoryDB([])
    service = SessionFlushService(db)

    result = await service.flush(
        user_id="11111111-1111-1111-1111-111111111111",
        org_id="22222222-2222-2222-2222-222222222222",
        conversation_id="33333333-3333-3333-3333-333333333333",
        through_revision=1,
    )

    assert result.outcome == "empty_window"
    assert db.commit_calls == 0
    assert db.cursor == 0


@pytest.mark.asyncio
async def test_local_single_flight_only_proposes_and_commits_once():
    db = _MemoryDB([_message("u1", "user", "以后都用中文回答", 1)])
    service = SessionFlushService(db)
    proposal = AsyncMock(return_value=L1ProposalResult(
        success=True,
        decision="NO_MEMORY",
    ))

    with patch(
        "services.memory.session_flush.L1Extractor.propose",
        new=proposal,
    ):
        first, second = await asyncio.gather(
            service.flush(
                user_id="11111111-1111-1111-1111-111111111111",
                org_id="22222222-2222-2222-2222-222222222222",
                conversation_id="33333333-3333-3333-3333-333333333333",
                through_revision=1,
            ),
            service.flush(
                user_id="11111111-1111-1111-1111-111111111111",
                org_id="22222222-2222-2222-2222-222222222222",
                conversation_id="33333333-3333-3333-3333-333333333333",
                through_revision=1,
            ),
        )

    assert {first.outcome, second.outcome} == {
        "committed",
        "already_committed",
    }
    assert proposal.await_count == 1
    assert db.commit_calls == 1


@pytest.mark.asyncio
async def test_window_stops_at_twenty_messages_and_uses_last_revision():
    db = _MemoryDB([
        _message(f"u{revision}", "user", f"消息 {revision}", revision)
        for revision in range(1, 26)
    ])
    service = SessionFlushService(db)

    with patch(
        "services.memory.session_flush.L1Extractor.propose",
        new=AsyncMock(return_value=L1ProposalResult(
            success=True,
            decision="NO_MEMORY",
        )),
    ) as proposal:
        result = await service.flush(
            user_id="11111111-1111-1111-1111-111111111111",
            org_id="22222222-2222-2222-2222-222222222222",
            conversation_id="33333333-3333-3333-3333-333333333333",
            through_revision=25,
        )

    assert len(proposal.await_args.args[0]) == 20
    assert result.through_revision == 20
    assert db.cursor == 20


@pytest.mark.asyncio
async def test_window_never_splits_messages_from_same_revision():
    rows = [
        _message(f"u{revision}", "user", f"消息 {revision}", revision)
        for revision in range(1, 20)
    ]
    rows.extend([
        _message("u20", "user", "同轮用户消息", 20),
        _message("a20", "assistant", "同轮助手消息", 20),
    ])
    db = _MemoryDB(rows)
    service = SessionFlushService(db)

    with patch(
        "services.memory.session_flush.L1Extractor.propose",
        new=AsyncMock(return_value=L1ProposalResult(
            success=True,
            decision="NO_MEMORY",
        )),
    ) as proposal:
        result = await service.flush(
            user_id="11111111-1111-1111-1111-111111111111",
            org_id="22222222-2222-2222-2222-222222222222",
            conversation_id="33333333-3333-3333-3333-333333333333",
            through_revision=20,
        )

    assert len(proposal.await_args.args[0]) == 19
    assert result.through_revision == 19
    assert db.cursor == 19


@pytest.mark.asyncio
async def test_exact_duplicate_is_recorded_but_not_saved_as_candidate():
    db = _MemoryDB(
        [_message("u1", "user", "用户偏好中文", 1)],
        log_rows=[{
            "content": {
                "decision": "CANDIDATES",
                "items": [{"claim": "  用户偏好中文  "}],
            },
        }],
    )
    service = SessionFlushService(db)

    with patch(
        "services.memory.session_flush.L1Extractor.propose",
        new=AsyncMock(return_value=_candidate_proposal("用户偏好中文")),
    ):
        result = await service.flush(
            user_id="11111111-1111-1111-1111-111111111111",
            org_id="22222222-2222-2222-2222-222222222222",
            conversation_id="33333333-3333-3333-3333-333333333333",
            through_revision=1,
        )

    assert result.outcome == "committed"
    assert db.last_content["decision"] == "NO_MEMORY"
    assert db.last_content["receipt"]["duplicate_exact_count"] == 1
    assert db.last_content["receipt"]["accepted_count"] == 0


@pytest.mark.asyncio
async def test_semantic_duplicate_uses_point_nine_two_threshold():
    db = _MemoryDB(
        [_message("u1", "user", "我更喜欢中文回复", 1)],
        atom_rows=[{"content": "用户偏好使用中文", "content_hash": None}],
    )
    service = SessionFlushService(db)

    with patch(
        "services.memory.session_flush.L1Extractor.propose",
        new=AsyncMock(return_value=_candidate_proposal("用户喜欢中文回答")),
    ), patch(
        "services.memory.session_flush.get_embeddings",
        new=AsyncMock(return_value=[[1.0, 0.0], [1.0, 0.0]]),
    ):
        result = await service.flush(
            user_id="11111111-1111-1111-1111-111111111111",
            org_id="22222222-2222-2222-2222-222222222222",
            conversation_id="33333333-3333-3333-3333-333333333333",
            through_revision=1,
        )

    assert result.outcome == "committed"
    assert db.last_content["receipt"]["duplicate_semantic_count"] == 1
    assert db.last_content["receipt"]["compared_legacy_count"] == 1


@pytest.mark.asyncio
async def test_embedding_failure_does_not_commit_or_advance_cursor():
    db = _MemoryDB(
        [_message("u1", "user", "我更喜欢中文回复", 1)],
        atom_rows=[{"content": "用户偏好使用中文", "content_hash": None}],
    )
    service = SessionFlushService(db)

    with patch(
        "services.memory.session_flush.L1Extractor.propose",
        new=AsyncMock(return_value=_candidate_proposal("用户喜欢中文回答")),
    ), patch(
        "services.memory.session_flush.get_embeddings",
        new=AsyncMock(return_value=None),
    ):
        result = await service.flush(
            user_id="11111111-1111-1111-1111-111111111111",
            org_id="22222222-2222-2222-2222-222222222222",
            conversation_id="33333333-3333-3333-3333-333333333333",
            through_revision=1,
        )

    assert result.outcome == "dedup_failed"
    assert db.commit_calls == 0
    assert db.cursor == 0


@pytest.mark.asyncio
async def test_scheduler_routes_closed_revision_to_session_flush():
    from services.memory.pipeline_scheduler import PipelineScheduler

    scheduler = PipelineScheduler(db_pool=AsyncMock())
    scheduler._session_flush = AsyncMock()
    scheduler._consolidator = AsyncMock()
    scheduler._session_flush.flush.return_value = SessionFlushResult(
        outcome="committed",
        from_revision=0,
        through_revision=4,
    )
    state = {
        "user_id": "11111111-1111-1111-1111-111111111111",
        "org_id": "22222222-2222-2222-2222-222222222222",
        "session_id": "33333333-3333-3333-3333-333333333333",
    }

    await scheduler._run_l1(
        state,
        through_revision=4,
    )

    scheduler._session_flush.flush.assert_awaited_once_with(
        user_id=state["user_id"],
        org_id=state["org_id"],
        conversation_id=state["session_id"],
        through_revision=4,
    )
    scheduler._consolidator.consolidate.assert_awaited_once_with(
        user_id=state["user_id"],
        org_id=state["org_id"],
    )


@pytest.mark.asyncio
async def test_scheduler_isolates_consolidation_failure_after_flush():
    from services.memory.pipeline_scheduler import PipelineScheduler

    scheduler = PipelineScheduler(db_pool=AsyncMock())
    scheduler._session_flush = AsyncMock()
    scheduler._session_flush.flush.return_value = SessionFlushResult(
        outcome="committed",
        from_revision=0,
        through_revision=4,
    )
    scheduler._consolidator = AsyncMock()
    scheduler._consolidator.consolidate.side_effect = RuntimeError("provider down")
    state = {
        "user_id": "11111111-1111-1111-1111-111111111111",
        "org_id": "22222222-2222-2222-2222-222222222222",
        "session_id": "33333333-3333-3333-3333-333333333333",
    }

    await scheduler._run_l1(state, through_revision=4)

    scheduler._consolidator.consolidate.assert_awaited_once()
