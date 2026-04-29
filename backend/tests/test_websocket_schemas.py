"""
WebSocket 消息构建函数单元测试

测试 build_image_partial_update 消息格式：
- 成功时 payload 包含 image_index/completed_count/total_count/content_part
- 失败时 payload 包含 error 字段
- 整体消息结构（type/payload/message_id/task_id/conversation_id/timestamp）
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from schemas.websocket import build_image_partial_update, build_message_done, build_thinking_chunk
from schemas.websocket_builders import build_suggestions_ready


class TestBuildImagePartialUpdate:
    """测试 build_image_partial_update 消息构建"""

    def test_success_payload_structure(self):
        """测试：成功时 payload 包含完整字段"""
        content_part = {
            "type": "image",
            "url": "https://oss/img0.png",
            "width": 1024,
            "height": 1024,
        }

        msg = build_image_partial_update(
            task_id="task_1",
            conversation_id="conv_1",
            message_id="msg_1",
            image_index=0,
            completed_count=1,
            total_count=4,
            content_part=content_part,
        )

        assert msg["type"] == "image_partial_update"
        assert msg["payload"]["image_index"] == 0
        assert msg["payload"]["completed_count"] == 1
        assert msg["payload"]["total_count"] == 4
        assert msg["payload"]["content_part"] == content_part
        assert "error" not in msg["payload"]

    def test_error_payload_structure(self):
        """测试：失败时 payload 包含 error，content_part 为 None"""
        msg = build_image_partial_update(
            task_id="task_1",
            conversation_id="conv_1",
            message_id="msg_1",
            image_index=2,
            completed_count=3,
            total_count=4,
            content_part=None,
            error="模型超时",
        )

        assert msg["type"] == "image_partial_update"
        assert msg["payload"]["image_index"] == 2
        assert msg["payload"]["content_part"] is None
        assert msg["payload"]["error"] == "模型超时"
        assert msg["payload"]["completed_count"] == 3
        assert msg["payload"]["total_count"] == 4

    def test_message_top_level_fields(self):
        """测试：顶层字段 task_id / conversation_id / message_id / timestamp"""
        msg = build_image_partial_update(
            task_id="task_abc",
            conversation_id="conv_xyz",
            message_id="msg_123",
            image_index=1,
            completed_count=2,
            total_count=2,
        )

        assert msg["task_id"] == "task_abc"
        assert msg["conversation_id"] == "conv_xyz"
        assert msg["message_id"] == "msg_123"
        assert isinstance(msg["timestamp"], int)
        assert msg["timestamp"] > 0

    def test_no_error_field_when_not_provided(self):
        """测试：不传 error 时 payload 无 error 字段"""
        msg = build_image_partial_update(
            task_id="task_1",
            conversation_id="conv_1",
            message_id="msg_1",
            image_index=0,
            completed_count=1,
            total_count=1,
            content_part={"type": "image", "url": "https://oss/img.png"},
        )

        assert "error" not in msg["payload"]

    def test_single_image_batch(self):
        """测试：单图批次（num_images=1）的消息格式"""
        content_part = {"type": "image", "url": "https://oss/single.png"}

        msg = build_image_partial_update(
            task_id="task_1",
            conversation_id="conv_1",
            message_id="msg_1",
            image_index=0,
            completed_count=1,
            total_count=1,
            content_part=content_part,
        )

        assert msg["payload"]["image_index"] == 0
        assert msg["payload"]["completed_count"] == 1
        assert msg["payload"]["total_count"] == 1

    def test_image_index_boundary_values(self):
        """测试：image_index 边界值（0 和 3）"""
        for idx in [0, 3]:
            msg = build_image_partial_update(
                task_id="task_1",
                conversation_id="conv_1",
                message_id="msg_1",
                image_index=idx,
                completed_count=idx + 1,
                total_count=4,
            )
            assert msg["payload"]["image_index"] == idx


class TestBuildMessageDone:
    """测试 build_message_done 用于多图 finalize 场景"""

    def test_message_done_with_multi_image_content(self):
        """测试：finalize 后 message_done 包含多图 content"""
        message_data = {
            "id": "msg_1",
            "content": [
                {"type": "image", "url": "https://oss/0.png", "width": 1024, "height": 1024},
                {"type": "image", "url": "https://oss/1.png", "width": 1024, "height": 1024},
                {"type": "image", "url": None, "failed": True, "error": "超时"},
                {"type": "image", "url": "https://oss/3.png", "width": 1024, "height": 1024},
            ],
            "status": "completed",
            "credits_cost": 15,
        }

        msg = build_message_done(
            task_id="task_1",
            conversation_id="conv_1",
            message=message_data,
            credits_consumed=15,
        )

        assert msg["type"] == "message_done"
        assert msg["payload"]["message"]["content"][0]["url"] == "https://oss/0.png"
        assert msg["payload"]["message"]["content"][2]["failed"] is True
        assert msg["payload"]["credits_consumed"] == 15
        assert msg["message_id"] == "msg_1"


class TestBuildThinkingChunk:
    """测试 build_thinking_chunk 消息构建"""

    def test_payload_with_chunk_and_accumulated(self):
        """测试：chunk + accumulated 均存在"""
        msg = build_thinking_chunk(
            task_id="task_1",
            conversation_id="conv_1",
            message_id="msg_1",
            chunk="让我思考",
            accumulated="让我思考",
        )

        assert msg["type"] == "thinking_chunk"
        assert msg["payload"]["chunk"] == "让我思考"
        assert msg["payload"]["accumulated"] == "让我思考"
        assert msg["task_id"] == "task_1"
        assert msg["conversation_id"] == "conv_1"
        assert msg["message_id"] == "msg_1"

    def test_payload_without_accumulated(self):
        """测试：不传 accumulated 时 payload 无该字段"""
        msg = build_thinking_chunk(
            task_id="task_1",
            conversation_id="conv_1",
            message_id="msg_1",
            chunk="一下",
        )

        assert msg["payload"]["chunk"] == "一下"
        assert "accumulated" not in msg["payload"]

    def test_timestamp_is_valid(self):
        """测试：timestamp 为正整数"""
        msg = build_thinking_chunk(
            task_id="t", conversation_id="c",
            message_id="m", chunk="x",
        )
        assert isinstance(msg["timestamp"], int)
        assert msg["timestamp"] > 0


class TestBuildMessageDoneThinkingContent:
    """测试 build_message_done 对 generation_params 的透传"""

    def test_message_done_preserves_gen_params(self):
        """测试：message 中 generation_params 字段被原样透传（thinking 已改为 ThinkingPart 存 content）"""
        message_data = {
            "id": "msg_t1",
            "content": [{"type": "thinking", "text": "推理过程"}, {"type": "text", "text": "回答"}],
            "generation_params": {"tool_digest": {"tools": []}},
            "status": "completed",
        }

        msg = build_message_done(
            task_id="task_1",
            conversation_id="conv_1",
            message=message_data,
        )

        gen_params = msg["payload"]["message"]["generation_params"]
        assert gen_params["tool_digest"] == {"tools": []}
        # thinking_content 不再写入 generation_params（ThinkingPart 在 content 中）
        assert "thinking_content" not in gen_params


class TestBuildRoutingComplete:
    """测试 build_routing_complete 消息构建"""

    def test_basic_structure(self):
        from schemas.websocket import build_routing_complete

        msg = build_routing_complete(
            task_id="t1",
            conversation_id="c1",
            generation_type="image",
            model="flux-1",
        )
        assert msg["type"] == "routing_complete"
        assert msg["task_id"] == "t1"
        assert msg["conversation_id"] == "c1"
        assert msg["payload"]["generation_type"] == "image"
        assert msg["payload"]["model"] == "flux-1"
        assert "timestamp" in msg

    def test_with_message_id(self):
        from schemas.websocket import build_routing_complete

        msg = build_routing_complete(
            task_id="t1",
            conversation_id="c1",
            generation_type="chat",
            model="gemini-3-pro",
            message_id="msg-123",
        )
        assert msg["message_id"] == "msg-123"

    def test_with_generation_params(self):
        from schemas.websocket import build_routing_complete

        gen_params = {"type": "image", "model": "flux-1", "aspect_ratio": "16:9"}
        msg = build_routing_complete(
            task_id="t1",
            conversation_id="c1",
            generation_type="image",
            model="flux-1",
            generation_params=gen_params,
        )
        assert msg["payload"]["generation_params"] == gen_params

    def test_without_optional_fields(self):
        from schemas.websocket import build_routing_complete

        msg = build_routing_complete(
            task_id="t1",
            conversation_id="c1",
            generation_type="chat",
            model="gemini-3-pro",
        )
        assert "message_id" not in msg
        assert "generation_params" not in msg["payload"]


class TestBuildAgentStepWithTaskId:
    """测试 build_agent_step 的 task_id 参数"""

    def test_without_task_id(self):
        from schemas.websocket import build_agent_step

        msg = build_agent_step(
            conversation_id="c1",
            tool_name="kuaimai_search",
            status="executing",
            turn=1,
        )
        assert msg["type"] == "agent_step"
        assert msg["conversation_id"] == "c1"
        assert "task_id" not in msg

    def test_with_task_id(self):
        from schemas.websocket import build_agent_step

        msg = build_agent_step(
            conversation_id="c1",
            tool_name="kuaimai_search",
            status="executing",
            turn=1,
            task_id="task-abc",
        )
        assert msg["task_id"] == "task-abc"
        assert msg["payload"]["tool_name"] == "kuaimai_search"


class TestBuildAgentStepProgressFields:
    """E1+E2: build_agent_step 进度和预估字段"""

    def test_progress_field_with_max_turns(self):
        from schemas.websocket import build_agent_step
        msg = build_agent_step(
            conversation_id="c1", tool_name="tool", status="running",
            turn=3, max_turns=20,
        )
        assert msg["payload"]["progress"] == "3/20"

    def test_no_progress_without_max_turns(self):
        from schemas.websocket import build_agent_step
        msg = build_agent_step(
            conversation_id="c1", tool_name="tool", status="running", turn=1,
        )
        assert "progress" not in msg["payload"]

    def test_elapsed_s_field(self):
        from schemas.websocket import build_agent_step
        msg = build_agent_step(
            conversation_id="c1", tool_name="tool", status="running",
            turn=1, elapsed_s=12.345,
        )
        assert msg["payload"]["elapsed_s"] == 12.3

    def test_tools_completed_field(self):
        from schemas.websocket import build_agent_step
        msg = build_agent_step(
            conversation_id="c1", tool_name="tool", status="running",
            turn=2, tools_completed=["local_stock_query", "local_order_query"],
        )
        assert msg["payload"]["tools_completed"] == ["local_stock_query", "local_order_query"]

    def test_estimated_s_field(self):
        from schemas.websocket import build_agent_step
        msg = build_agent_step(
            conversation_id="c1", tool_name="tool", status="running",
            turn=1, estimated_s=51,
        )
        assert msg["payload"]["estimated_s"] == 51

    def test_no_optional_fields_when_omitted(self):
        from schemas.websocket import build_agent_step
        msg = build_agent_step(
            conversation_id="c1", tool_name="tool", status="running", turn=1,
        )
        payload = msg["payload"]
        assert "progress" not in payload
        assert "elapsed_s" not in payload
        assert "tools_completed" not in payload
        assert "estimated_s" not in payload
        # 基础字段仍在
        assert payload["tool_name"] == "tool"
        assert payload["turn"] == 1


class TestBuildSuggestionsReady:
    """测试 build_suggestions_ready 消息构建"""

    def test_payload_structure(self):
        """payload 包含 suggestions 数组"""
        msg = build_suggestions_ready(
            conversation_id="conv_1",
            suggestions=["按店铺分析", "和前天对比"],
        )
        assert msg["type"] == "suggestions_ready"
        assert msg["payload"]["suggestions"] == ["按店铺分析", "和前天对比"]
        assert msg["conversation_id"] == "conv_1"

    def test_no_task_id(self):
        """suggestions_ready 不绑定 task_id"""
        msg = build_suggestions_ready("conv_1", ["建议"])
        assert msg.get("task_id") is None

    def test_has_timestamp(self):
        """消息包含时间戳"""
        msg = build_suggestions_ready("conv_1", ["建议"])
        assert "timestamp" in msg
        assert isinstance(msg["timestamp"], int)

    def test_empty_suggestions_still_valid(self):
        """空列表仍然构建合法消息（前端 guard 不渲染）"""
        msg = build_suggestions_ready("conv_1", [])
        assert msg["payload"]["suggestions"] == []


class TestBuildAskUserRequest:
    """测试 build_ask_user_request 消息构建"""

    def test_basic_structure(self):
        """基本字段齐全"""
        from schemas.websocket_builders import build_ask_user_request

        msg = build_ask_user_request(
            task_id="t1",
            conversation_id="c1",
            message_id="m1",
            interaction_id="int_1",
            question="请问你要查哪个店铺？",
        )
        assert msg["type"] == "ask_user_request"
        assert msg["task_id"] == "t1"
        assert msg["conversation_id"] == "c1"
        assert msg["message_id"] == "m1"
        assert msg["payload"]["interaction_id"] == "int_1"
        assert msg["payload"]["question"] == "请问你要查哪个店铺？"
        assert "timestamp" in msg

    def test_with_options(self):
        """传入 options 时包含在 payload 中"""
        from schemas.websocket_builders import build_ask_user_request

        msg = build_ask_user_request(
            task_id="t1",
            conversation_id="c1",
            message_id="m1",
            interaction_id="int_2",
            question="选择时间范围",
            options=["今天", "昨天", "最近7天"],
        )
        assert msg["payload"]["options"] == ["今天", "昨天", "最近7天"]

    def test_without_options(self):
        """不传 options 时 payload 无该字段"""
        from schemas.websocket_builders import build_ask_user_request

        msg = build_ask_user_request(
            task_id="t1",
            conversation_id="c1",
            message_id="m1",
            interaction_id="int_3",
            question="你想查什么？",
        )
        assert "options" not in msg["payload"]

    def test_default_timeout(self):
        """默认 timeout 为 86400"""
        from schemas.websocket_builders import build_ask_user_request

        msg = build_ask_user_request(
            task_id="t1", conversation_id="c1",
            message_id="m1", interaction_id="int_4",
            question="q",
        )
        assert msg["payload"]["timeout"] == 86400

    def test_custom_source(self):
        """自定义 source 参数"""
        from schemas.websocket_builders import build_ask_user_request

        msg = build_ask_user_request(
            task_id="t1", conversation_id="c1",
            message_id="m1", interaction_id="int_5",
            question="q", source="erp_agent",
        )
        assert msg["payload"]["source"] == "erp_agent"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
