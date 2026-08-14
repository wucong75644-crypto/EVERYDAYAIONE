from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from schemas.wecom import (
    WecomChatType,
    WecomIncomingMessage,
    WecomMsgType,
    WecomReplyContext,
)


def make_db_mock():
    """Build a DB mock with independent query builders per table."""
    db = MagicMock()
    table_mocks: dict[str, MagicMock] = {}

    def table(name: str):
        if name not in table_mocks:
            table_mocks[name] = MagicMock(name=f"table({name})")
        return table_mocks[name]

    db.table = MagicMock(side_effect=table)
    db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock()))
    db._table_mocks = table_mocks
    return db


def make_message(
    msgtype: str = WecomMsgType.TEXT,
    text: str = "你好",
    channel: str = "smart_robot",
) -> WecomIncomingMessage:
    return WecomIncomingMessage(
        msgid="msg001",
        wecom_userid="user_abc",
        corp_id="corp1",
        chatid="user_abc",
        chattype=WecomChatType.SINGLE,
        msgtype=msgtype,
        channel=channel,
        text_content=text,
    )


def make_reply_context(channel: str = "smart_robot") -> WecomReplyContext:
    if channel == "smart_robot":
        return WecomReplyContext(
            channel="smart_robot",
            ws_client=AsyncMock(),
            req_id="req001",
        )
    return WecomReplyContext(
        channel="app",
        wecom_userid="user_abc",
        agent_id=1000006,
        org_id="org_test",
        corp_id="corp_test",
        agent_secret="secret_test",
    )
