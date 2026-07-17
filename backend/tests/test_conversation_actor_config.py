"""Conversation Actor 灰度开关测试。"""

from core.config import Settings


def test_web_actor_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CONVERSATION_ACTOR_WEB_ENABLED", raising=False)

    assert Settings.model_fields[
        "conversation_actor_web_enabled"
    ].default is False
    assert Settings.model_fields[
        "conversation_actor_worker_enabled"
    ].default is False
    assert Settings.model_fields[
        "conversation_actor_wecom_enabled"
    ].default is False
