import pytest

from services.wecom.message_normalizer import (
    WecomMessageNormalizationError,
    normalize_wecom_message,
)


def test_private_file_callback_uses_sender_as_missing_chatid() -> None:
    message = normalize_wecom_message(
        {
            "msgid": "file-msg-1",
            "from": {"userid": "WuCong"},
            "chattype": "single",
            "msgtype": "file",
            "file": {
                "url": "https://example.test/file",
                "filename": "工作簿1.csv",
                "aeskey": "secret",
            },
        },
        org_id="org-1",
        corp_id="corp-1",
    )

    assert message.chatid == "WuCong"
    assert message.file_name == "工作簿1.csv"
    assert message.file_url == "https://example.test/file"
    assert message.aeskeys == {"https://example.test/file": "secret"}


def test_official_file_callback_does_not_require_filename() -> None:
    message = normalize_wecom_message(
        {
            "msgid": "file-msg-no-name",
            "from": {"userid": "WuCong"},
            "chattype": "single",
            "msgtype": "file",
            "file": {"url": "https://example.test/file", "aeskey": "secret"},
        },
        org_id="org-1",
        corp_id="corp-1",
    )

    assert message.file_name is None
    assert message.file_url == "https://example.test/file"


def test_group_callback_requires_chatid() -> None:
    with pytest.raises(
        WecomMessageNormalizationError,
        match="WECOM_GROUP_CHATID_MISSING",
    ):
        normalize_wecom_message(
            {
                "msgid": "group-msg-1",
                "from": {"userid": "member-1"},
                "chattype": "group",
                "msgtype": "text",
                "text": {"content": "hello"},
            },
            org_id="org-1",
            corp_id="corp-1",
        )


@pytest.mark.parametrize("field", ["msgid", "sender"])
def test_callback_requires_stable_message_identity(field: str) -> None:
    body = {
        "msgid": "msg-1",
        "from": {"userid": "user-1"},
        "chattype": "single",
        "msgtype": "text",
        "text": {"content": "hello"},
    }
    if field == "msgid":
        body["msgid"] = ""
        expected = "WECOM_MSGID_MISSING"
    else:
        body["from"] = {"userid": ""}
        expected = "WECOM_SENDER_MISSING"

    with pytest.raises(WecomMessageNormalizationError, match=expected):
        normalize_wecom_message(body, org_id="org-1", corp_id="corp-1")
