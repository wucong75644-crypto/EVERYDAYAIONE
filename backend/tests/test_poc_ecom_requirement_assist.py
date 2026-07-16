"""AI 帮写多模态 POC 的确定性单元测试。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from scripts.poc_ecom_requirement_assist import (
    build_messages,
    evaluate_result,
    main,
    parse_json_response,
    run_scenario,
    validate_result,
)


def valid_result() -> dict:
    return {
        "confirmed_product_facts": ["蓝色笔记本"],
        "unclear_items": ["材质未明确"],
        "reference_analysis": ["暖色背景与居中构图"],
        "conflicts": [],
        "suggestions": [
            {
                "id": f"scheme_{index}",
                "name": f"方案{index}",
                "positioning": f"定位{index}",
                "brief_markdown": f"突出400页大容量，方案{index}",
            }
            for index in range(1, 4)
        ],
    }


def test_parse_json_response_accepts_fenced_json() -> None:
    payload = valid_result()
    parsed = parse_json_response(f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```")
    assert parsed == payload


def test_parse_json_response_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="没有 JSON 对象"):
        parse_json_response("无法生成")


def test_parse_json_response_extracts_embedded_object() -> None:
    payload = valid_result()
    parsed = parse_json_response(f"结果如下：{json.dumps(payload, ensure_ascii=False)}。")
    assert parsed == payload


def test_validate_result_requires_three_ordered_suggestions() -> None:
    payload = valid_result()
    payload["suggestions"] = payload["suggestions"][:2]
    assert validate_result(payload) == ["suggestions 必须恰好包含 3 项"]


def test_validate_result_reports_invalid_fields_and_duplicate_names() -> None:
    payload = valid_result()
    payload["conflicts"] = "无"
    payload["suggestions"][1]["id"] = "wrong"
    payload["suggestions"][1]["name"] = "方案1"
    payload["suggestions"][2]["brief_markdown"] = ""

    errors = validate_result(payload)

    assert "conflicts 必须是数组" in errors
    assert "suggestions[2].brief_markdown 不能为空" in errors
    assert "三个方案名称必须不同" in errors
    assert any(error.startswith("方案 id 必须依次为") for error in errors)


def test_evaluate_result_detects_reference_product_contamination() -> None:
    payload = valid_result()
    payload["suggestions"][0]["brief_markdown"] += "，使用拼豆收纳盒"
    evaluation = evaluate_result(
        payload,
        has_reference=True,
        required_text="400页大容量",
        contamination_terms=["拼豆", "收纳盒"],
    )
    assert evaluation["structure_valid"] is True
    assert evaluation["reference_analysis_present"] is True
    assert evaluation["user_requirement_reflected"] is True
    assert evaluation["contamination_terms_found"] == ["拼豆", "收纳盒"]


def test_build_messages_marks_product_and_reference_roles(tmp_path: Path) -> None:
    product = tmp_path / "product.png"
    reference = tmp_path / "reference.jpg"
    product.write_bytes(b"product")
    reference.write_bytes(b"reference")

    messages = build_messages([product], [reference], "清新自然", "淘宝")
    content = messages[1]["content"]

    assert content[1]["text"].startswith("下面是产品图 1")
    assert content[2]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[3]["text"].startswith("下面是参考图 1")
    assert content[4]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_build_messages_rejects_missing_image(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="图片不存在"):
        build_messages([tmp_path / "missing.jpg"], [], "", "淘宝")


@pytest.mark.asyncio
async def test_run_scenario_records_usage_and_closes_adapter(tmp_path: Path) -> None:
    product = tmp_path / "product.png"
    product.write_bytes(b"product")
    adapter = AsyncMock()
    adapter.chat_sync.return_value = SimpleNamespace(
        content=json.dumps(valid_result(), ensure_ascii=False),
        prompt_tokens=120,
        completion_tokens=80,
    )

    with (
        patch("scripts.poc_ecom_requirement_assist.settings.dashscope_api_key", "test-key"),
        patch("scripts.poc_ecom_requirement_assist.DashScopeChatAdapter", return_value=adapter),
    ):
        report = await run_scenario(
            name="text_only",
            product_images=[product],
            reference_images=[],
            user_requirement="400页大容量",
            platform="淘宝",
            contamination_terms=[],
        )

    assert report["scenario"] == "text_only"
    assert report["prompt_tokens"] == 120
    assert report["completion_tokens"] == 80
    assert report["evaluation"]["structure_valid"] is True
    adapter.chat_sync.assert_awaited_once()
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_scenario_requires_api_key(tmp_path: Path) -> None:
    product = tmp_path / "product.png"
    product.write_bytes(b"product")
    with patch("scripts.poc_ecom_requirement_assist.settings.dashscope_api_key", ""):
        with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
            await run_scenario(
                name="text_only",
                product_images=[product],
                reference_images=[],
                user_requirement="",
                platform="淘宝",
                contamination_terms=[],
            )


@pytest.mark.asyncio
async def test_main_runs_all_three_scenarios_and_writes_report(tmp_path: Path) -> None:
    product = tmp_path / "product.png"
    reference = tmp_path / "reference.png"
    output = tmp_path / "report.json"
    product.write_bytes(b"product")
    reference.write_bytes(b"reference")
    args = SimpleNamespace(
        product_image=[product],
        reference_image=[reference],
        user_requirement="清新自然",
        platform="淘宝",
        contamination_term=[],
        output=output,
    )
    scenario_mock = AsyncMock(side_effect=lambda **kwargs: {"scenario": kwargs["name"]})

    with (
        patch("scripts.poc_ecom_requirement_assist.parse_args", return_value=args),
        patch("scripts.poc_ecom_requirement_assist.run_scenario", scenario_mock),
    ):
        await main()

    assert [item["scenario"] for item in json.loads(output.read_text())] == [
        "text_only",
        "reference_only",
        "combined",
    ]
    assert scenario_mock.await_count == 3
