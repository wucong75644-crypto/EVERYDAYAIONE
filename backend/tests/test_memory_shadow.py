"""Session Memory shadow 读取与 PromptBuilder 非注入测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.memory.memory_service_v2 import MemoryServiceV2


@pytest.mark.asyncio
async def test_shadow_reader_returns_only_candidate_claims():
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[
        {
            "content": {
                "decision": "CANDIDATES",
                "items": [{"claim": "用户偏好中文"}],
            },
        },
        {"content": '{"decision":"NO_MEMORY"}'},
        {"content": "invalid"},
    ])
    service = MemoryServiceV2()
    service._db = db

    claims = await service.get_session_memory_shadow("user-1")

    assert claims == ["用户偏好中文"]


@pytest.mark.asyncio
async def test_memory_context_reads_shadow_without_injecting_it():
    service = MemoryServiceV2()
    service._db = MagicMock()
    service._retrieval = MagicMock()
    service._retrieval.search = AsyncMock(return_value=[])
    service._retrieval.format_for_injection.return_value = "旧记忆内容"
    service.get_session_memory_shadow = AsyncMock(
        return_value=["只能影子读取的内容"],
    )

    memory, persona = await service.build_memory_context(
        user_id="user-1",
        org_id="org-1",
        query="当前问题",
    )

    assert memory == "旧记忆内容"
    assert persona == ""
    assert "只能影子读取的内容" not in memory
    service.get_session_memory_shadow.assert_awaited_once_with("user-1")
    service._retrieval.search.assert_awaited_once_with(
        query="当前问题",
        user_id="user-1",
        org_id="org-1",
        max_results=3,
    )
