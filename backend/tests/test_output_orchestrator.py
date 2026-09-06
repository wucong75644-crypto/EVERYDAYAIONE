"""统一输出编排层测试。"""

from unittest.mock import AsyncMock

import pytest

from services.handlers.output_orchestrator import (
    OutputOrchestrator,
    canonicalize_content_blocks,
    canonicalize_output,
    canonicalize_text,
)


TABLE = {
    "type": "table",
    "title": "付款订单",
    "columns": ["平台", "有效订单数", "有效金额"],
    "rows": [{"平台": "抖音", "有效订单数": 128, "有效金额": 2260.5}],
}


def _markdown_table() -> str:
    return (
        "### 付款订单\n"
        "| 平台 | 有效订单数 | 有效金额 |\n"
        "| --- | --- | --- |\n"
        "| 抖音 | 128 | 2260.5 |"
    )


def test_structured_table_owns_matching_markdown_display():
    text = "查询完成。\n\n" + _markdown_table() + "\n\n请关注有效金额。"

    result = canonicalize_text(text, [TABLE])

    assert result == "查询完成。\n\n请关注有效金额。"


def test_structured_table_owns_semantically_equivalent_markdown_display():
    block = {
        "type": "table",
        "columns": ["分组", "总订单数", "总金额", "有效订单数", "有效金额"],
        "rows": [
            {
                "分组": "1688",
                "总订单数": 197,
                "总金额": 9720.94,
                "有效订单数": 191,
                "有效金额": 9572.58,
            },
            {
                "分组": "pdd",
                "总订单数": 5388,
                "总金额": 0,
                "有效订单数": 4726,
                "有效金额": 0,
            },
        ],
    }
    text = (
        "2026-09-06 今天付款订单数按平台划分:\n\n"
        "| 平台 | 总订单数 | 有效订单数 | 总金额(元) | 有效金额(元) |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 拼多多 | 5,388 | 4,726 | 0.00 | 0.00 |\n"
        "| 1688 | 197 | 191 | 9,720.94 | 9,572.58 |\n"
        "| 合计 | 5,585 | 4,917 | 9,720.94 | 9,572.58 |"
    )

    assert canonicalize_text(text, [block]) == (
        "2026-09-06 今天付款订单数按平台划分:"
    )


def test_semantic_table_match_keeps_different_business_facts():
    text = (
        "| 平台 | 总订单数 | 有效订单数 | 总金额(元) | 有效金额(元) |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 1688 | 197 | 190 | 9,720.94 | 9,572.58 |"
    )

    assert canonicalize_text(text, [{
        "type": "table",
        "columns": ["分组", "总订单数", "总金额", "有效订单数", "有效金额"],
        "rows": [{
            "分组": "1688",
            "总订单数": 197,
            "总金额": 9720.94,
            "有效订单数": 191,
            "有效金额": 9572.58,
        }],
    }]) == text


def test_semantic_table_match_keeps_different_dimension_labels():
    text = (
        "| 平台 | 总订单数 | 有效订单数 | 总金额(元) | 有效金额(元) |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 其他平台 | 197 | 191 | 9,720.94 | 9,572.58 |"
    )

    assert canonicalize_text(text, [{
        "type": "table",
        "columns": ["分组", "总订单数", "总金额", "有效订单数", "有效金额"],
        "rows": [{
            "分组": "1688",
            "总订单数": 197,
            "总金额": 9720.94,
            "有效订单数": 191,
            "有效金额": 9572.58,
        }],
    }]) == text


def test_plain_markdown_table_is_preserved_without_structured_block():
    text = _markdown_table()

    assert canonicalize_text(text, []) == text


def test_table_fingerprint_supports_existing_escaped_pipe_format():
    block = {
        "type": "table",
        "columns": ["名称", "备注"],
        "rows": [{"名称": "商品", "备注": "a|b"}],
    }
    text = (
        "| 名称 | 备注 |\n"
        "| --- | --- |\n"
        "| 商品 | a\\|b |"
    )

    assert canonicalize_text(text, [block]) == ""


def test_structured_context_does_not_drop_plain_newline_chunks():
    assert canonicalize_text("\n", [TABLE]) == "\n"


def test_chart_diagram_and_media_links_are_deduplicated():
    blocks = [
        {
            "type": "chart",
            "option": {"series": [{"type": "bar", "data": [1, 2]}]},
        },
        {"type": "diagram", "source": "flowchart TD\nA-->B"},
        {"type": "image", "url": "https://cdn.example/a.png"},
        {"type": "file", "url": "https://cdn.example/report.xlsx"},
    ]
    text = (
        "结论\n\n"
        "```json\n{\"series\": [{\"type\": \"bar\", \"data\": [1, 2]}]}\n```\n\n"
        "```mermaid\nflowchart TD\nA-->B\n```\n\n"
        "![图表](https://cdn.example/a.png)\n"
        "[报表](https://cdn.example/report.xlsx)"
    )

    assert canonicalize_text(text, blocks) == "结论"


def test_canonical_output_keeps_text_and_structured_block_once():
    text, blocks = canonicalize_output(
        _markdown_table() + "\n\n摘要",
        [TABLE],
    )

    assert text == "摘要"
    assert blocks == [TABLE]


@pytest.mark.asyncio
async def test_streaming_orchestrator_buffers_candidate_until_structured_block():
    sink = AsyncMock()
    sink.text = ""
    sink.thinking = ""
    sink.blocks = []
    orchestrator = OutputOrchestrator(sink)

    await orchestrator.start()
    await orchestrator.on_text("结论如下。\n\n" + _markdown_table())
    sink.on_text.assert_awaited_once_with("结论如下。\n\n")

    await orchestrator.on_block(TABLE)
    await orchestrator.flush()

    assert sink.on_block.await_args.args[0] == TABLE
    assert sink.on_text.await_count == 1


@pytest.mark.asyncio
async def test_streaming_orchestrator_handles_table_split_across_chunks():
    sink = AsyncMock()
    sink.text = ""
    sink.thinking = ""
    sink.blocks = []
    orchestrator = OutputOrchestrator(sink, [TABLE])

    await orchestrator.on_text("结论。\n| 平台 | 有效订单数 | 有效金额 |\n")
    await orchestrator.on_text("| --- | --- | --- |\n| 抖音 | 128 | 2260.5 |")
    await orchestrator.flush()

    assert sink.on_text.await_count == 1
    assert sink.on_text.await_args.args[0] == "结论。\n"


@pytest.mark.asyncio
async def test_streaming_orchestrator_keeps_table_header_buffered_until_separator_chunk():
    sink = AsyncMock()
    sink.text = ""
    sink.thinking = ""
    sink.blocks = []
    orchestrator = OutputOrchestrator(sink, [TABLE])

    await orchestrator.on_text(
        "结论。\n2026-09-06 今天付款订单数按平台划分:\n\n"
    )
    await orchestrator.on_text("| 平台 | 有效订单数 | 有效金额 |\n")
    await orchestrator.on_text("| --- | --- | --- |\n| 抖音 | 128 | 2260.5 |")
    await orchestrator.flush()

    assert [call.args[0] for call in sink.on_text.await_args_list] == [
        "结论。\n2026-09-06 今天付款订单数按平台划分:\n\n",
    ]


def test_canonical_content_blocks_drops_only_duplicate_text():
    content = canonicalize_content_blocks([
        {"type": "text", "text": _markdown_table() + "\n\n总结"},
        TABLE,
    ])

    assert content[0] == {"type": "text", "text": "总结"}
    assert content[1] == TABLE


def test_canonical_content_blocks_deduplicates_structured_artifacts():
    chart = {
        "type": "chart",
        "option": {"series": [{"type": "bar", "data": [1, 2]}]},
    }
    diagram = {"type": "diagram", "source": "flowchart TD\nA-->B"}
    image = {"type": "image", "url": "https://cdn.example/a.png"}
    file = {"type": "file", "url": "https://cdn.example/report.xlsx"}

    content = canonicalize_content_blocks([
        TABLE,
        dict(TABLE),
        chart,
        dict(chart),
        diagram,
        dict(diagram),
        image,
        dict(image),
        file,
        dict(file),
    ])

    assert content == [TABLE, chart, diagram, image, file]


def test_canonical_content_blocks_keeps_distinct_structured_artifacts():
    first = {"type": "image", "url": "https://cdn.example/a.png"}
    second = {"type": "image", "url": "https://cdn.example/b.png"}

    assert canonicalize_content_blocks([first, second]) == [first, second]


@pytest.mark.asyncio
async def test_streaming_orchestrator_does_not_push_duplicate_structured_block():
    sink = AsyncMock()
    sink.text = ""
    sink.thinking = ""
    sink.blocks = []
    orchestrator = OutputOrchestrator(sink)

    await orchestrator.on_block(TABLE)
    await orchestrator.on_block(dict(TABLE))

    sink.on_block.assert_awaited_once_with(TABLE)
