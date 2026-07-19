"""PromptBuilder 首轮记忆上限与压缩后重新检索测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from services.prompt_builder.builder import BuildInput, PromptBuilder


def _builder(*, personal_context_allowed: bool = True) -> PromptBuilder:
    return PromptBuilder(BuildInput(
        user_id="user-1",
        org_id="org-1",
        conversation_id="conversation-1",
        text_content="当前问题",
        personal_context_allowed=personal_context_allowed,
    ))


@pytest.mark.asyncio
async def test_compaction_refresh_deletes_cache_and_requeries() -> None:
    builder = _builder()
    with (
        patch(
            "services.prompt_builder.session_memory_cache.delete_session_memory",
            new=AsyncMock(),
        ) as delete,
        patch(
            "services.prompt_builder.session_memory_cache.set_session_memory",
            new=AsyncMock(),
        ) as store,
        patch(
            "services.memory.memory_service_v2.MemoryServiceV2.build_memory_context",
            new=AsyncMock(return_value=("新记忆", "")),
        ) as search,
    ):
        result = await builder._refresh_memory_after_compaction()

    assert result == ("新记忆", "")
    delete.assert_awaited_once_with("conversation-1", "org-1")
    search.assert_awaited_once_with(
        user_id="user-1",
        org_id="org-1",
        query="当前问题",
    )
    store.assert_awaited_once_with(
        "conversation-1", "新记忆", "", "org-1",
    )


@pytest.mark.asyncio
async def test_compaction_refresh_failure_does_not_reuse_old_memory() -> None:
    builder = _builder()
    with (
        patch(
            "services.prompt_builder.session_memory_cache.delete_session_memory",
            new=AsyncMock(),
        ),
        patch(
            "services.memory.memory_service_v2.MemoryServiceV2.build_memory_context",
            new=AsyncMock(side_effect=TimeoutError("timeout")),
        ),
    ):
        result = await builder._refresh_memory_after_compaction()

    assert result == (None, "")


@pytest.mark.asyncio
async def test_compaction_refresh_respects_personal_context_policy() -> None:
    builder = _builder(personal_context_allowed=False)
    with (
        patch(
            "services.prompt_builder.session_memory_cache.delete_session_memory",
            new=AsyncMock(),
        ) as delete,
        patch(
            "services.memory.memory_service_v2.MemoryServiceV2.build_memory_context",
            new=AsyncMock(),
        ) as search,
    ):
        result = await builder._refresh_memory_after_compaction()

    assert result == (None, "")
    delete.assert_awaited_once()
    search.assert_not_awaited()
