"""Replay the authorized real model output through the creation boundary."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.scheduler.task_nl_parser import parse_task_request
from services.scheduler.chat_task_manager import ChatTaskManager
from tests.test_scheduled_task_request_content import BUSINESS, TARGETS


EVIDENCE = Path(__file__).resolve().parents[2] / "docs/document/scheduled-task-colloquial-evidence"
RECORD = json.loads((EVIDENCE / "before.json").read_text())


@pytest.mark.parametrize("clock", ["八点", "八点钟", "8点钟", "早晨八点钟", "晚上八点钟"])
@pytest.mark.parametrize("delivery", ["发给我", "发给我看", "发给我看看", "发送给我看一下", "推送给我查收"])
async def test_clock_and_delivery_grammar_preserve_business(clock, delivery):
    raw = deepcopy(RECORD["raw"])
    raw["changes"]["time_str"] = "20:00" if clock.startswith("晚上") else "08:00"
    raw["evidence"]["time_str"] = clock
    raw["request_parts"][2]["text"] = "每天" + clock
    raw["request_parts"][3]["text"] = delivery
    text = "创建一个定时任务。" + BUSINESS + "，每天" + clock + delivery
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(text)
    assert result["changes"]["prompt"] == BUSINESS
    assert result["changes"]["time_str"] == raw["changes"]["time_str"]
    assert result["missing_fields"] == []


@pytest.mark.parametrize("short_part", [False, True])
async def test_short_clock_evidence_and_omitted_grammatical_suffix(short_part):
    raw = deepcopy(RECORD["raw"])
    raw["evidence"]["time_str"] = "八点"
    if short_part:
        raw["request_parts"][2]["text"] = "每天八点"
        raw["request_parts"][3]["text"] = "发给我"
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(RECORD["input"])
    assert result["changes"]["prompt"] == BUSINESS
    assert result["missing_fields"] == []


@pytest.mark.parametrize("suffix", ["看退款率", "看看异常订单", "看一下平台占比"])
async def test_delivery_suffix_does_not_swallow_business_requirements(suffix):
    raw = deepcopy(RECORD["raw"])
    raw["request_parts"][3]["text"] = "发给我"
    text = RECORD["input"].removesuffix("看") + suffix
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(text)
    assert result["changes"]["prompt"] == BUSINESS + "；" + suffix


async def test_delivery_output_format_and_punctuation_remain_in_business():
    raw = deepcopy(RECORD["raw"])
    raw["request_parts"][3]["text"] = "，以表格显示，发给我看"
    text = RECORD["input"].replace("发给我看", "，以表格显示，发给我看")
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(text)
    assert result["changes"]["prompt"] == BUSINESS + "；以表格显示"


async def test_wrong_clock_still_requires_time_correction():
    raw = deepcopy(RECORD["raw"])
    raw["changes"]["time_str"] = "09:00"
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(RECORD["input"])
    assert result["changes"]["prompt"] == BUSINESS
    assert result["missing_fields"] == ["time_str"]
    assert "time_str" not in result["changes"]


async def test_missing_schedule_keeps_content_in_editable_form():
    from tests.test_scheduled_task_changeset_adapter import _Db

    raw = deepcopy(RECORD["raw"])
    for field in ("schedule_type", "time_str"):
        raw["changes"].pop(field)
        raw["evidence"].pop(field)
    raw["request_parts"].pop(2)
    text = RECORD["input"].replace("每天八点钟", "")
    manager = ChatTaskManager(_Db(), "u1", "o1", submission_mode="apply_if_allowed")
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)), \
         patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch.object(manager, "_begin_request", AsyncMock()) as submit:
        form = await manager.handle("create", {"description": text})
    submit.assert_not_awaited()
    fields = {field["name"]: field for field in form["fields"]}
    assert fields["prompt"]["default_value"] == BUSINESS
    assert fields["prompt"]["type"] == "textarea"
    assert fields["schedule_type"]["default_value"] == ""
    assert fields["time_str"]["default_value"] == ""


@pytest.mark.parametrize("record", json.loads((EVIDENCE.parent / "scheduled-task-content-evidence/model-verified.json").read_text()))
async def test_previous_real_model_outputs_keep_verified_behavior(record):
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=record["raw"])):
        result = await parse_task_request(record["input"])
    assert result == record["result"]


@pytest.mark.parametrize("record", [RECORD] + [json.loads((EVIDENCE / f"after-{index}.json").read_text()) for index in (1, 2)])
async def test_reported_request_reaches_original_submission_boundary(monkeypatch, record):
    from core.config import get_settings
    from services.agent.agent_result import AgentResult
    from services.agent.tool_executor import ToolExecutor
    from tests.tool_runtime_support import IdentityDB

    monkeypatch.setattr(get_settings(), "scheduled_task_direct_enabled", True)
    executor = ToolExecutor(IdentityDB(), "u1", "c1", "o1", tool_entrypoint="model", task_id="task1")
    executor._parent_messages = [{"role": "user", "content": record["input"]}]
    row = {"id": "synthetic-changeset", "status": "applied"}
    # Replay the recorded correct fields as the new model-tool contract.
    # The old parser replays above remain; new chat must never call it again.
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=record["raw"])) as model, \
         patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch("services.permissions.checker.check_permission", AsyncMock(return_value=True)), \
         patch.object(ChatTaskManager, "_begin_request", AsyncMock(return_value={"type": "change_set", "data": row})) as submit:
        result = await executor.execute("manage_scheduled_task", {"action": "create", "description": "模型改写",
                                        "definition": record["raw"]["changes"], "recipient": record["raw"]["recipient"]}, call_id="colloquial-request")
    from services.scheduler.chat_task_manager import FormBlockResult, handle_form_submit
    assert isinstance(result, FormBlockResult)
    model.assert_not_awaited()
    submit.assert_not_awaited()
    values = {f['name']: f.get('default_value') for f in result.form['fields']}
    with patch("services.permissions.checker.check_permission", AsyncMock(return_value=True)), \
         patch("services.scheduler.chat_task_manager._propose_form_change", AsyncMock(return_value={"success": True})) as confirmed:
        await handle_form_submit(IdentityDB(), "u1", "o1", "scheduled_task_create", values)
    definition = confirmed.call_args.kwargs['definition']
    assert confirmed.call_args.kwargs['operation'] == 'create'
    assert {k: definition[k] for k in ('name', 'prompt', 'schedule_type', 'time_str', 'push_target', 'timezone')} == {
        "name": record["raw"]["changes"].get("name", BUSINESS[:20]), "prompt": BUSINESS,
        "schedule_type": "daily", "time_str": "08:00",
        "push_target": {"type": "web", "user_id": "u1"}, "timezone": "Asia/Shanghai",
    }
