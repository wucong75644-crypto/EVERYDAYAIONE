"""Conversation Actor Worker 配置测试。"""

from core.config import Settings


def test_web_actor_has_no_legacy_routing_flag():
    assert "conversation_actor_web_enabled" not in Settings.model_fields
    assert Settings.model_fields[
        "conversation_actor_worker_enabled"
    ].default is False
