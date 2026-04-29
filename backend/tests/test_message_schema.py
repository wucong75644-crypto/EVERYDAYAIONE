"""
ThinkingPart / ToolStepPart schema 单元测试

覆盖：序列化、反序列化、discriminator 路由、可选字段省略、截断边界
"""

import sys
from pathlib import Path

backend_dir = str(Path(__file__).resolve().parent.parent)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import pytest
from schemas.message import (
    ThinkingPart,
    ToolStepPart,
    ContentPart,
    TextPart,
)
from pydantic import TypeAdapter


# ============================================================
# ThinkingPart
# ============================================================

class TestThinkingPart:

    def test_basic_serialization(self):
        """ThinkingPart 序列化包含 type/text/duration_ms"""
        p = ThinkingPart(text="推理过程", duration_ms=1234)
        d = p.model_dump()
        assert d == {"type": "thinking", "text": "推理过程", "duration_ms": 1234}

    def test_serialization_excludes_none(self):
        """duration_ms 为 None 时 exclude_none 省略"""
        p = ThinkingPart(text="推理")
        d = p.model_dump(exclude_none=True)
        assert "duration_ms" not in d
        assert d["type"] == "thinking"

    def test_empty_text_allowed(self):
        """空字符串 text 合法（流式阶段可能首 chunk 为空）"""
        p = ThinkingPart(text="")
        assert p.text == ""

    def test_type_literal_is_thinking(self):
        """type 字段固定为 'thinking'"""
        p = ThinkingPart(text="x")
        assert p.type == "thinking"


# ============================================================
# ToolStepPart
# ============================================================

class TestToolStepPart:

    def test_basic_serialization(self):
        """ToolStepPart 完整字段序列化"""
        p = ToolStepPart(
            tool_name="code_execute",
            tool_call_id="tc_1",
            status="completed",
            summary="图表已生成",
            code="print(1)",
            output="1",
            elapsed_ms=5000,
        )
        d = p.model_dump()
        assert d["type"] == "tool_step"
        assert d["tool_name"] == "code_execute"
        assert d["tool_call_id"] == "tc_1"
        assert d["status"] == "completed"
        assert d["code"] == "print(1)"
        assert d["output"] == "1"
        assert d["elapsed_ms"] == 5000

    def test_minimal_running_step(self):
        """running 状态只需 tool_name + tool_call_id"""
        p = ToolStepPart(tool_name="web_search", tool_call_id="tc_2")
        d = p.model_dump(exclude_none=True)
        assert d == {
            "type": "tool_step",
            "tool_name": "web_search",
            "tool_call_id": "tc_2",
            "status": "running",
        }

    def test_error_status(self):
        """error 状态序列化"""
        p = ToolStepPart(
            tool_name="erp_agent",
            tool_call_id="tc_3",
            status="error",
            summary="超时",
            elapsed_ms=30000,
        )
        assert p.status == "error"
        assert p.summary == "超时"

    def test_type_literal_is_tool_step(self):
        """type 字段固定为 'tool_step'"""
        p = ToolStepPart(tool_name="x", tool_call_id="y")
        assert p.type == "tool_step"


# ============================================================
# ContentPart discriminator 路由
# ============================================================

class TestContentPartDiscriminator:
    """验证 ContentPart Union 正确按 type 字段反序列化到对应类"""

    adapter = TypeAdapter(ContentPart)

    def test_thinking_discriminator(self):
        """type=thinking 反序列化为 ThinkingPart"""
        obj = self.adapter.validate_python({"type": "thinking", "text": "hi"})
        assert isinstance(obj, ThinkingPart)
        assert obj.text == "hi"

    def test_tool_step_discriminator(self):
        """type=tool_step 反序列化为 ToolStepPart"""
        obj = self.adapter.validate_python({
            "type": "tool_step",
            "tool_name": "code_execute",
            "tool_call_id": "tc_1",
            "status": "completed",
        })
        assert isinstance(obj, ToolStepPart)
        assert obj.tool_name == "code_execute"

    def test_text_discriminator_still_works(self):
        """type=text 仍正确反序列化为 TextPart（向后兼容）"""
        obj = self.adapter.validate_python({"type": "text", "text": "hello"})
        assert isinstance(obj, TextPart)

    def test_mixed_content_list(self):
        """混合类型列表正确反序列化"""
        from typing import List
        list_adapter = TypeAdapter(List[ContentPart])
        data = [
            {"type": "thinking", "text": "推理", "duration_ms": 500},
            {"type": "tool_step", "tool_name": "web_search", "tool_call_id": "tc_1", "status": "running"},
            {"type": "text", "text": "回答"},
        ]
        parts = list_adapter.validate_python(data)
        assert len(parts) == 3
        assert isinstance(parts[0], ThinkingPart)
        assert isinstance(parts[1], ToolStepPart)
        assert isinstance(parts[2], TextPart)
