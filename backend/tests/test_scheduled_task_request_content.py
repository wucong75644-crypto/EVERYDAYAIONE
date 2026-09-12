"""Creation speech must not become a scheduled execution instruction."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.scheduler.task_nl_parser import parse_task_request
from services.scheduler.chat_task_manager import ChatTaskManager
from tests.test_scheduled_task_changeset_adapter import _Db


TEXT = "创建一个定时任务，查询昨天的付款订单数按照平台划分"
BUSINESS = "查询昨天的付款订单数按照平台划分"
RAW = {
    "changes": {"name": "昨日付款订单数统计", "prompt": BUSINESS},
    "evidence": {"prompt": BUSINESS}, "recipient": "",
    "request_parts": [
        {"kind": "request", "text": "创建一个定时任务，"},
        {"kind": "execution", "text": BUSINESS},
    ],
}
TARGETS = [{"label": "推送给我（网页）", "value": '{"type":"web","user_id":"u1"}'}]


async def test_reported_request_extracts_only_business_and_requires_missing_schedule():
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=RAW)):
        result = await parse_task_request(TEXT)
    assert result["changes"]["prompt"] == BUSINESS
    assert set(result["missing_fields"]) == {"schedule_type", "time_str"}
    assert "time_str" not in result["changes"]


async def test_form_exposes_the_extracted_instruction_before_user_submits_schedule():
    manager = ChatTaskManager(_Db(), "u1", "o1", submission_mode="apply_if_allowed")
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=RAW)), \
         patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch.object(manager, "_begin_request", AsyncMock()) as submit:
        form = await manager.handle("create", {"description": TEXT})
    submit.assert_not_awaited()
    fields = {field["name"]: field for field in form["fields"]}
    assert fields["prompt"]["type"] == "textarea"
    assert fields["prompt"]["default_value"] == BUSINESS
    assert fields["schedule_type"]["default_value"] == fields["time_str"]["default_value"] == ""
    assert TEXT in form["description"]  # original remains separate and visible
    fixture = Path(__file__).resolve().parents[2] / "frontend/src/test/fixtures/scheduledTaskContentForm.json"
    assert {**form, "form_id": "synthetic-task-content-form"} == json.loads(fixture.read_text())


async def test_business_scope_comes_from_complete_source_not_model_rewrite():
    raw = deepcopy(RAW)
    raw["changes"]["prompt"] = "查询所有店铺的所有订单金额"
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(TEXT)
    assert result["changes"]["prompt"] == BUSINESS


@pytest.mark.parametrize("mutation", ["rewrite_source", "classify_business_as_request", "whole_request_as_execution", "unknown_kind", "missing_parts"])
async def test_unreliable_partition_requires_correction_instead_of_executing_original(mutation):
    raw = deepcopy(RAW)
    if mutation == "rewrite_source":
        raw["request_parts"][1]["text"] = "查询全部订单"
    elif mutation == "classify_business_as_request":
        raw["request_parts"][1]["kind"] = "request"
    elif mutation == "whole_request_as_execution":
        raw["request_parts"] = [{"kind": "execution", "text": TEXT}]
    elif mutation == "unknown_kind":
        raw["request_parts"][1]["kind"] = "ignore"
    else:
        raw.pop("request_parts")
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(TEXT)
    assert "prompt" not in result["changes"] and "prompt" in result["missing_fields"]


async def test_schedule_delivery_removed_but_business_dates_filters_and_output_preserved():
    instruction = "查询昨天A店已付款订单数，排除退款订单；按照平台划分，以表格显示"
    text = "请创建一个定时任务，每天上午9点，" + instruction + "，发送到销售群"
    raw = {
        "changes": {"name": "付款订单日报", "prompt": "订单汇总", "schedule_type": "daily", "time_str": "09:00"},
        "evidence": {"prompt": "订单数", "schedule_type": "每天", "time_str": "上午9点"},
        "recipient": "销售群",
        "request_parts": [
            {"kind": "request", "text": "请创建一个定时任务，"},
            {"kind": "schedule", "text": "每天上午9点，"},
            {"kind": "execution", "text": instruction},
            {"kind": "delivery", "text": "，发送到销售群"},
        ],
    }
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(text)
    assert result["changes"]["prompt"] == instruction
    assert result["recipient"] == "销售群" and result["missing_fields"] == []


@pytest.mark.parametrize("kind", ["schedule", "delivery"])
async def test_metadata_classification_cannot_drop_business_requirements(kind):
    raw = deepcopy(RAW)
    raw["request_parts"][1]["kind"] = kind
    raw["changes"].update(schedule_type="daily", time_str="08:00")
    raw["evidence"].update(schedule_type=BUSINESS, time_str=BUSINESS)
    raw["recipient"] = "平台"
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(TEXT)
    assert "prompt" in result["missing_fields"]


async def test_valid_complete_request_reuses_existing_submission_chain_with_clean_content():
    raw = deepcopy(RAW)
    raw["changes"].update(schedule_type="daily", time_str="09:00")
    raw["evidence"].update(schedule_type="每天", time_str="9点")
    raw["request_parts"].insert(1, {"kind": "schedule", "text": "每天9点，"})
    text = "创建一个定时任务，每天9点，" + BUSINESS
    manager = ChatTaskManager(_Db(), "u1", "o1", submission_mode="apply_if_allowed")
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)), \
         patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch.object(manager, "_begin_request", AsyncMock(return_value={"type": "change_set"})) as submit:
        result = await manager.handle("create", {"description": text})
    assert result["type"] == "change_set"
    assert submit.call_args.args[1]["prompt"] == BUSINESS


async def test_reordered_segments_and_separator_omission_still_preserve_source_order():
    raw = deepcopy(RAW)
    raw["changes"].update(schedule_type="daily", time_str="09:00")
    raw["evidence"].update(schedule_type="每天", time_str="上午9点")
    raw["request_parts"] = [
        {"kind": "request", "text": "创建一个定时任务"},
        {"kind": "schedule", "text": "每天上午9点执行"},
        {"kind": "execution", "text": "列出环比变化"},
        {"kind": "execution", "text": BUSINESS},
    ]
    text = "创建一个定时任务，" + BUSINESS + "，列出环比变化，每天上午9点执行"
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(text)
    assert result["changes"]["prompt"] == BUSINESS + "；列出环比变化"
    assert result["missing_fields"] == []


async def test_duplicate_spans_cannot_claim_the_same_source_twice():
    raw = deepcopy(RAW)
    raw["request_parts"].append(raw["request_parts"][1].copy())
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(TEXT)
    assert "prompt" in result["missing_fields"]


async def test_weekday_list_does_not_leave_schedule_words_in_business_instruction():
    raw = deepcopy(RAW)
    raw["changes"].update(schedule_type="weekly", weekdays=[1, 5], time_str="10:00")
    raw["evidence"].update(schedule_type="每周一和周五", weekdays="周一和周五", time_str="上午10点")
    raw["request_parts"].append({"kind": "schedule", "text": "每周一和周五上午10点，"})
    text = "创建一个定时任务，每周一和周五上午10点，" + BUSINESS
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(text)
    assert result["changes"]["prompt"] == BUSINESS
    assert result["changes"]["weekdays"] == [1, 5]
    assert result["missing_fields"] == []


async def test_model_omitted_business_words_are_recovered_from_original_source():
    raw = deepcopy(RAW)
    raw["request_parts"][1]["text"] = "查询昨天的付款订单数"
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(TEXT)
    assert result["changes"]["prompt"].replace("；", "") == BUSINESS


async def test_output_format_misclassified_as_delivery_is_kept_in_instruction():
    raw = deepcopy(RAW)
    text = TEXT + "，以表格显示，发送到销售群"
    raw["recipient"] = "销售群"
    raw["request_parts"].append({"kind": "delivery", "text": "以表格显示，发送到销售群"})
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(text)
    assert result["changes"]["prompt"] == BUSINESS + "；以表格显示"


async def test_omitted_schedule_verb_is_removed_only_as_a_schedule_suffix():
    raw = deepcopy(RAW)
    raw["changes"].update(schedule_type="daily", time_str="09:00")
    raw["evidence"].update(schedule_type="每天", time_str="上午9点")
    raw["request_parts"].append({"kind": "schedule", "text": "每天上午9点"})
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(TEXT + "，每天上午9点执行")
    assert result["changes"]["prompt"] == BUSINESS


def test_panel_parse_route_reuses_content_extraction_with_unchanged_public_shape():
    from fastapi.testclient import TestClient
    from tests.test_scheduled_tasks_routes import FakeDB, _build_app
    with patch("api.routes.scheduled_tasks.check_permission", AsyncMock(return_value=True)), \
         patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=RAW)):
        response = TestClient(_build_app(FakeDB())).post("/api/scheduled-tasks/parse", json={
            "text": TEXT, "operation": "create", "explicit_fields_only": True,
        })
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["prompt"] == BUSINESS
    assert set(data["missing_fields"]) == {"schedule_type", "time_str"}
    assert "request_parts" not in data
