"""旧业务门禁、L2 Scene 与 L3 Persona 生产链路退出测试。"""

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.memory.pipeline_scheduler import PipelineScheduler
from services.memory.config import MemoryV2Config
from services.memory.memory_service_v2 import MemoryServiceV2
from services.prompt_builder.builder import BuildInput, PromptBuilder


STATE = {
    "user_id": "11111111-1111-1111-1111-111111111111",
    "org_id": "22222222-2222-2222-2222-222222222222",
    "session_id": "33333333-3333-3333-3333-333333333333",
}
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_scheduler_has_no_l2_or_l3_runtime_entrypoints() -> None:
    scheduler = PipelineScheduler(AsyncMock())

    assert not hasattr(scheduler, "_schedule_l2")
    assert not hasattr(scheduler, "_run_l2")
    assert not hasattr(scheduler, "_maybe_trigger_l3")
    assert not hasattr(scheduler, "_run_l3")
    assert not hasattr(scheduler, "_should_extract")


def test_removed_l2_l3_config_and_domain_argument_do_not_return() -> None:
    config_fields = MemoryV2Config.__dataclass_fields__
    retrieval_parameters = inspect.signature(
        MemoryServiceV2.get_relevant_memories
    ).parameters

    assert not any(name.startswith(("l2_", "l3_")) for name in config_fields)
    assert not any(name.startswith("pipeline_l2_") for name in config_fields)
    assert "pipeline_session_active_hours" not in config_fields
    assert "domain" not in retrieval_parameters


def test_v2_facade_has_no_dormant_manual_crud() -> None:
    for name in (
        "add_memory",
        "get_all_memories",
        "delete_memory",
        "delete_all_memories",
        "get_memory_count",
    ):
        assert not hasattr(MemoryServiceV2, name)


def test_mem0_runtime_is_physically_removed() -> None:
    for path in (
        "services/memory_service.py",
        "services/memory_config.py",
        "services/memory_filter.py",
    ):
        assert not (BACKEND_ROOT / path).exists()

    requirements = (BACKEND_ROOT / "requirements.txt").read_text(
        encoding="utf-8"
    )
    main_source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
    assert "mem0ai" not in requirements.casefold()
    assert "_get_mem0" not in main_source
    assert "Mem0 pre-warm" not in main_source


@pytest.mark.asyncio
async def test_legacy_entry_without_revision_is_fail_closed() -> None:
    scheduler = PipelineScheduler(AsyncMock())
    scheduler._session_flush = AsyncMock()

    await scheduler._run_l1(STATE)

    scheduler._session_flush.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_revision_does_not_touch_pipeline_state() -> None:
    db = AsyncMock()
    scheduler = PipelineScheduler(db)

    await scheduler.on_turn_committed(
        user_id=STATE["user_id"],
        org_id=STATE["org_id"],
        session_id=STATE["session_id"],
    )

    db.fetchrow.assert_not_awaited()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_legacy_persona_is_ignored() -> None:
    builder = PromptBuilder(BuildInput(
        user_id="user-1",
        org_id="org-1",
        conversation_id="conversation-1",
        text_content="当前问题",
    ))
    with (
        patch(
            "services.prompt_builder.session_memory_cache.get_session_memory",
            new=AsyncMock(return_value=("通用记忆", "旧画像")),
        ),
        patch(
            "services.handlers.chat_context.summary_manager.get_context_summary",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.handlers.chat_context.history_loader.build_context_messages",
            new=AsyncMock(return_value=[]),
        ),
    ):
        memory, summary, history = await builder._parallel_fetch()

    assert memory == "通用记忆"
    assert builder._persona_text == ""
    assert summary is None
    assert history == []
