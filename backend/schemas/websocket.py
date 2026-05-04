"""
统一 WebSocket 消息模型（向后兼容入口）

实际定义拆分到：
- websocket_types.py: 枚举 + Payload 模型
- websocket_builders.py: 所有 build_* 构建函数

所有外部 from schemas.websocket import ... 不需要改。
"""

# Re-export 类型定义
from schemas.websocket_types import (  # noqa: F401
    WSMessageType,
    WSBaseMessage,
    SubscribePayload,
    UnsubscribePayload,
    ClientMessage,
    MessagePendingPayload,
    MessageStartPayload,
    MessageChunkPayload,
    MessageProgressPayload,
    MessageDonePayload,
    MessageErrorPayload,
    CreditsChangedPayload,
    SubscribedPayload,
    ErrorPayload,
)

# Re-export 构建函数
from schemas.websocket_builders import (  # noqa: F401
    build_message_start,
    build_message_chunk,
    build_thinking_chunk,
    build_stream_end,
    build_message_done,
    build_message_error,
    build_message_retry,
    build_image_partial_update,
    build_subscribed,
    build_error,
    build_ping,
    build_pong,
    build_server_restarting,
    build_routing_complete,
    build_agent_step,
    build_tool_call,
    build_tool_result,
    build_tool_confirm_request,
    build_content_block_add,
)
