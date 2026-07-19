"""Grok 风格通用 L1 记忆提取与失败关闭测试。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestL1ExtractionPrompt:
    def test_formats_untrusted_messages_as_json(self):
        from services.memory.prompts.l1_extraction import format_extraction_prompt

        result = format_extraction_prompt(
            [{"id": "m1", "role": "user", "content": "我喜欢吃苹果"}],
            background_messages=[
                {"id": "bg1", "role": "user", "content": "你好"},
            ],
            previous_scene_name="不应进入提示词",
        )

        payload = json.loads(result.split("\n", 1)[1])
        assert payload["new_messages"][0]["id"] == "m1"
        assert payload["background_for_context_only"][0]["content"] == "你好"
        assert "不应进入提示词" not in result

    def test_empty_messages_are_supported(self):
        from services.memory.prompts.l1_extraction import format_extraction_prompt

        payload = json.loads(format_extraction_prompt([]).split("\n", 1)[1])
        assert payload["new_messages"] == []

    def test_system_prompt_is_generic_and_fail_closed(self):
        from services.memory.prompts.l1_extraction import (
            EXTRACT_MEMORIES_SYSTEM_PROMPT,
        )

        assert "NO_MEMORY" in EXTRACT_MEMORIES_SYSTEM_PROMPT
        assert "exact source text" in EXTRACT_MEMORIES_SYSTEM_PROMPT
        assert "instruction" in EXTRACT_MEMORIES_SYSTEM_PROMPT
        assert "电商" not in EXTRACT_MEMORIES_SYSTEM_PROMPT


class TestL1ExtractionParser:
    def test_valid_candidate_keeps_exact_evidence(self):
        from services.memory.l1_extractor import _parse_extraction_result

        source = [{"id": "m1", "role": "user", "content": "我一直喜欢用Python写代码"}]
        raw = json.dumps({
            "decision": "CANDIDATES",
            "items": [{
                "claim": "用户长期偏好使用 Python",
                "kind": "preference",
                "scope": "long_term",
                "explicitness": "explicit",
                "evidence": [{"message_id": "m1", "quote": "我一直喜欢用Python写代码"}],
            }],
        })

        scenes = _parse_extraction_result(raw, source)

        assert scenes[0].memories[0].content == "用户长期偏好使用 Python"
        assert scenes[0].memories[0].source_message_ids == ["m1"]
        assert scenes[0].memories[0].metadata["kind"] == "preference"

    @pytest.mark.parametrize(
        "raw",
        [
            '{"decision":"NO_MEMORY"}',
            "这不是JSON",
            '```json\n{"decision":"NO_MEMORY"}\n```',
            '{"decision":"CANDIDATES","items":[]}',
        ],
    )
    def test_non_candidate_outputs_write_nothing(self, raw):
        from services.memory.l1_extractor import _parse_extraction_result

        assert _parse_extraction_result(raw, []) == []

    def test_assistant_only_evidence_rejects_batch(self):
        from services.memory.l1_extractor import _parse_extraction_result

        source = [{"id": "a1", "role": "assistant", "content": "你喜欢跑步"}]
        raw = json.dumps({
            "decision": "CANDIDATES",
            "items": [{
                "claim": "用户喜欢跑步",
                "kind": "preference",
                "scope": "long_term",
                "explicitness": "explicit",
                "evidence": [{"message_id": "a1", "quote": "你喜欢跑步"}],
            }],
        })
        assert _parse_extraction_result(raw, source) == []

    def test_one_invalid_candidate_rejects_whole_batch(self):
        from services.memory.l1_extractor import _parse_extraction_result

        source = [{"id": "m1", "role": "user", "content": "以后都用中文回答"}]
        valid = {
            "claim": "以后使用中文回答",
            "kind": "instruction",
            "scope": "long_term",
            "explicitness": "explicit",
            "evidence": [{"message_id": "m1", "quote": "以后都用中文回答"}],
        }
        invalid = dict(valid, evidence=[{"message_id": "missing", "quote": "虚构证据"}])
        raw = json.dumps({"decision": "CANDIDATES", "items": [valid, invalid]})
        assert _parse_extraction_result(raw, source) == []


class TestL1Proposal:
    @pytest.mark.asyncio
    async def test_no_memory_is_valid_and_invalid_json_is_failure(self):
        from services.memory.l1_extractor import L1Extractor

        extractor = L1Extractor()
        extractor._call_llm_extraction = AsyncMock(
            side_effect=['{"decision":"NO_MEMORY"}', "invalid"],
        )

        no_memory = await extractor.propose([])
        invalid = await extractor.propose([])

        assert no_memory.success is True
        assert no_memory.decision == "NO_MEMORY"
        assert invalid.success is False
        assert invalid.decision == "INVALID"


class TestMemoryClosedRevisionDispatch:
    @pytest.mark.asyncio
    async def test_extract_memories_dispatches_closed_revision(self):
        from services.handlers.chat_context_mixin import ChatContextMixin

        scheduler = MagicMock()
        scheduler.on_turn_committed = AsyncMock()
        handler = SimpleNamespace(db=MagicMock(), org_id="org-1")

        with patch(
            "services.memory.memory_service_v2.get_scheduler",
            new=AsyncMock(return_value=scheduler),
        ) as get_scheduler:
            await ChatContextMixin._extract_memories_async(
                handler,
                user_id="user-1",
                conversation_id="conversation-1",
                user_text="以后请始终使用中文回答我",
                assistant_text="好的",
                input_message_id="input-1",
                output_message_id="output-1",
                through_revision=7,
            )

        get_scheduler.assert_awaited_once_with(db_pool=handler.db)
        assert "messages" not in scheduler.on_turn_committed.await_args.kwargs
        assert (
            scheduler.on_turn_committed.await_args.kwargs["through_revision"]
            == 7
        )

    @pytest.mark.asyncio
    async def test_short_message_still_dispatches_closed_revision(self):
        from services.handlers.chat_context_mixin import ChatContextMixin

        scheduler = MagicMock()
        scheduler.on_turn_committed = AsyncMock()
        handler = SimpleNamespace(db=MagicMock(), org_id="org-1")

        with patch(
            "services.memory.memory_service_v2.get_scheduler",
            new=AsyncMock(return_value=scheduler),
        ):
            await ChatContextMixin._extract_memories_async(
                handler,
                user_id="user-1",
                conversation_id="conversation-1",
                user_text="记住",
                assistant_text="好的",
                through_revision=8,
            )

        assert (
            scheduler.on_turn_committed.await_args.kwargs["through_revision"]
            == 8
        )
