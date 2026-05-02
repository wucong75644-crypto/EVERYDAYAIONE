"""
孤儿任务恢复测试

覆盖：有内容恢复、无内容标记失败、无 message_id 跳过、upsert 幂等性
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import MagicMock
from services.task_recovery import recover_orphan_tasks, _mark_task_failed


def _mock_db(tasks: list):
    """构建 mock db，模拟 tasks 查询和 messages/tasks 写入"""
    db = MagicMock()

    # tasks.select().in_().execute()
    select_response = MagicMock()
    select_response.data = tasks
    db.table.return_value.select.return_value.in_.return_value.execute.return_value = select_response

    # messages.upsert().execute() 和 tasks.update().eq().execute()
    upsert_response = MagicMock()
    upsert_response.data = [{"id": "msg-1"}]
    db.table.return_value.upsert.return_value.execute.return_value = upsert_response

    update_response = MagicMock()
    update_response.data = [{"id": "task-1"}]
    db.table.return_value.update.return_value.eq.return_value.execute.return_value = update_response

    return db


@pytest.mark.asyncio
async def test_recover_task_with_accumulated_content():
    """有 accumulated_content 的任务应恢复到 messages 表"""
    tasks = [{
        "id": "task-1",
        "type": "chat",
        "external_task_id": "ext-1",
        "placeholder_message_id": "msg-1",
        "conversation_id": "conv-1",
        "model_id": "gpt-4",
        "client_task_id": "client-1",
        "accumulated_content": "这是部分生成的内容",
        "credit_transaction_id": None,
    }]
    db = _mock_db(tasks)
    result = await recover_orphan_tasks(db)
    assert result == 1

    # 验证 messages upsert 被调用
    upsert_calls = [
        c for c in db.table.return_value.upsert.call_args_list
    ]
    assert len(upsert_calls) >= 1
    upsert_data = upsert_calls[0][0][0]
    assert upsert_data["id"] == "msg-1"
    assert upsert_data["content"] == [{"type": "text", "text": "这是部分生成的内容"}]
    assert upsert_data["status"] == "completed"
    assert upsert_data["credits_cost"] == 0
    assert upsert_data["generation_params"]["type"] == "chat"


@pytest.mark.asyncio
async def test_recover_task_preserves_task_type():
    """恢复时 generation_params.type 应使用任务的实际类型，而非硬编码 chat"""
    tasks = [{
        "id": "task-img",
        "type": "image",
        "external_task_id": "ext-img",
        "placeholder_message_id": "msg-img",
        "conversation_id": "conv-1",
        "model_id": "flux-pro",
        "client_task_id": "client-img",
        "accumulated_content": "部分内容",
        "credit_transaction_id": None,
    }]
    db = _mock_db(tasks)
    result = await recover_orphan_tasks(db)
    assert result == 1

    upsert_data = db.table.return_value.upsert.call_args[0][0]
    assert upsert_data["generation_params"]["type"] == "image"
    assert upsert_data["generation_params"]["model"] == "flux-pro"


@pytest.mark.asyncio
async def test_recover_task_type_defaults_to_chat():
    """任务无 type 字段时默认 chat"""
    tasks = [{
        "id": "task-no-type",
        "external_task_id": "ext-1",
        "placeholder_message_id": "msg-1",
        "conversation_id": "conv-1",
        "model_id": "gpt-4",
        "client_task_id": "client-1",
        "accumulated_content": "内容",
        "credit_transaction_id": None,
    }]
    db = _mock_db(tasks)
    await recover_orphan_tasks(db)

    upsert_data = db.table.return_value.upsert.call_args[0][0]
    assert upsert_data["generation_params"]["type"] == "chat"


@pytest.mark.asyncio
async def test_skip_task_without_accumulated_content():
    """无 accumulated_content 的任务应标记为 failed，不恢复"""
    tasks = [{
        "id": "task-2",
        "external_task_id": "ext-2",
        "placeholder_message_id": "msg-2",
        "conversation_id": "conv-2",
        "model_id": "gpt-4",
        "client_task_id": "client-2",
        "accumulated_content": "",
        "credit_transaction_id": None,
    }]
    db = _mock_db(tasks)
    result = await recover_orphan_tasks(db)
    assert result == 0


@pytest.mark.asyncio
async def test_skip_task_without_message_id():
    """无 placeholder_message_id 的任务应跳过"""
    tasks = [{
        "id": "task-3",
        "external_task_id": "ext-3",
        "placeholder_message_id": None,
        "conversation_id": "conv-3",
        "model_id": "gpt-4",
        "client_task_id": "client-3",
        "accumulated_content": "有内容但无message_id",
        "credit_transaction_id": None,
    }]
    db = _mock_db(tasks)
    result = await recover_orphan_tasks(db)
    assert result == 0


@pytest.mark.asyncio
async def test_no_orphan_tasks():
    """无孤儿任务时返回 0"""
    db = _mock_db([])
    result = await recover_orphan_tasks(db)
    assert result == 0


@pytest.mark.asyncio
async def test_db_query_error():
    """数据库查询失败时返回 0 不抛异常"""
    db = MagicMock()
    db.table.return_value.select.return_value.in_.return_value.execute.side_effect = Exception("DB error")
    result = await recover_orphan_tasks(db)
    assert result == 0


@pytest.mark.asyncio
async def test_multiple_tasks_mixed():
    """混合场景：一个有内容可恢复，一个无内容标记失败"""
    tasks = [
        {
            "id": "task-ok",
            "external_task_id": "ext-ok",
            "placeholder_message_id": "msg-ok",
            "conversation_id": "conv-1",
            "model_id": "gemini",
            "client_task_id": "client-ok",
            "accumulated_content": "部分内容",
            "credit_transaction_id": None,
        },
        {
            "id": "task-empty",
            "external_task_id": "ext-empty",
            "placeholder_message_id": "msg-empty",
            "conversation_id": "conv-2",
            "model_id": "gemini",
            "client_task_id": "client-empty",
            "accumulated_content": None,
            "credit_transaction_id": None,
        },
    ]
    db = _mock_db(tasks)
    result = await recover_orphan_tasks(db)
    assert result == 1


@pytest.mark.asyncio
async def test_recover_task_with_accumulated_blocks():
    """有 accumulated_blocks 的任务应合并 blocks + 剩余文字恢复到 messages 表"""
    tasks = [{
        "id": "task-blocks",
        "type": "chat",
        "external_task_id": "ext-blocks",
        "placeholder_message_id": "msg-blocks",
        "conversation_id": "conv-1",
        "model_id": "gemini-pro",
        "client_task_id": "client-blocks",
        "accumulated_content": "分析中最终回答",
        "accumulated_blocks": [
            {"type": "text", "text": "分析中"},
            {"type": "tool_step", "tool_name": "data_query", "status": "completed"},
        ],
        "credit_transaction_id": None,
    }]
    db = _mock_db(tasks)
    result = await recover_orphan_tasks(db)
    assert result == 1

    upsert_data = db.table.return_value.upsert.call_args[0][0]
    # blocks + 剩余文字 = 3 个 content part
    assert len(upsert_data["content"]) == 3
    assert upsert_data["content"][0] == {"type": "text", "text": "分析中"}
    assert upsert_data["content"][1]["type"] == "tool_step"
    assert upsert_data["content"][2] == {"type": "text", "text": "最终回答"}


@pytest.mark.asyncio
async def test_recover_task_without_blocks_fallback():
    """无 accumulated_blocks 的旧任务仍走纯文字恢复（向后兼容）"""
    tasks = [{
        "id": "task-old",
        "type": "chat",
        "external_task_id": "ext-old",
        "placeholder_message_id": "msg-old",
        "conversation_id": "conv-1",
        "model_id": "gpt-4",
        "client_task_id": "client-old",
        "accumulated_content": "纯文字内容",
        "credit_transaction_id": None,
        # 没有 accumulated_blocks 字段
    }]
    db = _mock_db(tasks)
    result = await recover_orphan_tasks(db)
    assert result == 1

    upsert_data = db.table.return_value.upsert.call_args[0][0]
    assert upsert_data["content"] == [{"type": "text", "text": "纯文字内容"}]


def test_mark_task_failed():
    """_mark_task_failed 应更新任务状态"""
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    task = {"id": "task-x", "credit_transaction_id": None}
    _mark_task_failed(db, task, error_msg="test error")
    db.table.assert_called_with("tasks")


def test_mark_task_failed_refunds_credits():
    """_mark_task_failed 有 credit_transaction_id 时应退积分"""
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    refund_response = MagicMock()
    refund_response.data = {"refunded": True, "user_id": "u1", "amount": 10}
    db.rpc.return_value.execute.return_value = refund_response

    task = {"id": "task-y", "credit_transaction_id": "tx-123"}
    _mark_task_failed(db, task, error_msg="test error")

    # 验证 rpc 退积分被调用
    db.rpc.assert_called_once_with(
        'atomic_refund_credits',
        {'p_transaction_id': 'tx-123'}
    )


@pytest.mark.asyncio
async def test_orphan_without_content_refunds_credits():
    """无内容孤儿任务应退积分"""
    tasks = [{
        "id": "task-refund",
        "external_task_id": "ext-refund",
        "placeholder_message_id": "msg-refund",
        "conversation_id": "conv-1",
        "model_id": "gpt-4",
        "client_task_id": "client-refund",
        "accumulated_content": "",
        "credit_transaction_id": "tx-456",
    }]
    db = _mock_db(tasks)
    refund_response = MagicMock()
    refund_response.data = {"refunded": True, "user_id": "u1", "amount": 5}
    db.rpc.return_value.execute.return_value = refund_response

    result = await recover_orphan_tasks(db)
    assert result == 0

    # 验证退积分被调用
    db.rpc.assert_called_once_with(
        'atomic_refund_credits',
        {'p_transaction_id': 'tx-456'}
    )
