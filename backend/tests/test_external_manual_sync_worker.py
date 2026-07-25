"""Durable external manual Sync consumer tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.kuaimai_external.manual_worker import _execute_claim


def _caller(data):
    caller = MagicMock()
    caller.execute = AsyncMock(return_value=MagicMock(data=data))
    return caller


@pytest.mark.asyncio
async def test_success_finishes_with_same_fencing_token():
    db = MagicMock()
    db.rpc.side_effect = lambda name, params=None: _caller(
        True if name == "sync_finish_external_sync" else None
    )
    request = {"id": "req-1", "execution_token": "token-1"}
    with patch(
        "services.kuaimai_external.manual_worker._run_sync",
        new_callable=AsyncMock,
    ):
        await _execute_claim(db, request)

    finish = [
        call for call in db.rpc.call_args_list
        if call.args[0] == "sync_finish_external_sync"
    ][0]
    assert finish.args[1]["p_request_id"] == "req-1"
    assert finish.args[1]["p_execution_token"] == "token-1"
    assert finish.args[1]["p_success"] is True


@pytest.mark.asyncio
async def test_failure_is_persisted_without_escaping_consumer():
    db = MagicMock()
    db.rpc.side_effect = lambda name, params=None: _caller(
        True if name == "sync_finish_external_sync" else None
    )
    request = {"id": "req-2", "execution_token": "token-2"}
    with patch(
        "services.kuaimai_external.manual_worker._run_sync",
        new_callable=AsyncMock,
        side_effect=RuntimeError("provider failed"),
    ):
        await _execute_claim(db, request)

    finish = [
        call for call in db.rpc.call_args_list
        if call.args[0] == "sync_finish_external_sync"
    ][0]
    assert finish.args[1]["p_success"] is False
    assert finish.args[1]["p_error_message"] == "provider failed"
