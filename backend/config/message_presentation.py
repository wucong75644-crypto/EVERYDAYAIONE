"""消息展示能力：模型说明、沙盒校验和前端共用 JSON 颜色目录。"""
import json
from pathlib import Path
from typing import Any

PRESENTATION_CONTRACT = json.loads(
    Path(__file__).with_suffix(".json").read_text(encoding="utf-8")
)
PRESENTATION_COLORS = tuple(PRESENTATION_CONTRACT["colors"])
PRESENTATION_COLORS_TEXT = " / ".join(PRESENTATION_COLORS)


def validate_cell_styles(value: Any) -> list[dict[str, dict]] | None:
    """拒绝未约定样式；不根据数值或箭头推断颜色。"""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("cell_styles 必须是与数据行对应的 list[dict]")
    result = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("cell_styles 每一行必须是 dict，未设置样式的行使用 {}")
        clean_row = {}
        for column, style in row.items():
            if not isinstance(column, str) or not isinstance(style, dict):
                raise ValueError("cell_styles 使用列名映射到样式 dict")
            if set(style) - {"color", "bold"}:
                raise ValueError("单元格样式只支持 color 和 bold")
            if "color" in style and (
                not isinstance(style["color"], str) or style["color"] not in PRESENTATION_COLORS
            ):
                raise ValueError(f"color 只支持 {PRESENTATION_COLORS_TEXT}")
            if "bold" in style and not isinstance(style["bold"], bool):
                raise ValueError("bold 必须是 bool")
            clean_row[column] = dict(style)
        result.append(clean_row)
    return result
