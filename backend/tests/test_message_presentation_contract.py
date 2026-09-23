import os
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-key")

import pytest
from config.message_presentation import PRESENTATION_COLORS, PRESENTATION_COLORS_TEXT
from services.prompt_builder.layers.static_layer import StaticLayer
from services.tools.definitions.code_schemas import build_code_tools
from services.sandbox.emit_protocol import install_emit_in_globals
from services.sandbox.emit_protocol import build_table_payload
from services.handlers.emit_payloads import build_block_from_payload, build_part_from_payload
from services.handlers.chat.outcome_builder import build_content_parts
from schemas.message import serialize_content_part


def test_table_presentation_survives_emit_and_persistence():
    rows = [{"涨跌": 10}, {"涨跌": 10}]
    styles = [{"涨跌": {"color": "green"}}, {"涨跌": {"color": "red", "bold": True}}]
    payload = build_table_payload(rows, cell_styles=styles)
    assert payload["rows"] == rows
    block = build_block_from_payload(payload)
    parts = [build_part_from_payload(payload), *build_content_parts([block], fallback_text="")]
    for part in parts:
        serialized = serialize_content_part(part)
        assert serialized["rows"] == rows
        assert serialized["cell_styles"] == styles


def test_prompt_and_tool_advertise_the_same_palette():
    prompt = StaticLayer.render()
    description = build_code_tools()[0]["function"]["description"]
    assert PRESENTATION_COLORS_TEXT in prompt
    assert PRESENTATION_COLORS_TEXT in description
    assert '<span data-color="颜色名">内容</span>' in prompt
    assert "cell_styles" in prompt and "cell_styles" in description
    assert "{{presentation_colors}}" not in prompt
    assert "上涨红色、下跌绿色" not in prompt


@pytest.mark.parametrize("color", PRESENTATION_COLORS)
def test_emit_supports_every_advertised_color(color):
    namespace, buffer = {}, []
    install_emit_in_globals(namespace, buffer)
    namespace["emit_table"]([{"值": 0}], cell_styles=[{"值": {"color": color}}])
    assert buffer[0]["cell_styles"] == [{"值": {"color": color}}]
    assert buffer[0]["rows"] == [{"值": 0}]


@pytest.mark.parametrize("styles", [
    "invalid", [], [{"missing": {"color": "red"}}],
    [{"值": {"color": "pink"}}], [{"值": {"bold": "true"}}],
    [{"值": {"style": "color:red"}}], [{"值": {"color": ["red"]}}],
])
def test_emit_rejects_mismatched_or_unsupported_styles(styles):
    with pytest.raises(ValueError):
        build_table_payload([{"值": 10}], cell_styles=styles)


def test_truncation_keeps_styles_aligned_without_mutating_inputs():
    rows = [{"值": i} for i in range(250)]
    styles = [{"值": {"color": "red" if i % 2 else "green"}} for i in range(250)]
    result = build_table_payload(rows, cell_styles=styles)
    assert result["rows"] == rows[:200]
    assert result["cell_styles"] == styles[:200]
    assert len(rows) == len(styles) == 250
    assert result["truncated"] is True


def test_old_tables_need_no_style_metadata():
    payload = build_table_payload([{"值": 10}])
    assert "cell_styles" not in payload
    assert "cell_styles" not in serialize_content_part(build_part_from_payload(payload))
