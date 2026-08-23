"""
ThinkingPart / ToolStepPart schema 单元测试

覆盖：序列化、反序列化、discriminator 路由、可选字段省略、截断边界
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

backend_dir = str(Path(__file__).resolve().parent.parent)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import pytest
from schemas.message import (
    ThinkingPart,
    ToolStepPart,
    ContentPart,
    DiagramPart,
    Message,
    MessageResponse,
    MessageRole,
    MessageStatus,
    ImagePart,
    TextPart,
    serialize_content_part,
)
from pydantic import TypeAdapter
from unittest.mock import MagicMock


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
            input='{"code":"print(1)"}',
            code="print(1)",
            output="1",
            elapsed_ms=5000,
        )
        d = p.model_dump()
        assert d["type"] == "tool_step"
        assert d["tool_name"] == "code_execute"
        assert d["tool_call_id"] == "tc_1"
        assert d["status"] == "completed"
        assert d["input"] == '{"code":"print(1)"}'
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
            output="超时",
            elapsed_ms=30000,
        )
        assert p.status == "error"
        assert p.output == "超时"

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

    def test_diagram_discriminator(self):
        obj = self.adapter.validate_python({
            "type": "diagram",
            "format": "mermaid",
            "source": "flowchart TD\nA-->B",
            "title": "流程",
        })
        assert isinstance(obj, DiagramPart)
        assert obj.source == "flowchart TD\nA-->B"

    @pytest.mark.parametrize("source", ["", " \n "])
    def test_diagram_rejects_empty_source(self, source):
        with pytest.raises(ValueError):
            self.adapter.validate_python({
                "type": "diagram",
                "format": "mermaid",
                "source": source,
            })

    def test_diagram_rejects_unknown_format(self):
        with pytest.raises(ValueError):
            self.adapter.validate_python({
                "type": "diagram",
                "format": "plantuml",
                "source": "@startuml",
            })

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


def test_message_response_preserves_turn_relationship_fields():
    """列表/搜索响应不能丢失正式 Turn 与上下文 revision。"""
    message = Message(
        id="00000000-0000-0000-0000-000000000002",
        conversation_id="00000000-0000-0000-0000-000000000001",
        role=MessageRole.ASSISTANT,
        content=[TextPart(text="完成")],
        turn_id="00000000-0000-0000-0000-000000000003",
        reply_to_message_id="00000000-0000-0000-0000-000000000004",
        context_revision=7,
        message_kind="conversation",
        created_at=datetime.now(timezone.utc),
    )

    response = MessageResponse.from_message(message)

    assert response.turn_id == message.turn_id
    assert response.reply_to_message_id == message.reply_to_message_id
    assert response.context_revision == 7
    assert response.message_kind == "conversation"


def test_message_response_accepts_interrupted_status_for_refresh():
    """暂停后的历史消息必须能通过 API 响应模型校验。"""
    message = Message(
        id="msg-interrupted",
        conversation_id="conv-interrupted",
        role=MessageRole.ASSISTANT,
        content=[TextPart(text="已生成的部分")],
        status=MessageStatus.INTERRUPTED,
        created_at=datetime.now(timezone.utc),
    )

    response = MessageResponse.from_message(message)

    assert response.status == MessageStatus.INTERRUPTED


def test_image_content_serialization_omits_absent_optional_metadata():
    """User image blocks survive the API/DB wire boundary without null-shape loss."""
    part = ImagePart(url="https://oss.example/input.png")

    assert serialize_content_part(part) == {
        "type": "image",
        "url": "https://oss.example/input.png",
    }
    response = MessageResponse.from_message(Message(
        id="msg-image",
        conversation_id="conv-image",
        role=MessageRole.USER,
        content=[part],
        created_at=datetime.now(timezone.utc),
    ))

    assert response.content == [{
        "type": "image",
        "url": "https://oss.example/input.png",
    }]


@pytest.mark.asyncio
async def test_create_user_message_persists_image_without_nullable_metadata():
    """The user-message DB write must use the same canonical wire shape."""
    from api.routes.message_helpers import create_user_message

    db = MagicMock()
    db.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": "msg-image",
        "conversation_id": "conv-image",
        "role": "user",
        "status": "completed",
        "created_at": "2026-08-19T00:00:00+00:00",
    }]

    await create_user_message(
        db=db,
        conversation_id="conv-image",
        content=[ImagePart(url="https://oss.example/input.png")],
    )

    inserted = db.table.return_value.insert.call_args.args[0]
    assert inserted["content"] == [{
        "type": "image",
        "url": "https://oss.example/input.png",
    }]
