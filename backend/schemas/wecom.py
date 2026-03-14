"""
企业微信消息类型定义

两个渠道（智能机器人长连接 / 自建应用回调）共用的统一消息结构。
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class WecomIncomingMessage:
    """企微收到的用户消息（统一格式，两个渠道共用）"""

    msgid: str                              # 消息 ID（去重用）
    wecom_userid: str                       # 发送者企微用户 ID
    corp_id: str                            # 企业 ID
    chatid: str                             # 会话 ID（私聊=userid，群聊=群chatid）
    chattype: str                           # "single" | "group"
    msgtype: str                            # "text" | "image" | "voice" | "mixed"
    channel: str                            # "smart_robot" | "app"
    text_content: Optional[str] = None      # 文本内容
    image_urls: List[str] = field(default_factory=list)  # 图片 URL 列表
    raw_data: Optional[dict] = None         # 原始消息数据（调试用）


@dataclass
class WecomReplyContext:
    """回复上下文（封装不同渠道的回复方式）"""

    channel: str                            # "smart_robot" | "app"

    # 长连接模式（智能机器人）
    ws_client: Optional[Any] = None         # WecomWSClient 实例
    req_id: Optional[str] = None            # 原始请求 ID（回复必须携带）

    # 自建应用模式
    wecom_userid: Optional[str] = None      # 回复目标用户 ID
    agent_id: Optional[int] = None          # 应用 AgentID

    # 进行中的 stream（收到消息时立即创建，保持 req_id 活跃）
    active_stream_id: Optional[str] = None


# 企微 WebSocket 协议命令常量
class WecomCommand:
    """企微长连接协议命令"""

    SUBSCRIBE = "aibot_subscribe"
    PING = "ping"
    MSG_CALLBACK = "aibot_msg_callback"
    EVENT_CALLBACK = "aibot_event_callback"
    RESPOND_MSG = "aibot_respond_msg"
    RESPOND_WELCOME = "aibot_respond_welcome_msg"
    SEND_MSG = "aibot_send_msg"


# 企微消息类型常量
class WecomMsgType:
    """企微支持的消息类型"""

    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    FILE = "file"
    MIXED = "mixed"
    MARKDOWN = "markdown"
    STREAM = "stream"


# 企微聊天类型常量
class WecomChatType:
    """企微会话类型"""

    SINGLE = "single"       # 私聊
    GROUP = "group"         # 群聊
