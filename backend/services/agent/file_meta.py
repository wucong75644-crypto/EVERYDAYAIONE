"""文件元数据生成模块（.meta.json schema / sample / stats / formulas / issues）。"""
from __future__ import annotations

import json
import re
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
_SAMPLE_ROWS = 5           # 兼容保留（其他模块可能引用）
_SAMPLE_HEAD = 4           # 开头取几行
_SAMPLE_MIDDLE = 2         # 中段取几行（解决 lost-in-the-middle）
_SAMPLE_TAIL = 4           # 末尾取几行
_SAMPLE_BOUNDARY_MAX = 2   # 边界补充上限（来自 prescan anomalies）
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
    merged_cells: list[dict[str, Any]] = field(default_factory=list)
    raw_preserved: bool = True    # 原始结构是否被保留（未自动 ffill）
    grain: dict[str, Any] = field(default_factory=dict)    # 粒度检测结果
    prescan: dict[str, Any] = field(default_factory=dict)  # AI 坐标预探测结论
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
    merged_ranges: list[tuple[int, int, int, int]] | None = None,
    prescan_result: Any = None,
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
    meta.sample = _build_sample(df, cleaning_report.data_start_row, prescan_result)

    # ── stats ──
    meta.stats = {
        "missing_values": int(df.isnull().sum().sum()),
        "duplicates": int(df.duplicated().sum()),
    }

    # ── formulas ──
    meta.formulas = formulas or []

    # ── issues（合并来自 cleaning_report + DataFrame 扫描 + 额外标注）──
    meta.issues = cleaning_report.issues.copy()  # excel_cleaner 的结构化标注
    meta.issues.extend(_scan_issues(df, cleaning_report.data_start_row))
    if formula_skip_reason:
        meta.issues.append({
            "type": "formula_skipped",
            "severity": "info",
            "location": {},
            "preserved": True,
            "action": "公式信息未提取",
            "recovery_hint": formula_skip_reason,
        })

    # ── merged_cells（合并单元格信息，不自动处理，AI 决定）──
    if merged_ranges:
        for min_row, max_row, min_col, max_col in merged_ranges:
            col_start = _col_index_to_letter(min_col - 1)
            col_end = _col_index_to_letter(max_col - 1)
            meta.merged_cells.append({
                "range": f"{col_start}{min_row}:{col_end}{max_row}",
                "rows": [min_row, max_row],
                "cols": [min_col, max_col],
            })
        meta.raw_preserved = (cleaning_report.merged_cols_filled == 0)
        if meta.raw_preserved:
            meta.issues.append({
                "type": "merged_cells",
                "severity": "info",
                "location": {},
                "preserved": True,
                "action": f"检测到{len(merged_ranges)}个合并区域，原始数据保留",
                "recovery_hint": "根据业务语义决定：ffill（纵向填充）或重命名列（横向分组）",
            })
        else:
            meta.issues.append({
                "type": "merged_cells",
                "severity": "info",
                "location": {},
                "preserved": False,
                "action": f"检测到{len(merged_ranges)}个合并区域，已按merge range精确填充{cleaning_report.merged_cols_filled}个单元格",
                "recovery_hint": "合并区域空值已自动填充，数据可直接查询",
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

        # 唯一值计数（所有列都存，供粒度检测使用）
        if len(non_null) > 0:
            unique_count = non_null.nunique()
            entry["unique_count"] = unique_count
            # 分类检测仍然只对 string 且 ≤20 时存 categories
            if entry["type"] == "string" and 1 < unique_count <= _CATEGORY_THRESHOLD:
                top_values = non_null.value_counts().head(10).index.tolist()
                entry["categories"] = [str(v) for v in top_values]

        schema[col_str] = entry
    return schema


# ── 粒度检测 ──

_GROUP_KEY_HINTS = re.compile(
    r'(订单|编号|单号|发票号|ID|order|invoice|bill|no\.?$)', re.IGNORECASE
)


def _detect_grain(
    df: pd.DataFrame,
    schema: dict[str, dict[str, Any]],
    actual_row_count: int,
) -> dict[str, Any] | None:
    """检测一对多粒度关系。返回 grain dict 或 None（不确定时不输出）。

    复用 schema 里已有的 unique_count，只在找到分组键后才做 groupby。
    注意：schema 的 unique_count 基于传入的 df（大文件时是采样），
    所以 ratio 计算用 len(df) 而非 actual_row_count。
    actual_row_count 只用于输出展示。
    """
    sample_rows = len(df)
    if sample_rows < 10 or actual_row_count < 10:
        return None

    # Step 1: 从 schema 找候选分组键
    # unique_count 来自 _build_schema 对 df 的计算，ratio 必须基于 df 行数
    candidates: list[tuple[str, int, float, float]] = []
    for col_name, info in schema.items():
        if info.get("type") not in ("string", "integer"):
            continue
        uc = info.get("unique_count", 0)
        if uc < 5:
            continue
        ratio = uc / sample_rows  # 用采样行数算 ratio，不用 actual_row_count
        if not (0.15 <= ratio <= 0.85):
            continue
        avg_size = sample_rows / uc  # 采样内的平均组大小
        if avg_size < 1.5:
            continue
        name_boost = 2.0 if _GROUP_KEY_HINTS.search(col_name) else 1.0
        score = name_boost * (1.0 - abs(ratio - 0.45))
        candidates.append((col_name, uc, avg_size, score))

    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[3])
    group_key, unique_count, avg_size, _ = candidates[0]

    # 验证：至少 30% 的组有多行
    if group_key not in df.columns:
        return None
    group_sizes = df.groupby(group_key).size()
    if (group_sizes > 1).mean() < 0.3:
        return None

    # Step 2: 分类其他列（统一用 round(4) + groupby.nunique）
    order_level: list[str] = []
    line_level: list[str] = []

    for col_name, info in schema.items():
        if col_name == group_key or col_name not in df.columns:
            continue
        if info.get("null_ratio", 0) > 0.9:
            continue  # 高空值列跳过
        col_data = df[col_name]
        if col_data.isna().all():
            continue

        try:
            if info.get("type") in ("integer", "decimal"):
                rounded = col_data.round(4)
                group_unique = rounded.groupby(df[group_key]).nunique()
            else:
                group_unique = df.groupby(group_key)[col_name].nunique()
            if (group_unique == 1).mean() > 0.95:
                order_level.append(col_name)
            else:
                line_level.append(col_name)
        except (TypeError, ValueError):
            line_level.append(col_name)

    # Step 3: 置信度门控 — 至少 1 个数值列是订单级
    numeric_order = [
        c for c in order_level
        if schema.get(c, {}).get("type") in ("integer", "decimal")
    ]
    if not numeric_order:
        return None

    # 输出用实际行数（大文件时采样的 avg_size 按比例换算）
    actual_avg_size = round(actual_row_count / max(unique_count, 1), 1)
    return {
        "group_key": group_key,
        "unique_count": int(unique_count),
        "row_count": actual_row_count,
        "avg_group_size": actual_avg_size,
        "order_level_fields": order_level,
        "line_level_fields": line_level,
    }


def _build_sample(
    df: pd.DataFrame, data_start_row: int, prescan_result: Any = None,
) -> dict[str, list[dict]]:
    """提取代表性样本数据，带 Excel 原始行号。

    覆盖三段（head + middle + tail），共约 10 行：
    - head 4 行：开头
    - middle 2 行：中段（避免"lost in the middle"）
    - tail 4 行：末尾（含可能的汇总行）

    边界补充（boundary，可选 0-2 行）：
    - 复用 prescan_result.anomalies[*].sample_rows ——
      AI 在 schema 生成前已经判定过的代表性异常行
    - 零额外计算成本（不调 idxmax）
    """
    n_head = min(_SAMPLE_HEAD, len(df))
    n_tail = min(_SAMPLE_TAIL, len(df))
    if n_head == 0 and n_tail == 0:
        return {"head": [], "middle": [], "tail": [], "boundary": []}

    head_df = df.head(n_head)
    tail_df = df.tail(n_tail)

    # middle 段（仅文件够长时才生成，避免和 head/tail 重叠）
    if len(df) > n_head + n_tail + _SAMPLE_MIDDLE:
        mid_start = len(df) // 2
        middle_df = df.iloc[mid_start:mid_start + _SAMPLE_MIDDLE]
    else:
        middle_df = df.iloc[0:0]

    # boundary 段：复用 prescan 的 AI 异常行号（零额外扫描）
    boundary_df = df.iloc[0:0]
    if prescan_result is not None:
        anomalies = getattr(prescan_result, "anomalies", None) or []
        covered = set(head_df.index) | set(middle_df.index) | set(tail_df.index)
        boundary_indices: list[int] = []
        for anomaly in anomalies:
            for excel_row in (anomaly.get("sample_rows") or []):
                df_idx = int(excel_row) - data_start_row
                if 0 <= df_idx < len(df) and df_idx not in covered:
                    boundary_indices.append(df_idx)
                    covered.add(df_idx)
                    if len(boundary_indices) >= _SAMPLE_BOUNDARY_MAX:
                        break
            if len(boundary_indices) >= _SAMPLE_BOUNDARY_MAX:
                break
        if boundary_indices:
            boundary_df = df.loc[boundary_indices]

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
        "middle": _rows_to_dicts(middle_df),
        "tail": _rows_to_dicts(tail_df),
        "boundary": _rows_to_dicts(boundary_df),
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
                "severity": "warning",
                "location": {
                    "row": int(first_null_idx) + data_start_row,
                    "col": _col_index_to_letter(i),
                    "raw_col_name": col_str,
                },
                "preserved": True,
                "action": f"{col_str}列有{null_count}个缺失值（NULL 保留）",
                "recovery_hint": f"填充: df['{col_str}'].fillna(均值/0/ffill)，或查询时 WHERE {col_str} IS NOT NULL",
            })

        if len(issues) >= _MAX_ISSUES:
            break

    # 重复行检测
    dup_count = int(df.duplicated().sum())
    if dup_count > 0:
        first_dup_idx = df.duplicated().idxmax()
        issues.append({
            "type": "duplicate_row",
            "severity": "warning",
            "location": {"row": int(first_dup_idx) + data_start_row},
            "preserved": True,
            "action": f"有{dup_count}条重复数据（保留未删除）",
            "recovery_hint": "去重: df.drop_duplicates()，或查询时 SELECT DISTINCT",
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
    row_count = s.get('row_count', 0)
    lines.append(
        f"数据概览：{row_count}行 × {s.get('col_count', 0)}列"
        f"，{s.get('sheet_count', 1)} 个 Sheet"
    )
    # 规模警告：schema-aware reasoning（行业标准做法，对标 OpenAI Files API）
    # 让 LLM 看到具体规模 → 自然选择 SQL 聚合而非全量 .df()
    if row_count >= 100_000:
        lines.append(
            f"⚠️ 大数据（{row_count:,}行）：禁止 SELECT * .df() 全量加载，"
            "会 OOM。必须先 SQL 聚合/筛选后再 .df()。"
        )
    elif row_count >= 10_000:
        lines.append(
            f"提示：{row_count:,}行 中等规模，建议 SQL 先 WHERE/GROUP BY 过滤再 .df()。"
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

    # grain warning
    if meta.grain:
        g = meta.grain
        gk = g["group_key"]
        lines.append(
            f"⚠ 数据粒度：明细表（每行 ≠ 一个{gk}，"
            f"平均每组 {g.get('avg_group_size', 0)} 行）"
        )
        lines.append(
            f"  分组键: {gk}（{g['unique_count']}个唯一值 / {g['row_count']}行）"
        )
        ol = g.get("order_level_fields", [])
        ll = g.get("line_level_fields", [])
        numeric_ol = [c for c in ol if meta.schema.get(c, {}).get("type") in ("integer", "decimal")]
        dim_ol = [c for c in ol if c not in numeric_ol]
        if numeric_ol:
            lines.append(
                f"  ⚠ 订单级数值（同一{gk}内值相同，SUM前必须先按{gk}去重）："
            )
            lines.append(f"    {', '.join(numeric_ol)}")
        if dim_ol:
            lines.append(f"  订单级维度（同组内相同）：{', '.join(dim_ol[:8])}")
        if ll:
            lines.append(f"  明细级字段（每行独立，可直接SUM/COUNT）：{', '.join(ll[:8])}")
        if numeric_ol:
            lines.append(
                f"  正确写法: SELECT SUM(\"{numeric_ol[0]}\") FROM "
                f"(SELECT DISTINCT \"{gk}\", \"{numeric_ol[0]}\" FROM data)"
            )
        lines.append("")

    # sample — 三段覆盖 + AI 标注边界
    if meta.sample:
        lines.append("样本数据：")
        for rd in (meta.sample.get("head") or []):
            fields = {k: v for k, v in rd.items() if k != "_row"}
            lines.append(f"  Row {rd.get('_row', '?')}: {fields}")
        middle = meta.sample.get("middle") or []
        if middle:
            lines.append("  ... [中段] ...")
            for rd in middle:
                fields = {k: v for k, v in rd.items() if k != "_row"}
                lines.append(f"  Row {rd.get('_row', '?')}: {fields}")
        tail = meta.sample.get("tail") or []
        if tail:
            lines.append("  ... [末尾] ...")
            for rd in tail:
                fields = {k: v for k, v in rd.items() if k != "_row"}
                lines.append(f"  Row {rd.get('_row', '?')}: {fields}")
        boundary = meta.sample.get("boundary") or []
        if boundary:
            lines.append("  📌 AI 标注的代表性行（异常/边界值，预探测时判定）：")
            for rd in boundary:
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
    if meta.merged_cells:
        if meta.raw_preserved:
            lines.append(f"合并单元格（{len(meta.merged_cells)}个，未自动处理，需你根据业务语义决定）：")
        else:
            lines.append(f"合并单元格（{len(meta.merged_cells)}个，已自动精确填充，非全列ffill）：")
        for mc in meta.merged_cells[:5]:
            lines.append(f"  {mc.get('range', '?')}")
    for issue in meta.issues[:8]:
        sev = issue.get("severity", "info")
        action = issue.get("action", issue.get("suggestion", ""))
        hint = issue.get("recovery_hint", "")
        loc = issue.get("location", {})
        loc_parts = []
        if loc.get("row"):
            loc_parts.append(f"Row {loc['row']}")
        if loc.get("col"):
            loc_parts.append(f"{loc['col']}列")
        if loc.get("cols"):
            loc_parts.append(f"列: {loc['cols']}")
        if loc.get("rows"):
            loc_parts.append(f"Row: {loc['rows'][:5]}")
        loc_str = " ".join(loc_parts) if loc_parts else ""
        preserved = "✓保留" if issue.get("preserved") else "✗已转换"
        line = f"[{sev}] {loc_str} {action} [{preserved}]"
        if hint:
            line += f"\n    → {hint}"
        lines.append(line)
    dsr = (meta.cleaning or {}).get("data_start_row", 2)
    lines.append(f"\n行号映射：Excel行号 = Parquet索引 + {dsr}")

    return "\n".join(lines)
