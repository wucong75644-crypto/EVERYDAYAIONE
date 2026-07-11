"""
记忆系统 V2 单元测试

覆盖：
- L1 提取提示词格式化
- L1 冲突检测提示词格式化
- L1 解析 LLM 输出
- RRF 混合检索融合算法
- 管道调度 Warm-up 逻辑
- 上下文压缩三级策略
- 质量门过滤
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


# ============ Fixtures ============

@pytest.fixture
def user_id():
    return str(uuid4())

@pytest.fixture
def org_id():
    return str(uuid4())

@pytest.fixture
def session_id():
    return str(uuid4())


# ============================================================
# L1 提取提示词
# ============================================================

class TestL1ExtractionPrompt:
    """L1 提取提示词格式化测试"""

    def test_format_basic(self):
        from services.memory.prompts.l1_extraction import format_extraction_prompt

        messages = [
            {"id": "m1", "role": "user", "content": "我喜欢吃苹果", "timestamp": 1700000000000},
            {"id": "m2", "role": "assistant", "content": "好的，我记住了", "timestamp": 1700000001000},
        ]
        result = format_extraction_prompt(messages)

        assert "【待提取的新消息】" in result
        assert "我喜欢吃苹果" in result
        assert "[m1]" in result
        assert "[user]" in result

    def test_with_background(self):
        from services.memory.prompts.l1_extraction import format_extraction_prompt

        bg = [{"id": "bg1", "role": "user", "content": "你好", "timestamp": 1700000000000}]
        new = [{"id": "m1", "role": "user", "content": "我是程序员", "timestamp": 1700000001000}]
        result = format_extraction_prompt(new, background_messages=bg)

        assert "【背景对话】" in result
        assert "你好" in result
        assert "我是程序员" in result

    def test_previous_scene_name(self):
        from services.memory.prompts.l1_extraction import format_extraction_prompt

        result = format_extraction_prompt(
            [{"id": "m1", "role": "user", "content": "test", "timestamp": None}],
            previous_scene_name="我在和程序员讨论架构",
        )
        assert "我在和程序员讨论架构" in result

    def test_empty_messages(self):
        from services.memory.prompts.l1_extraction import format_extraction_prompt

        result = format_extraction_prompt([])
        assert "【待提取的新消息】" in result

    def test_system_prompt_contains_three_types(self):
        from services.memory.prompts.l1_extraction import EXTRACT_MEMORIES_SYSTEM_PROMPT

        assert "persona" in EXTRACT_MEMORIES_SYSTEM_PROMPT
        assert "episodic" in EXTRACT_MEMORIES_SYSTEM_PROMPT
        assert "instruction" in EXTRACT_MEMORIES_SYSTEM_PROMPT
        assert "宁缺毋滥" in EXTRACT_MEMORIES_SYSTEM_PROMPT


# ============================================================
# L1 冲突检测提示词
# ============================================================

class TestL1DedupPrompt:
    """L1 冲突检测提示词格式化测试"""

    def test_format_with_candidates(self):
        from services.memory.prompts.l1_dedup import format_batch_conflict_prompt

        matches = [
            {
                "new_memory": {
                    "record_id": "new_1",
                    "content": "用户喜欢Python",
                    "type": "persona",
                    "priority": 70,
                    "scene_name": "编程",
                },
                "candidates": [
                    {
                        "record_id": "old_1",
                        "content": "用户使用Python",
                        "type": "persona",
                        "priority": 60,
                        "scene_name": "编程",
                        "timestamps": ["2026-01-01"],
                    }
                ],
            }
        ]
        result = format_batch_conflict_prompt(matches)

        assert "统一候选记忆池" in result
        assert "用户喜欢Python" in result
        assert "用户使用Python" in result
        assert "new_1" in result
        assert "old_1" in result

    def test_format_no_candidates(self):
        from services.memory.prompts.l1_dedup import format_batch_conflict_prompt

        matches = [
            {
                "new_memory": {
                    "record_id": "new_1",
                    "content": "新记忆",
                    "type": "persona",
                    "priority": 70,
                },
                "candidates": [],
            }
        ]
        result = format_batch_conflict_prompt(matches)
        assert "直接 store" in result

    def test_system_prompt_contains_four_actions(self):
        from services.memory.prompts.l1_dedup import CONFLICT_DETECTION_SYSTEM_PROMPT

        for action in ["store", "update", "merge", "skip"]:
            assert f'"{action}"' in CONFLICT_DETECTION_SYSTEM_PROMPT
        assert "跨 type 合并" in CONFLICT_DETECTION_SYSTEM_PROMPT


# ============================================================
# L1 解析 LLM 输出
# ============================================================

class TestL1ExtractionParser:
    """L1 提取结果解析测试"""

    def test_parse_valid_json(self):
        from services.memory.l1_extractor import _parse_extraction_result

        raw = json.dumps([{
            "scene_name": "编程讨论",
            "message_ids": ["m1"],
            "memories": [
                {
                    "content": "用户喜欢Python",
                    "type": "persona",
                    "priority": 80,
                    "source_message_ids": ["m1"],
                    "metadata": {},
                }
            ],
        }])

        scenes = _parse_extraction_result(raw)
        assert len(scenes) == 1
        assert scenes[0].scene_name == "编程讨论"
        assert len(scenes[0].memories) == 1
        assert scenes[0].memories[0].content == "用户喜欢Python"
        assert scenes[0].memories[0].type == "persona"
        assert scenes[0].memories[0].priority == 80

    def test_parse_with_markdown_wrapper(self):
        from services.memory.l1_extractor import _parse_extraction_result

        raw = '```json\n[{"scene_name": "test", "message_ids": [], "memories": []}]\n```'
        scenes = _parse_extraction_result(raw)
        assert len(scenes) == 1
        assert scenes[0].scene_name == "test"

    def test_parse_empty_memories(self):
        from services.memory.l1_extractor import _parse_extraction_result

        raw = '[{"scene_name": "闲聊", "message_ids": ["m1"], "memories": []}]'
        scenes = _parse_extraction_result(raw)
        assert len(scenes) == 1
        assert len(scenes[0].memories) == 0

    def test_parse_invalid_json(self):
        from services.memory.l1_extractor import _parse_extraction_result

        scenes = _parse_extraction_result("这不是JSON")
        assert len(scenes) == 0

    def test_parse_multiple_scenes(self):
        from services.memory.l1_extractor import _parse_extraction_result

        raw = json.dumps([
            {"scene_name": "工作", "message_ids": ["m1"], "memories": [
                {"content": "用户是工程师", "type": "persona", "priority": 80, "source_message_ids": [], "metadata": {}}
            ]},
            {"scene_name": "生活", "message_ids": ["m2"], "memories": [
                {"content": "用户喜欢跑步", "type": "persona", "priority": 60, "source_message_ids": [], "metadata": {}}
            ]},
        ])
        scenes = _parse_extraction_result(raw)
        assert len(scenes) == 2

    def test_type_normalization(self):
        from services.memory.l1_extractor import _normalize_type

        assert _normalize_type("persona") == "persona"
        assert _normalize_type("episodic") == "episodic"
        assert _normalize_type("instruction") == "instruction"
        assert _normalize_type("episode") == "episodic"
        assert _normalize_type("preference") == "persona"
        assert _normalize_type("invalid") is None


# ============================================================
# L1 质量门
# ============================================================

class TestL1QualityGate:
    """L1 质量过滤测试"""

    def test_short_message_filtered(self):
        from services.memory.l1_extractor import _should_extract

        assert _should_extract("hi") is False
        assert _should_extract("ok") is False

    def test_command_filtered(self):
        from services.memory.l1_extractor import _should_extract

        assert _should_extract("/help") is False
        assert _should_extract("/reset") is False

    def test_normal_message_passes(self):
        from services.memory.l1_extractor import _should_extract

        assert _should_extract("我喜欢用Python写代码") is True
        assert _should_extract("今天去超市买了很多东西") is True

    def test_trigger_keywords_pass_short(self):
        from services.memory.l1_extractor import _should_extract

        assert _should_extract("我喜欢苹果") is True
        assert _should_extract("记住这个") is True


# ============================================================
# RRF 混合检索融合
# ============================================================

class TestRRFMerge:
    """RRF 融合算法测试"""

    def test_basic_merge(self):
        from services.memory.retrieval_pipeline import RetrievalPipeline

        pipeline = RetrievalPipeline()

        vector = [
            {"record_id": "a", "content": "A", "type": "persona", "priority": 80, "scene_name": ""},
            {"record_id": "b", "content": "B", "type": "persona", "priority": 70, "scene_name": ""},
            {"record_id": "c", "content": "C", "type": "persona", "priority": 60, "scene_name": ""},
        ]
        bm25 = [
            {"record_id": "b", "content": "B", "type": "persona", "priority": 70, "scene_name": ""},
            {"record_id": "d", "content": "D", "type": "persona", "priority": 50, "scene_name": ""},
            {"record_id": "a", "content": "A", "type": "persona", "priority": 80, "scene_name": ""},
        ]

        merged = pipeline._rrf_merge(vector, bm25, max_results=3)

        # b 和 a 都出现在两个列表中，RRF 分数更高
        ids = [m["record_id"] for m in merged]
        assert len(merged) == 3
        # b: rank 1(vec) + rank 0(bm25) → 高分，a: rank 0(vec) + rank 2(bm25) → 也高
        assert "a" in ids
        assert "b" in ids

    def test_rrf_score_calculation(self):
        """验证 RRF 分数计算：1/(K+rank+1)"""
        from services.memory.retrieval_pipeline import RetrievalPipeline

        pipeline = RetrievalPipeline()
        K = pipeline._cfg.retrieval_rrf_k  # 60

        vector = [{"record_id": "x", "content": "", "type": "persona", "priority": 50, "scene_name": ""}]
        bm25 = [{"record_id": "x", "content": "", "type": "persona", "priority": 50, "scene_name": ""}]

        merged = pipeline._rrf_merge(vector, bm25, max_results=1)
        expected_score = 1.0 / (K + 0 + 1) + 1.0 / (K + 0 + 1)  # rank 0 in both
        assert abs(merged[0]["rrf_score"] - expected_score) < 1e-6

    def test_format_for_injection(self):
        from services.memory.retrieval_pipeline import RetrievalPipeline, ScoredMemory

        pipeline = RetrievalPipeline()
        memories = [
            ScoredMemory(atom_id="1", content="用户是程序员", type="persona", priority=80, scene_name="工作", score=0.9),
            ScoredMemory(atom_id="2", content="用户五月去日本", type="episodic", priority=70, scene_name="旅行", score=0.8,
                        activity_start="2026-05-01T00:00:00", activity_end="2026-05-10T00:00:00"),
        ]
        result = pipeline.format_for_injection(memories)

        assert "[persona|工作]" in result
        assert "用户是程序员" in result
        assert "[episodic|旅行]" in result
        assert "2026-05-01" in result

    def test_format_empty(self):
        from services.memory.retrieval_pipeline import RetrievalPipeline

        pipeline = RetrievalPipeline()
        assert pipeline.format_for_injection([]) == ""


# ============================================================
# 上下文压缩
# ============================================================

class TestContextCompressor:
    """三级上下文压缩测试"""

    def _make_messages(self, count: int, content_len: int = 100) -> list:
        """生成测试消息"""
        msgs = [{"role": "system", "content": "你是助手。"}]
        for i in range(count):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append({"role": role, "content": f"消息{i}。" + "内容" * content_len})
        return msgs

    def test_no_compression_needed(self):
        import asyncio
        from services.memory.context_compressor import ContextCompressor

        comp = ContextCompressor()
        msgs = self._make_messages(3, 10)
        result = asyncio.run(
            comp.compress_if_needed(msgs, context_window=200000)
        )
        assert len(result) == len(msgs)  # 不压缩

    def test_aggressive_compress(self):
        from services.memory.context_compressor import ContextCompressor

        comp = ContextCompressor()
        msgs = self._make_messages(20, 500)
        result = comp._aggressive_compress(msgs)

        assert len(result) < len(msgs)
        # 始终保留 system prompt
        assert result[0]["role"] == "system"
        # 始终保留最近4条
        assert len(result) >= 5  # 1 system + 4 tail

    def test_emergency_compress(self):
        from services.memory.context_compressor import ContextCompressor

        comp = ContextCompressor()
        msgs = self._make_messages(50, 200)
        result = comp._emergency_compress(msgs, window=10000)

        assert len(result) >= 5  # system + at least 4
        assert result[0]["role"] == "system"

    def test_token_estimation(self):
        from services.memory.context_compressor import ContextCompressor

        comp = ContextCompressor()
        msgs = [{"role": "user", "content": "你好世界Hello World"}]
        tokens = comp._estimate_tokens(msgs)
        assert tokens > 0
        # 4个中文字 / 1.7 + 10个英文字 / 4 ≈ 2.35 + 2.75 ≈ 5
        assert 3 <= tokens <= 10


# ============================================================
# 管道调度 Warm-up
# ============================================================

class TestPipelineWarmup:
    """管道调度 Warm-up 逻辑测试（不涉及 DB）"""

    def test_warmup_threshold_progression(self):
        """验证 Warm-up 阈值：1→2→4→...→N"""
        from services.memory.config import MemoryV2Config

        cfg = MemoryV2Config(pipeline_every_n_conversations=5, pipeline_enable_warmup=True)

        threshold = 1
        progression = [threshold]
        while threshold > 0:
            new = min(threshold * 2, cfg.pipeline_every_n_conversations)
            if new >= cfg.pipeline_every_n_conversations:
                new = 0  # 毕业
            threshold = new
            progression.append(threshold)

        # 1 → 2 → 4 → 0（毕业，因为 4*2=8 > 5）
        assert progression == [1, 2, 4, 0]


# ============================================================
# L2 场景提示词
# ============================================================

class TestL2ScenePrompt:
    """L2 场景提示词格式化测试"""

    def test_format_with_warning(self):
        from services.memory.prompts.l2_scene import format_scene_extraction_prompt

        result = format_scene_extraction_prompt(
            memories_json="[]",
            scene_summaries="test",
            scene_count=15,
            max_scenes=15,
        )
        assert "红色预警" in result

    def test_format_orange_warning(self):
        from services.memory.prompts.l2_scene import format_scene_extraction_prompt

        result = format_scene_extraction_prompt(
            memories_json="[]",
            scene_summaries="",
            scene_count=14,
            max_scenes=15,
        )
        assert "橙色预警" in result

    def test_format_no_warning(self):
        from services.memory.prompts.l2_scene import format_scene_extraction_prompt

        result = format_scene_extraction_prompt(
            memories_json="[]",
            scene_summaries="",
            scene_count=5,
            max_scenes=15,
        )
        assert "预警" not in result


# ============================================================
# L3 画像提示词
# ============================================================

class TestL3PersonaPrompt:
    """L3 画像提示词格式化测试"""

    def test_first_mode(self):
        from services.memory.prompts.l3_persona import format_persona_prompt

        result = format_persona_prompt(
            mode="first",
            current_time="2026-05-16T00:00:00Z",
            total_atoms=10,
            scene_count=3,
            changed_scene_count=3,
            changed_scenes_content="test content",
        )
        assert "首次生成" in result
        assert "10" in result

    def test_incremental_mode(self):
        from services.memory.prompts.l3_persona import format_persona_prompt

        result = format_persona_prompt(
            mode="incremental",
            current_time="2026-05-16T00:00:00Z",
            total_atoms=50,
            scene_count=5,
            changed_scene_count=2,
            changed_scenes_content="new stuff",
            existing_persona="old persona",
        )
        assert "迭代更新" in result
        assert "迭代决策指南" in result
        assert "old persona" in result

    def test_system_prompt_uses_short_fact_contract(self):
        from services.memory.prompts.l3_persona import PERSONA_GENERATION_SYSTEM_PROMPT

        assert "短事实清单" in PERSONA_GENERATION_SYSTEM_PROMPT
        assert '<fact category="基本信息">' in PERSONA_GENERATION_SYSTEM_PROMPT
        assert "禁止散文/段落格式" in PERSONA_GENERATION_SYSTEM_PROMPT
        assert "不含外层 `<user_facts>`" in PERSONA_GENERATION_SYSTEM_PROMPT
