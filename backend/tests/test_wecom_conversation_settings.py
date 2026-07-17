from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.wecom.conversation_settings import (
    get_wecom_conversation_setting,
    set_wecom_conversation_setting,
)


def _db(row=None, rpc_result=None):
    db = MagicMock()
    query = db.table.return_value.select.return_value
    query.eq.return_value = query
    query.is_.return_value = query
    query.maybe_single.return_value.execute.return_value = SimpleNamespace(data=row)
    db.rpc.return_value.execute.return_value = SimpleNamespace(data=rpc_result)
    return db


def test_reads_model_and_thinking_from_database():
    db = _db({
        "model_id": "qwen3.5-plus",
        "chat_settings": {"thinking_mode": "deep"},
    })

    assert get_wecom_conversation_setting(db, "conv", "user", "model") == "qwen3.5-plus"
    assert get_wecom_conversation_setting(
        db, "conv", "user", "thinking_mode",
    ) == "deep"


def test_missing_conversation_setting_returns_none():
    assert get_wecom_conversation_setting(
        _db(), "conv", "user", "model",
    ) is None


def test_unknown_setting_is_rejected():
    with pytest.raises(ValueError):
        get_wecom_conversation_setting(_db(), "conv", "user", "temperature")


def test_update_uses_atomic_rpc():
    db = _db(rpc_result={
        "model_id": "qwen3.5-plus",
        "chat_settings": {"thinking_mode": "fast"},
    })

    result = set_wecom_conversation_setting(
        db, "conv", "user", "thinking_mode", "fast",
    )

    assert result["chat_settings"]["thinking_mode"] == "fast"
    assert db.rpc.call_args.args[0] == "update_wecom_conversation_setting"
    assert db.rpc.call_args.args[1]["p_org_id"] is None


def test_read_and_update_preserve_explicit_org_scope():
    db = _db(
        row={"model_id": "auto", "chat_settings": {}},
        rpc_result={"model_id": "qwen3.5-plus", "chat_settings": {}},
    )

    get_wecom_conversation_setting(db, "conv", "user", "model", "org")
    set_wecom_conversation_setting(
        db, "conv", "user", "model", "qwen3.5-plus", "org",
    )

    query = db.table.return_value.select.return_value
    assert any(call.args == ("org_id", "org") for call in query.eq.call_args_list)
    assert db.rpc.call_args.args[1]["p_org_id"] == "org"
