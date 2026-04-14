"""session_memory 单元测试 — 增量记忆提取

覆盖：
- init_session_memory: 初始化空结构 + Lock
- get_session_memory: 防御性初始化
- format_session_memory: 格式化输出 / 空返回 None
- extract_incremental: LLM 提取 + 去重 + 大小上限 + Lock 保护
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, patch

from services.handlers.session_memory import (
    _session_memory,
    _extract_lock,
    init_session_memory,
    get_session_memory,
    format_session_memory,
    extract_incremental,
)


@pytest.fixture(autouse=True)
def reset_contextvars():
    """每个测试前重置 ContextVar"""
    _session_memory.set(None)
    _extract_lock.set(None)
    yield
    _session_memory.set(None)
    _extract_lock.set(None)


# ============================================================
# init_session_memory
# ============================================================


class TestInitSessionMemory:
    def test_returns_empty_structure(self):
        mem = init_session_memory()
        assert mem == {"topics": [], "entities": [], "conclusions": [], "pending": []}

    def test_sets_contextvar(self):
        init_session_memory()
        assert _session_memory.get() is not None

    def test_creates_lock(self):
        init_session_memory()
        lock = _extract_lock.get()
        assert isinstance(lock, asyncio.Lock)

    def test_idempotent(self):
        """多次调用不报错，返回新的空结构"""
        mem1 = init_session_memory()
        mem1["topics"].append("test")
        mem2 = init_session_memory()
        assert mem2["topics"] == []  # 新的空结构


# ============================================================
# get_session_memory
# ============================================================


class TestGetSessionMemory:
    def test_after_init(self):
        init_session_memory()
        mem = get_session_memory()
        assert "topics" in mem

    def test_defensive_init(self):
        """未调 init 时自动初始化"""
        mem = get_session_memory()
        assert mem == {"topics": [], "entities": [], "conclusions": [], "pending": []}

    def test_returns_same_reference(self):
        """返回同一个 dict 引用（就地修改可见）"""
        init_session_memory()
        mem1 = get_session_memory()
        mem1["topics"].append("test")
        mem2 = get_session_memory()
        assert mem2["topics"] == ["test"]


# ============================================================
# format_session_memory
# ============================================================


class TestFormatSessionMemory:
    def test_empty_returns_none(self):
        init_session_memory()
        assert format_session_memory() is None

    def test_with_topics(self):
        init_session_memory()
        mem = get_session_memory()
        mem["topics"].append("库存查询")
        result = format_session_memory()
        assert "### 话题线索" in result
        assert "库存查询" in result

    def test_with_entities(self):
        init_session_memory()
        mem = get_session_memory()
        mem["entities"].append("订单号: 1234567890")
        result = format_session_memory()
        assert "### 关键实体" in result
        assert "1234567890" in result

    def test_all_sections(self):
        init_session_memory()
        mem = get_session_memory()
        mem["topics"].append("退货")
        mem["entities"].append("SKU-001")
        mem["conclusions"].append("已退款")
        mem["pending"].append("等发货")
        result = format_session_memory()
        assert "话题线索" in result
        assert "关键实体" in result
        assert "已确认结论" in result
        assert "待处理事项" in result


# ============================================================
# extract_incremental
# ============================================================


class TestExtractIncremental:
    @pytest.mark.asyncio
    async def test_skips_without_init(self):
        """未初始化时静默跳过"""
        await extract_incremental([{"role": "user", "content": "hello"}])
        # 不报错即通过

    @pytest.mark.asyncio
    async def test_extracts_entities(self):
        """正常提取实体"""
        init_session_memory()
        mock_response = json.dumps({
            "topics": ["库存查询"],
            "entities": ["SKU-001", "¥199.00"],
            "conclusions": ["库存充足"],
            "pending": [],
        })

        with patch(
            "services.context_summarizer._call_summary_model",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await extract_incremental([
                {"role": "user", "content": "查一下 SKU-001 的库存"},
                {"role": "tool", "content": "SKU-001 库存: 150"},
            ])

        mem = get_session_memory()
        assert "SKU-001" in mem["entities"]
        assert "库存查询" in mem["topics"]

    @pytest.mark.asyncio
    async def test_dedup(self):
        """重复实体不追加"""
        init_session_memory()
        mem = get_session_memory()
        mem["entities"].append("SKU-001")

        mock_response = json.dumps({
            "topics": [],
            "entities": ["SKU-001", "SKU-002"],
            "conclusions": [],
            "pending": [],
        })

        with patch(
            "services.context_summarizer._call_summary_model",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await extract_incremental([
                {"role": "tool", "content": "data"},
            ])

        mem = get_session_memory()
        assert mem["entities"].count("SKU-001") == 1
        assert "SKU-002" in mem["entities"]

    @pytest.mark.asyncio
    async def test_max_items_per_key(self):
        """每个章节最多 20 条"""
        init_session_memory()
        mem = get_session_memory()
        # 预填 19 条
        for i in range(19):
            mem["entities"].append(f"E-{i}")

        mock_response = json.dumps({
            "topics": [],
            "entities": ["NEW-1", "NEW-2", "NEW-3"],
            "conclusions": [],
            "pending": [],
        })

        with patch(
            "services.context_summarizer._call_summary_model",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await extract_incremental([
                {"role": "tool", "content": "data"},
            ])

        mem = get_session_memory()
        assert len(mem["entities"]) == 20  # 19 + 1（第二条超限）

    @pytest.mark.asyncio
    async def test_invalid_json_silent(self):
        """LLM 返回非 JSON 静默跳过"""
        init_session_memory()

        with patch(
            "services.context_summarizer._call_summary_model",
            new_callable=AsyncMock,
            return_value="这不是JSON",
        ):
            await extract_incremental([
                {"role": "user", "content": "hello"},
            ])

        mem = get_session_memory()
        assert mem["topics"] == []

    @pytest.mark.asyncio
    async def test_llm_failure_silent(self):
        """LLM 调用失败静默跳过"""
        init_session_memory()

        with patch(
            "services.context_summarizer._call_summary_model",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await extract_incremental([
                {"role": "user", "content": "hello"},
            ])

        mem = get_session_memory()
        assert mem["topics"] == []

    @pytest.mark.asyncio
    async def test_empty_messages_skip(self):
        """空消息列表跳过"""
        init_session_memory()
        await extract_incremental([])
        mem = get_session_memory()
        assert mem["topics"] == []

    @pytest.mark.asyncio
    async def test_multimodal_content(self):
        """多模态消息正确提取文本"""
        init_session_memory()
        mock_response = json.dumps({
            "topics": ["图片分析"],
            "entities": [],
            "conclusions": [],
            "pending": [],
        })

        with patch(
            "services.context_summarizer._call_summary_model",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await extract_incremental([
                {"role": "user", "content": [
                    {"type": "text", "text": "分析这张图"},
                ]},
            ])

        mem = get_session_memory()
        assert "图片分析" in mem["topics"]

    @pytest.mark.asyncio
    async def test_passes_existing_memory_to_llm(self):
        """已有记录传给 LLM 避免重复"""
        init_session_memory()
        mem = get_session_memory()
        mem["entities"].append("已有实体")

        with patch(
            "services.context_summarizer._call_summary_model",
            new_callable=AsyncMock,
            return_value=json.dumps({
                "topics": [], "entities": [], "conclusions": [], "pending": [],
            }),
        ) as mock_call:
            await extract_incremental([
                {"role": "user", "content": "测试"},
            ])

        # 验证 input_text 包含已有记录
        call_args = mock_call.call_args
        input_text = call_args.args[1]
        assert "已有实体" in input_text
