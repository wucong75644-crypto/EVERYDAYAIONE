"""Chat 内容块结果构造测试。"""

from schemas.message import (
    ChartPart,
    FilePart,
    FormPart,
    ImagePart,
    TextPart,
    ThinkingPart,
    ToolResultPart,
    ToolStepPart,
)
from services.handlers.chat.outcome_builder import (
    append_final_turn_blocks,
    build_content_parts,
)


def test_build_content_parts_preserves_mixed_block_order() -> None:
    blocks = [
        {"type": "thinking", "text": "分析", "duration_ms": 12},
        {"type": "text", "text": "结果"},
        {
            "type": "tool_step",
            "tool_name": "query",
            "tool_call_id": "call-1",
            "status": "completed",
            "input": "{}",
            "output": "ok",
        },
        {"type": "tool_result", "tool_name": "query", "text": "ok"},
        {"type": "image", "url": "https://cdn.test/image.png", "alt": "image"},
        {
            "type": "file",
            "url": "https://cdn.test/report.csv",
            "name": "report.csv",
            "mime_type": "text/csv",
        },
        {"type": "chart", "option": {"series": []}, "title": "趋势"},
        {
            "type": "form",
            "form_id": "form-1",
            "form_type": "confirmation",
            "title": "确认",
            "fields": [],
        },
    ]

    parts = build_content_parts(blocks, fallback_text="unused")

    assert [type(part) for part in parts] == [
        ThinkingPart,
        TextPart,
        ToolStepPart,
        ToolResultPart,
        ImagePart,
        FilePart,
        ChartPart,
        FormPart,
    ]


def test_build_content_parts_keeps_plain_media_markers_as_text() -> None:
    text = (
        '{"image":"https://example.test/a.jpg"} '
        "[FILE]https://example.test/a.csv|a.csv|text/csv|1[/FILE]"
    )

    parts = build_content_parts([], fallback_text=text)

    assert parts == [TextPart(text=text)]


def test_build_content_parts_inserts_fallback_thinking_before_text() -> None:
    parts = build_content_parts(
        [],
        fallback_text="答案",
        fallback_thinking="思考",
        fallback_thinking_duration_ms=25,
    )

    assert isinstance(parts[0], ThinkingPart)
    assert parts[0].duration_ms == 25
    assert parts[1] == TextPart(text="答案")


def test_append_final_turn_blocks_respects_thinking_commit_state() -> None:
    blocks: list[dict] = []

    append_final_turn_blocks(
        blocks,
        thinking="推理",
        thinking_committed=False,
        thinking_duration_ms=18,
        text="结论",
    )
    append_final_turn_blocks(
        blocks,
        thinking="已经提交",
        thinking_committed=True,
        thinking_duration_ms=30,
        text="",
    )

    assert blocks == [
        {"type": "thinking", "text": "推理", "duration_ms": 18},
        {"type": "text", "text": "结论"},
    ]


def test_unknown_content_block_is_ignored() -> None:
    parts = build_content_parts(
        [{"type": "future_block", "value": "x"}],
        fallback_text="unused",
    )

    assert parts == []
