"""文件坐标预探测模块。

三段采样（开头+中间+末尾）→ 转坐标文本 → AI 判断结构 → 返回结论。
AI 一次调用覆盖：表头位置、多级表头、合计行、备注区域、合并语义等。

设计文档：docs/document/TECH_文件处理系统_坐标预探测方案.md
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

# 采样配置
_HEAD_ROWS = 20
_MID_ROWS = 10
_TAIL_ROWS = 20


@dataclass
class PrescanResult:
    """AI 预探测返回的结构判断。"""
    header_type: str = "single"       # single | multi_level | none
    header_rows: list[int] = field(default_factory=list)
    data_start_row: int = 1
    column_mapping: dict[str, str] = field(default_factory=dict)
    special_rows: dict[str, list[int]] = field(default_factory=dict)
    regions: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = "low"
    reasoning: str = ""
    raw_response: str = ""            # 原始 AI 返回（调试用）


def build_prescan_prompt(
    filename: str,
    sheet_name: str,
    total_rows: int,
    total_cols: int,
    sampled_text: str,
) -> str:
    """构建坐标预探测的 AI 输入提示词。"""
    return f"""我们在做文件预探测，需要你理解坐标格式的规则。

格式说明：
每个单元格用"坐标:值"表示，同一行用 | 分隔。
空单元格直接用空字符串 ''，不用 null，不用任何标记。

文件: {filename} | Sheet: {sheet_name} | 总行数: {total_rows} | 总列数: {total_cols}

{sampled_text}

规则：
1. 空的就是 ''，你看到连续的空格，说明这里可能是合并单元格的延伸
2. 你自己从空格分布推断合并范围
3. 这是三段采样（开头 + 中间 + 末尾），不是完整文件

你的任务是判断：
- 表头在哪里（单级还是多级）
- 数据从哪行开始
- 有没有合计行/备注行/单位行
- 有没有多个数据区域（纵向或横向）
- 如果有多级表头或重复列名，给出业务语义的列名映射

返回严格 JSON 格式（不要 markdown 代码块）：
{{"header_type": "single | multi_level | none", "header_rows": [行号], "data_start_row": 行号, "column_mapping": {{"原列字母": "业务列名"}}, "special_rows": {{"summary": [行号], "unit": [行号], "note": [行号]}}, "regions": [{{"start_row": N, "end_row": N, "start_col": "A", "end_col": "E", "description": ""}}], "confidence": "high | medium | low", "reasoning": "简短说明判断依据"}}"""


def sample_to_coordinate_text(
    reader: Any,
    target_sheet: int | str,
    total_rows: int,
    total_cols: int,
) -> str:
    """三段采样 → 转坐标文本格式。"""
    lines: list[str] = []

    # 开头
    head_end = min(_HEAD_ROWS, total_rows)
    head_df = reader.load_sheet(target_sheet, header_row=None, n_rows=head_end).to_pandas()
    lines.append(f"--- 开头（Row 1-{head_end}）---")
    lines.extend(_df_to_coord_lines(head_df, row_offset=1))

    # 中间（文件够大时）
    if total_rows > _HEAD_ROWS + _TAIL_ROWS + _MID_ROWS:
        mid_start = total_rows // 2 - _MID_ROWS // 2
        mid_end = mid_start + _MID_ROWS
        try:
            mid_df = reader.load_sheet(
                target_sheet, header_row=None,
                skip_rows=mid_start, n_rows=_MID_ROWS,
            ).to_pandas()
            lines.append(f"\n--- 中间（Row {mid_start + 1}-{mid_end}）---")
            lines.extend(_df_to_coord_lines(mid_df, row_offset=mid_start + 1))
        except Exception:
            pass

    # 末尾（文件够大时）
    if total_rows > _HEAD_ROWS + _TAIL_ROWS:
        tail_start = total_rows - _TAIL_ROWS
        try:
            tail_df = reader.load_sheet(
                target_sheet, header_row=None,
                skip_rows=tail_start, n_rows=_TAIL_ROWS,
            ).to_pandas()
            lines.append(f"\n--- 末尾（Row {tail_start + 1}-{total_rows}）---")
            lines.extend(_df_to_coord_lines(tail_df, row_offset=tail_start + 1))
        except Exception:
            pass

    return "\n".join(lines)


def _df_to_coord_lines(df: "pd.DataFrame", row_offset: int) -> list[str]:
    """DataFrame → 坐标文本行列表。"""
    lines = []
    for idx in range(len(df)):
        row_num = idx + row_offset
        cells = []
        for col_idx in range(len(df.columns)):
            col_letter = _col_letter(col_idx)
            val = df.iloc[idx, col_idx]
            if val is None or (isinstance(val, float) and str(val) == "nan"):
                cells.append(f"{col_letter}{row_num}:")
            else:
                cells.append(f"{col_letter}{row_num}:{val}")
        lines.append(f"Row {row_num}: {' | '.join(cells)}")
    return lines


def _col_letter(idx: int) -> str:
    """0-indexed → Excel 列字母。"""
    result = ""
    n = idx
    while True:
        result = chr(ord("A") + n % 26) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result


async def run_prescan(
    reader: Any,
    target_sheet: int | str,
    filename: str,
    sheet_name: str,
    total_rows: int,
    total_cols: int,
) -> PrescanResult:
    """执行坐标预探测：采样 → AI 判断 → 返回结论。"""
    sampled_text = sample_to_coordinate_text(reader, target_sheet, total_rows, total_cols)

    prompt = build_prescan_prompt(filename, sheet_name, total_rows, total_cols, sampled_text)

    # 调用 AI
    try:
        response = await _call_llm(prompt)
        result = _parse_prescan_response(response)
        result.raw_response = response
        logger.info(
            f"Prescan OK | {filename} | header={result.header_rows} "
            f"| data_start={result.data_start_row} | confidence={result.confidence}"
        )
        return result
    except Exception as e:
        logger.warning(f"Prescan failed, fallback to code detection: {e}")
        return PrescanResult(confidence="low", reasoning=f"AI prescan failed: {e}")


async def _call_llm(prompt: str) -> str:
    """调用 LLM 做结构判断。"""
    try:
        from core.config import get_settings
        from openai import AsyncOpenAI

        settings = get_settings()
        client = AsyncOpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
        )
        resp = await client.chat.completions.create(
            model="qwen-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1000,
            timeout=10,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        raise RuntimeError(f"LLM call failed: {e}") from e


def _parse_prescan_response(response: str) -> PrescanResult:
    """解析 AI 返回的 JSON。"""
    # 去掉可能的 markdown 代码块
    text = response.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    data = json.loads(text)
    return PrescanResult(
        header_type=data.get("header_type", "single"),
        header_rows=data.get("header_rows", [0]),
        data_start_row=data.get("data_start_row", 1),
        column_mapping=data.get("column_mapping", {}),
        special_rows=data.get("special_rows", {}),
        regions=data.get("regions", []),
        confidence=data.get("confidence", "low"),
        reasoning=data.get("reasoning", ""),
    )
