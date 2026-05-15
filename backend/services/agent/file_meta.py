"""文件元数据生成模块（.meta.json schema / sample / stats / formulas / issues）。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pandas as pd
from loguru import logger

from services.agent.excel_cleaner import (
    CleaningReport,
    _parse_sheet_tags,
    _resolve_sheet_xml_path,
)

# ── 常量 ──
_SAMPLE_ROWS = 5           # head/tail 各取几行
_CATEGORY_THRESHOLD = 20   # unique ≤ 此值视为分类列
_MAX_ISSUES = 50           # issues 最多记录条数


def _col_index_to_letter(idx: int) -> str:
    """0-indexed 列索引 → Excel 列字母（0=A, 1=B, ..., 25=Z, 26=AA）。"""
    result = ""
    n = idx
    while True:
        result = chr(ord("A") + n % 26) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result


@dataclass
class FileMeta:
    """完整的 .meta.json 结构。"""

    version: str = "1.0"
    status: str = "pass"                  # pass | warning | fail
    source_file: str = ""
    processed_at: str = ""
    last_accessed_at: str = ""
    processed_by: str = "L1"              # L1 | L2
    summary: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    sample: dict[str, list[dict]] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)
    formulas: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    cleaning: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_file_meta(
    df: pd.DataFrame,
    cleaning_report: CleaningReport,
    source_file: str,
    sheet_count: int = 1,
    formulas: list[dict[str, Any]] | None = None,
    formula_skip_reason: str = "",
) -> FileMeta:
    """从 DataFrame + CleaningReport 生成完整 FileMeta。"""
    now = datetime.now().isoformat(timespec="seconds")
    row_count, col_count = df.shape

    meta = FileMeta(
        source_file=source_file,
        processed_at=now,
        last_accessed_at=now,
    )

    # ── summary ──
    meta.summary = {
        "description": f"{Path(source_file).name}，{row_count}行×{col_count}列",
        "row_count": row_count,
        "col_count": col_count,
        "sheet_count": sheet_count,
    }

    # ── schema（带列号）──
    meta.schema = _build_schema(df, cleaning_report.data_start_row)

    # ── sample（带行号）──
    meta.sample = _build_sample(df, cleaning_report.data_start_row)

    # ── stats ──
    meta.stats = {
        "missing_values": int(df.isnull().sum().sum()),
        "duplicates": int(df.duplicated().sum()),
    }

    # ── formulas ──
    meta.formulas = formulas or []

    # ── issues（从 DataFrame 扫描）──
    meta.issues = _scan_issues(df, cleaning_report.data_start_row)
    if formula_skip_reason:
        meta.issues.append({
            "type": "formula_skipped",
            "location": {},
            "severity": "info",
            "count": 0,
            "suggestion": formula_skip_reason,
        })

    # ── cleaning（保留现有 CleaningReport 字段）──
    cr_dict = asdict(cleaning_report)
    cr_dict["original_shape"] = list(cr_dict["original_shape"])
    cr_dict["final_shape"] = list(cr_dict["final_shape"])
    meta.cleaning = cr_dict

    # ── status 判定 ──
    meta.status = _determine_status(meta.issues)

    # ── confidence ──
    if meta.status == "fail":
        meta.confidence = 0.5
    elif meta.status == "warning":
        meta.confidence = 0.8
    else:
        meta.confidence = 1.0

    return meta


def _build_schema(df: pd.DataFrame, data_start_row: int) -> dict[str, dict[str, Any]]:
    """为每列生成 schema 信息（含 Excel 列号）。"""
    schema: dict[str, dict[str, Any]] = {}
    for i, col_name in enumerate(df.columns):
        col_str = str(col_name)
        if col_str.startswith("_is_"):
            continue
        col_data = df.iloc[:, i]
        non_null = col_data.dropna()

        entry: dict[str, Any] = {
            "col": _col_index_to_letter(i),
            "col_index": i,
            "type": _infer_dtype(col_data),
            "null_ratio": round(1 - len(non_null) / max(len(col_data), 1), 3),
        }

        # 数值列加范围
        if entry["type"] in ("integer", "decimal"):
            try:
                entry["min"] = float(non_null.min()) if len(non_null) > 0 else None
                entry["max"] = float(non_null.max()) if len(non_null) > 0 else None
            except (TypeError, ValueError):
                pass

        # 日期列加范围
        if entry["type"] == "datetime" and len(non_null) > 0:
            try:
                entry["range"] = [
                    str(non_null.min())[:10],
                    str(non_null.max())[:10],
                ]
            except (TypeError, ValueError):
                pass

        # 分类检测
        if entry["type"] == "string" and len(non_null) > 0:
            unique_count = non_null.nunique()
            if 1 < unique_count <= _CATEGORY_THRESHOLD:
                top_values = non_null.value_counts().head(10).index.tolist()
                entry["categories"] = [str(v) for v in top_values]

        schema[col_str] = entry
    return schema


def _build_sample(
    df: pd.DataFrame, data_start_row: int,
) -> dict[str, list[dict]]:
    """提取 head/tail 样本数据，带 Excel 原始行号。"""
    n = min(_SAMPLE_ROWS, len(df))
    if n == 0:
        return {"head": [], "tail": []}

    head_df = df.head(n)
    tail_df = df.tail(n)

    def _rows_to_dicts(subset: pd.DataFrame) -> list[dict]:
        result = []
        for idx in subset.index:
            row_dict: dict[str, Any] = {"_row": int(idx) + data_start_row}
            for col in subset.columns:
                if str(col).startswith("_is_"):
                    continue
                val = subset.at[idx, col]
                row_dict[str(col)] = _serialize_value(val)
            result.append(row_dict)
        return result

    return {
        "head": _rows_to_dicts(head_df),
        "tail": _rows_to_dicts(tail_df),
    }


def _scan_issues(
    df: pd.DataFrame, data_start_row: int,
) -> list[dict[str, Any]]:
    """扫描 DataFrame 中的数据质量问题（带位置坐标）。"""
    issues: list[dict[str, Any]] = []

    for i, col_name in enumerate(df.columns):
        col_str = str(col_name)
        if col_str.startswith("_is_"):
            continue
        col_data = df.iloc[:, i]
        null_count = int(col_data.isnull().sum())
        if null_count > 0:
            # 找第一个缺失值的行号
            first_null_idx = col_data.isnull().idxmax()
            issues.append({
                "type": "missing_value",
                "location": {
                    "row": int(first_null_idx) + data_start_row,
                    "col": _col_index_to_letter(i),
                    "raw_col_name": col_str,
                },
                "severity": "warning",
                "count": null_count,
                "suggestion": f"{col_str}列有{null_count}个缺失值",
            })

        if len(issues) >= _MAX_ISSUES:
            break

    # 重复行检测
    dup_count = int(df.duplicated().sum())
    if dup_count > 0:
        first_dup_idx = df.duplicated().idxmax()
        issues.append({
            "type": "duplicate_row",
            "location": {"row": int(first_dup_idx) + data_start_row},
            "severity": "warning",
            "count": dup_count,
            "suggestion": f"有{dup_count}条重复数据",
        })

    return issues


def _determine_status(issues: list[dict[str, Any]]) -> str:
    """根据 issues 判定状态。"""
    errors = [i for i in issues if i.get("severity") == "error"]
    if errors:
        return "fail"
    if issues:
        return "warning"
    return "pass"


def _infer_dtype(series: pd.Series) -> str:
    """从 pandas dtype 推断标准类型名称。"""
    dtype_str = str(series.dtype)
    if dtype_str in ("Int64", "int64", "int32", "int16", "int8"):
        return "integer"
    if dtype_str in ("float64", "float32", "Float64"):
        return "decimal"
    if "datetime" in dtype_str:
        return "datetime"
    if dtype_str == "bool":
        return "boolean"
    return "string"


def _serialize_value(val: Any) -> Any:
    """将 pandas 值序列化为 JSON 兼容类型。"""
    if pd.isna(val):
        return None
    if isinstance(val, (pd.Timestamp,)):
        return str(val)[:19]
    if isinstance(val, (int, float, str, bool)):
        return val
    return str(val)

# ── 公式提取 ──

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


# ── 读写函数 ──


def write_file_meta(cache_path: str, meta: FileMeta) -> None:
    """将 FileMeta 写入 .meta.json。"""
    meta_path = cache_path.replace(".parquet", ".meta.json")
    data = meta.to_dict()
    try:
        Path(meta_path).write_text(
            json.dumps(data, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Failed to write file meta: {e}")


def read_file_meta(cache_path: str) -> FileMeta | None:
    """读取 .meta.json，兼容新旧两种格式。"""
    meta_path = cache_path.replace(".parquet", ".meta.json")
    p = Path(meta_path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # 兼容旧格式（只有 CleaningReport 字段，无 version）
        if "version" not in data:
            return None
        return FileMeta(**data)
    except Exception:
        return None


def format_file_view(meta: FileMeta) -> str:
    """将 FileMeta 格式化为 AI context 注入的文件视图文本。"""
    lines: list[str] = []
    src = meta.source_file
    lines.append(f"[文件已就绪] {src}")
    lines.append("")

    # summary
    s = meta.summary
    lines.append(
        f"数据概览：{s.get('row_count', 0)}行 × {s.get('col_count', 0)}列"
        f"，{s.get('sheet_count', 1)} 个 Sheet"
    )
    lines.append("")

    # schema
    if meta.schema:
        lines.append(f"字段 schema（{len(meta.schema)}列）：")
        for col_name, info in meta.schema.items():
            col_letter = info.get("col", "?")
            dtype = info.get("type", "unknown")
            null_pct = info.get("null_ratio", 0)
            null_str = f"{null_pct*100:.1f}%" if null_pct > 0 else "0%"

            extra = ""
            if "min" in info and "max" in info:
                extra = f" | 范围: {info['min']} ~ {info['max']}"
            elif "range" in info:
                extra = f" | {info['range'][0]} ~ {info['range'][1]}"
            elif "categories" in info:
                cats = ", ".join(info["categories"][:5])
                extra = f" | 枚举: {cats}"

            lines.append(f"  {col_letter} | {col_name} | {dtype} | 空值: {null_str}{extra}")
        lines.append("")

    # sample
    if meta.sample:
        lines.append("样本数据：")
        for rd in (meta.sample.get("head") or []):
            fields = {k: v for k, v in rd.items() if k != "_row"}
            lines.append(f"  Row {rd.get('_row', '?')}: {fields}")
        tail = (meta.sample.get("tail") or [])[-2:]
        if tail:
            lines.append("  ...")
            for rd in tail:
                fields = {k: v for k, v in rd.items() if k != "_row"}
                lines.append(f"  Row {rd.get('_row', '?')}: {fields}")
        lines.append("")

    # stats + formulas + issues
    st = meta.stats or {}
    stat_parts = []
    if st.get("missing_values", 0) > 0:
        stat_parts.append(f"缺失值 {st['missing_values']}")
    if st.get("duplicates", 0) > 0:
        stat_parts.append(f"重复行 {st['duplicates']}")
    if stat_parts:
        lines.append(f"统计：{' | '.join(stat_parts)}")

    for f in meta.formulas[:3]:
        lines.append(f"公式：{f.get('cell','?')} = {f.get('formula','?')} → {f.get('value','?')}")
    for issue in meta.issues[:5]:
        loc = issue.get("location", {})
        loc_str = f"Row {loc.get('row', '?')}" + (f" {loc['col']}列" if "col" in loc else "")
        lines.append(f"[{issue.get('severity','info')}] {loc_str} — {issue.get('suggestion','')}")
    dsr = (meta.cleaning or {}).get("data_start_row", 2)
    lines.append(f"\n行号映射：Excel行号 = Parquet索引 + {dsr}")

    return "\n".join(lines)
