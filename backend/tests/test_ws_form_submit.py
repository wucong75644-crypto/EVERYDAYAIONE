"""WS form_submit 处理单测

覆盖 api/routes/ws.py 中的 _handle_form_submit：
- org_id 查找成功/失败
- handle_form_submit 调用 + 结果回传
- 异常捕获 + 错误回传
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
@patch("api.routes.ws.ws_manager")
@patch("api.routes.ws.get_db")
async def test_form_submit_success(mock_get_db, mock_ws):
    """正常路径：查到 org_id → 调用 handle_form_submit → 回传成功"""
    from api.routes.ws import _handle_form_submit

    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.eq.return_value = db
    db.limit.return_value = db
    db.execute.return_value = MagicMock(data=[{"org_id": "org-123"}])
    mock_get_db.return_value = db
    mock_ws.send_to_connection = AsyncMock()

    with patch(
        "services.scheduler.chat_task_manager.handle_form_submit",
        new_callable=AsyncMock,
        return_value={"success": True, "message": "✅ 已创建"},
    ):
        await _handle_form_submit(
            "conn-1", "user-1", "scheduled_task_create",
            {"name": "日报", "prompt": "推日报"}, "conv-1",
        )

    # 验证回传了成功结果
    call_args = mock_ws.send_to_connection.call_args[0]
    assert call_args[0] == "conn-1"
    msg = call_args[1]
    assert msg["type"] == "form_submit_result"
    assert msg["payload"]["success"] is True
    assert msg["conversation_id"] == "conv-1"


@pytest.mark.asyncio
@patch("api.routes.ws.ws_manager")
@patch("api.routes.ws.get_db")
async def test_form_submit_no_org(mock_get_db, mock_ws):
    """用户不属于任何企业 → 返回错误"""
    from api.routes.ws import _handle_form_submit

    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.eq.return_value = db
    db.limit.return_value = db
    db.execute.return_value = MagicMock(data=[])  # 无 org
    mock_get_db.return_value = db
    mock_ws.send_to_connection = AsyncMock()

    await _handle_form_submit(
        "conn-1", "user-1", "scheduled_task_create", {"name": "x"}, "conv-1",
    )

    msg = mock_ws.send_to_connection.call_args[0][1]
    assert msg["type"] == "form_submit_result"
    assert msg["payload"]["success"] is False
    assert "企业" in msg["payload"]["message"]


@pytest.mark.asyncio
@patch("api.routes.ws.ws_manager")
@patch("api.routes.ws.get_db")
async def test_form_submit_exception(mock_get_db, mock_ws):
    """handle_form_submit 抛异常 → 捕获并返回错误"""
    from api.routes.ws import _handle_form_submit

    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.eq.return_value = db
    db.limit.return_value = db
    db.execute.return_value = MagicMock(data=[{"org_id": "org-1"}])
    mock_get_db.return_value = db
    mock_ws.send_to_connection = AsyncMock()

    with patch(
        "services.scheduler.chat_task_manager.handle_form_submit",
        new_callable=AsyncMock,
        side_effect=Exception("DB connection lost"),
    ):
        await _handle_form_submit(
            "conn-1", "user-1", "scheduled_task_create", {}, "conv-1",
        )

    msg = mock_ws.send_to_connection.call_args[0][1]
    assert msg["type"] == "form_submit_result"
    assert msg["payload"]["success"] is False
    assert "失败" in msg["payload"]["message"]
