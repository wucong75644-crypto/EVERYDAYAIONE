"""Excel 公式提取（lxml 流式解析 ZIP 内 XML）。"""
from __future__ import annotations

from typing import Any
from zipfile import ZipFile

from loguru import logger

from services.agent.excel_cleaner import _parse_sheet_tags, _resolve_sheet_xml_path


_MAX_FORMULAS = 200           # 公式提取上限
_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_TAG_C = f"{{{_NS}}}c"
_TAG_F = f"{{{_NS}}}f"
_TAG_V = f"{{{_NS}}}v"


def extract_formulas(
    excel_path: str,
    sheet_name: str | int | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """从 Excel 提取公式（lxml 流式解析）。返回 (formulas, skip_reason)。"""
    if not str(excel_path).lower().endswith((".xlsx", ".xlsm")):
        return [], ""

    try:
        from lxml import etree
    except ImportError:
        logger.warning("lxml not installed, skip formula extraction")
        return [], ""

    try:
        zf = ZipFile(excel_path, "r")
    except Exception as e:
        logger.debug(f"Formula extraction: cannot open zip: {e}")
        return [], ""

    try:
        return _extract_formulas_from_zip(zf, sheet_name, etree)
    finally:
        zf.close()


def _extract_formulas_from_zip(
    zf: ZipFile, sheet_name: str | int | None, etree: Any,
) -> tuple[list[dict[str, Any]], str]:
    """ZIP 内：定位 sheet XML → 流式提取公式。"""
    try:
        wb_xml = zf.read("xl/workbook.xml").decode("utf-8", errors="replace")
    except KeyError:
        return [], ""
    sheet_tags = _parse_sheet_tags(wb_xml)
    if not sheet_tags:
        return [], ""
    if sheet_name is None:
        target_idx = 0
    elif isinstance(sheet_name, int):
        target_idx = sheet_name
    else:
        target_idx = 0
        name_lower = str(sheet_name).lower().strip()
        for i, (name, _, _) in enumerate(sheet_tags):
            if name.lower().strip() == name_lower:
                target_idx = i
                break
    if target_idx >= len(sheet_tags):
        return [], ""
    ws_title = sheet_tags[target_idx][0]
    xml_path = _resolve_sheet_xml_path(zf, target_idx)
    if not xml_path:
        return [], ""
    try:
        with zf.open(xml_path) as f:
            return _parse_sheet_formulas(f, ws_title, etree), ""
    except Exception as e:
        logger.debug(f"Formula extraction failed: {e}")
        return [], ""


def _parse_sheet_formulas(stream: Any, ws_title: str, etree: Any) -> list[dict[str, Any]]:
    """lxml.iterparse 流式扫描 <c> 标签提取公式。"""
    result: list[dict[str, Any]] = []
    context = etree.iterparse(stream, events=("end",), tag=_TAG_C)

    for _, elem in context:
        f_elem = elem.find(_TAG_F)
        if f_elem is not None and f_elem.text:
            # 共享公式：只记录定义处（有 text），跳过引用处（无 text）
            cell_ref = elem.get("r", "")
            formula_text = f"={f_elem.text}"

            v_elem = elem.find(_TAG_V)
            raw_value: Any = v_elem.text if v_elem is not None else None

            # 尝试数值化
            if raw_value is not None:
                try:
                    raw_value = int(raw_value)
                except ValueError:
                    try:
                        raw_value = float(raw_value)
                    except ValueError:
                        pass  # 保持字符串

            result.append({
                "cell": f"{ws_title}!{cell_ref}",
                "formula": formula_text,
                "value": raw_value,
            })

            if len(result) >= _MAX_FORMULAS:
                break

        # 释放已处理节点，保持内存恒定
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]

    return result
