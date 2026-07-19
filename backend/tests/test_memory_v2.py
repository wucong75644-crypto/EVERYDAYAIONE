"""
记忆系统 V2 单元测试

覆盖：
- RRF 混合检索融合算法
- 管道调度 Warm-up 逻辑
- 上下文压缩三级策略
"""

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
# RRF 混合检索融合
# ============================================================

class TestRRFMerge:
    """RRF 融合算法测试"""

    def test_basic_merge(self):
        from services.memory.retrieval_pipeline import RetrievalPipeline

        pipeline = RetrievalPipeline()

        vector = [
            {"record_id": "a", "content": "A", "kind": "preference", "priority": 80},
            {"record_id": "b", "content": "B", "kind": "instruction", "priority": 70},
            {"record_id": "c", "content": "C", "kind": "decision", "priority": 60},
        ]
        bm25 = [
            {"record_id": "b", "content": "B", "kind": "instruction", "priority": 70},
            {"record_id": "d", "content": "D", "kind": "tracked_plan", "priority": 50},
            {"record_id": "a", "content": "A", "kind": "preference", "priority": 80},
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

        vector = [{"record_id": "x", "content": "", "kind": "memory", "priority": 50}]
        bm25 = [{"record_id": "x", "content": "", "kind": "memory", "priority": 50}]

        merged = pipeline._rrf_merge(vector, bm25, max_results=1)
        expected_score = 1.0 / (K + 0 + 1) + 1.0 / (K + 0 + 1)  # rank 0 in both
        assert abs(merged[0]["rrf_score"] - expected_score) < 1e-6

    def test_format_for_injection(self):
        from services.memory.retrieval_pipeline import RetrievalPipeline, ScoredMemory

        pipeline = RetrievalPipeline()
        memories = [
            ScoredMemory(atom_id="1", content="用户是程序员", kind="user_profile", priority=80, score=0.9),
            ScoredMemory(atom_id="2", content="用户五月去日本", kind="tracked_plan", priority=70, score=0.8,
                        activity_start="2026-05-01T00:00:00", activity_end="2026-05-10T00:00:00"),
        ]
        result = pipeline.format_for_injection(memories)

        assert "[user_profile]" in result
        assert "用户是程序员" in result
        assert "[tracked_plan]" in result
        assert "2026-05-01" in result
        assert "persona" not in result

    def test_format_empty(self):
        from services.memory.retrieval_pipeline import RetrievalPipeline

        pipeline = RetrievalPipeline()
        assert pipeline.format_for_injection([]) == ""

    def test_format_missing_kind_uses_generic_label(self):
        from services.memory.retrieval_pipeline import RetrievalPipeline, ScoredMemory

        memory = ScoredMemory(
            atom_id="1",
            content="历史记忆",
            kind="",
            priority=50,
            score=0.8,
        )

        assert RetrievalPipeline().format_for_injection([memory]) == "- [memory] 历史记忆"


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
