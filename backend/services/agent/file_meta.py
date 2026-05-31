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

    # 跨段特征去重：避免 head/middle/tail/boundary 中"同质化"行重复占字符
    # 签名规则：数值列看是否非零（不看具体值），字符串列看前 8 字符 hash
    return _dedup_samples_by_signature({
        "head": _rows_to_dicts(head_df),
        "middle": _rows_to_dicts(middle_df),
        "tail": _rows_to_dicts(tail_df),
        "boundary": _rows_to_dicts(boundary_df),
    }, df)


def _dedup_samples_by_signature(
    sample: dict[str, list[dict]], df: pd.DataFrame,
) -> dict[str, list[dict]]:
    """跨段去重：保留特征签名不同的行，去掉同质化重复样本。

    每行计算签名（数值是否非零 + 字符串短 hash），按出现顺序保留首次出现的行。
    各段最少保留 1 行（即使被去重也保 head[0]/tail[-1]，保结构完整）。
    """
    # 计算列类型映射
    col_types: dict[str, str] = {}
    for col in df.columns:
        col_str = str(col)
        if col_str.startswith("_is_"):
            continue
        try:
            kind = df[col].dtype.kind
            if kind in ("i", "u", "f"):
                col_types[col_str] = "number"
            else:
                col_types[col_str] = "string"
        except Exception:
            col_types[col_str] = "string"

    def _sig(row: dict) -> tuple:
        sig: list = []
        for col, t in col_types.items():
            val = row.get(col)
            if val is None or val == "":
                sig.append("∅")
            elif t == "number":
                try:
                    sig.append("+" if float(val) > 0 else "0")
                except (TypeError, ValueError):
                    sig.append(str(val)[:8])
            else:
                sig.append(hash(str(val)[:8]))
        return tuple(sig)

    seen: set = set()
    out: dict[str, list[dict]] = {"head": [], "middle": [], "tail": [], "boundary": []}

    # 各段必保第 1 行（保结构完整，让 LLM 知道有数据）
    for seg in ("head", "middle", "tail", "boundary"):
        rows = sample.get(seg) or []
        if not rows:
            continue
        # 首行无条件保留 + 记签名
        first = rows[0]
        out[seg].append(first)
        seen.add(_sig(first))
        # 后续行按签名去重
        for row in rows[1:]:
            s = _sig(row)
            if s in seen:
                continue
            seen.add(s)
            out[seg].append(row)
    return out


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
    """将 FileMeta 格式化为 AI context 注入的文件视图文本。

    U 形 attention 优化（[arXiv 2406.16008] Found in the Middle）:
      - 头部锚定: 概览 + 大数据警告 + 订单级字段警告（核心约束）
      - 中段: schema（含 🔴 订单级行级标签）+ 样本数据 + 数据质量
      - 尾部锚定: 再次提醒订单级处理规则（双锚定防中段遗漏）

    Markdown 结构化分块: 千问/OpenAI/Gemini 都认 ## 标题
    """
    lines: list[str] = []
    src = meta.source_file
    s = meta.summary or {}
    row_count = s.get("row_count", 0)
    grain = meta.grain or {}

    # 预计算订单级字段（schema 标签 + 警告共用）
    order_level_fields: list[str] = grain.get("order_level_fields", []) or []
    numeric_ol = [
        c for c in order_level_fields
        if meta.schema.get(c, {}).get("type") in ("integer", "decimal")
    ]
    group_key = grain.get("group_key", "")

    # ============================================================
    # [HEAD] 顶部锚定：核心警告（U 形 attention 头部高峰）
    # ============================================================
    lines.append(f"[文件已就绪] {src}")
    lines.append("")
    lines.append("## 📊 数据概览")
    lines.append(
        f"- 规模: {row_count:,} 行 × {s.get('col_count', 0)} 列，"
        f"{s.get('sheet_count', 1)} 个 Sheet"
    )
    if row_count >= 100_000:
        lines.append(
            f"- ⚠️ **大数据**({row_count:,} 行): 禁止 `SELECT * .df()` 全量加载，"
            "会 OOM。必须先 SQL 聚合/筛选后再 .df()。"
        )
    elif row_count >= 10_000:
        lines.append(
            f"- 提示: {row_count:,} 行中等规模，建议 SQL 先 WHERE/GROUP BY 过滤再 .df()。"
        )
    if numeric_ol:
        lines.append(
            f"- 🔴 **订单级字段必读**: `{', '.join(numeric_ol)}` 在同一 {group_key} 内值重复，"
            f"SUM 前必须先 DISTINCT，否则数字虚高 10-30%。"
        )
        lines.append(
            f"  正确写法: `SELECT SUM(\"{numeric_ol[0]}\") FROM "
            f"(SELECT DISTINCT \"{group_key}\", \"{numeric_ol[0]}\" FROM data)`"
        )
    lines.append("")

    # ============================================================
    # [MID] schema — 行级 🔴 订单级标签
    # ============================================================
    if meta.schema:
        lines.append(f"## 📐 字段 schema（{len(meta.schema)} 列）")
        order_level_set = set(order_level_fields)
        for col_name, info in meta.schema.items():
            col_letter = info.get("col", "?")
            dtype = info.get("type", "unknown")
            null_pct = info.get("null_ratio", 0)

            extra_parts: list[str] = []
            if null_pct > 0:
                extra_parts.append(f"空值: {null_pct*100:.1f}%")
            if "min" in info and "max" in info:
                extra_parts.append(f"范围: {info['min']} ~ {info['max']}")
            elif "range" in info:
                extra_parts.append(f"{info['range'][0]} ~ {info['range'][1]}")
            elif "categories" in info:
                cats = ", ".join(info["categories"][:5])
                extra_parts.append(f"枚举: {cats}")

            tag = ""
            if col_name in order_level_set:
                if col_name in numeric_ol:
                    tag = " | 🔴 订单级（SUM 前 DISTINCT）"
                else:
                    tag = " | 订单级维度"
            extra = " | ".join(extra_parts)
            extra = f" | {extra}" if extra else ""
            lines.append(f"  {col_letter} | {col_name} | {dtype}{extra}{tag}")
        lines.append("")

    # ============================================================
    # [MID] 数据粒度详情（订单级头部已警告，这里给数字背景）
    # ============================================================
    if grain:
        lines.append("## 📦 数据粒度")
        lines.append(
            f"- 明细表: 每订单平均 {grain.get('avg_group_size', 0)} 行（"
            f"{grain['unique_count']} 个 {group_key} / {grain['row_count']} 行）"
        )
        ll = grain.get("line_level_fields", []) or []
        if ll:
            lines.append(f"- 明细级字段（每行独立，可直接 SUM/COUNT）: {', '.join(ll[:8])}")
        lines.append("")

    # ============================================================
    # [MID] 样本数据（已在 _build_sample 跨段去重）
    # ============================================================
    if meta.sample:
        lines.append("## 📋 样本数据")
        head_rows = meta.sample.get("head") or []
        middle_rows = meta.sample.get("middle") or []
        tail_rows = meta.sample.get("tail") or []
        boundary_rows = meta.sample.get("boundary") or []

        def _emit(rows: list[dict], tag: str) -> None:
            for rd in rows:
                fields = {k: v for k, v in rd.items() if k != "_row"}
                lines.append(f"  Row {rd.get('_row', '?')} [{tag}]: {fields}")

        _emit(head_rows, "head")
        _emit(middle_rows, "middle")
        _emit(tail_rows, "tail")
        if boundary_rows:
            _emit(boundary_rows, "边界")
        lines.append("")

    # ============================================================
    # [MID] 数据质量（issues 合并 + 智能压缩）
    # ============================================================
    notes_lines: list[str] = []
    st = meta.stats or {}
    if st.get("missing_values", 0) > 0:
        notes_lines.append(f"- 缺失值: {st['missing_values']}")
    if st.get("duplicates", 0) > 0:
        notes_lines.append(f"- 重复行: {st['duplicates']}")

    for f in meta.formulas[:3]:
        notes_lines.append(
            f"- 公式: {f.get('cell','?')} = {f.get('formula','?')} → {f.get('value','?')}"
        )

    if meta.merged_cells:
        if meta.raw_preserved:
            notes_lines.append(
                f"- 合并单元格 {len(meta.merged_cells)} 个（未自动处理）"
            )
        else:
            notes_lines.append(
                f"- 合并单元格 {len(meta.merged_cells)} 个（已精确填充）"
            )

    # issues — 智能合并：同行的 N 个缺失值 warning 合并成 1 行
    notes_lines.extend(_compress_issues(meta.issues[:20]))

    if notes_lines:
        lines.append("## 📝 数据质量")
        lines.extend(notes_lines)
        lines.append("")

    # ============================================================
    # [TAIL] 尾部锚定：再次提醒 + 多字段聚合范式
    # 行业最佳实践（One-shot template + ReAct fallback）:
    # 在 LLM 容易"自创错误 JOIN 写法"的多字段场景，直接给完整代码模板
    # ============================================================
    if numeric_ol:
        lines.append("## ⚠️ 再次提醒（重要）")
        lines.append(
            f"订单级字段 `{', '.join(numeric_ol)}` 在 {group_key} 内值重复，"
            f"SUM 前必须先 `DISTINCT {group_key}` 去重。"
        )

        # 多字段聚合范式（明细级 + 订单级混合统计时必用）
        line_level_numeric = [
            c for c in (grain.get("line_level_fields", []) or [])
            if meta.schema.get(c, {}).get("type") in ("integer", "decimal")
        ]
        if line_level_numeric:
            first_ll = line_level_numeric[0]
            first_ol = numeric_ol[0]
            lines.append("")
            lines.append("📌 多字段聚合范式（同时统计明细级 + 订单级字段时必用）:")
            lines.append("```python")
            lines.append("# 步骤1：明细级字段直接 SUM")
            lines.append("detail = duckdb.sql(f\"\"\"")
            lines.append(f'    SELECT 分组列, SUM("{first_ll}") AS "{first_ll}"')
            lines.append(f"    FROM read_parquet('{{path}}') GROUP BY 分组列")
            lines.append('""").df()')
            lines.append("")
            lines.append("# 步骤2：订单级字段先 DISTINCT 再 SUM")
            lines.append("order_ = duckdb.sql(f\"\"\"")
            lines.append(f'    SELECT 分组列, SUM("{first_ol}") AS "{first_ol}"')
            lines.append(
                f'    FROM (SELECT DISTINCT "{group_key}", 分组键, "{first_ol}" '
                f"FROM read_parquet('{{path}}'))"
            )
            lines.append("    GROUP BY 分组列")
            lines.append('""").df()')
            lines.append("")
            lines.append("# 步骤3：pandas merge 合并两个聚合结果（不要用 SQL 三表 JOIN，会导致数据重复）")
            lines.append("result = detail.merge(order_, on='分组列', how='left')")
            lines.append("```")

    dsr = (meta.cleaning or {}).get("data_start_row", 2)
    lines.append(f"\n行号映射: Excel 行号 = Parquet 索引 + {dsr}")

    return "\n".join(lines)


def _compress_issues(issues: list[dict]) -> list[str]:
    """智能合并 issues 为压缩后的 notes 行。

    特殊场景: 同行的多个"X 列有 N 个缺失值"warning 合并为 1 行
    （如 Row 5001 是 _is_summary 汇总行，5 列都缺失 → 合并为
    "Row 5001 多列缺失，大概率是 _is_summary 汇总行"）
    """
    if not issues:
        return []

    by_row: dict[int, list[dict]] = {}
    other: list[dict] = []
    for issue in issues:
        loc = issue.get("location", {}) or {}
        row = loc.get("row")
        if issue.get("type") == "missing_value" and isinstance(row, int):
            by_row.setdefault(row, []).append(issue)
        else:
            other.append(issue)

    out: list[str] = []
    for row, group in by_row.items():
        if len(group) >= 3:
            cols = [(i.get("location") or {}).get("col", "?") for i in group]
            out.append(
                f"- Row {row} 多列缺失（{', '.join(cols[:5])}{'...' if len(cols) > 5 else ''}），"
                f"大概率是 _is_summary 汇总行，查询时加 `WHERE _is_summary = false` 排除"
            )
        else:
            for i in group:
                out.append(_format_single_issue(i))
    for i in other:
        out.append(_format_single_issue(i))
    return out


def _format_single_issue(issue: dict) -> str:
    """格式化单条 issue 为一行 markdown bullet。"""
    sev = issue.get("severity", "info")
    action = issue.get("action", issue.get("suggestion", ""))
    loc = issue.get("location", {}) or {}
    loc_parts: list[str] = []
    if loc.get("row"):
        loc_parts.append(f"Row {loc['row']}")
    if loc.get("col"):
        loc_parts.append(f"{loc['col']}列")
    if loc.get("cols"):
        cols_str = (
            loc["cols"] if isinstance(loc["cols"], str)
            else ", ".join(map(str, loc["cols"][:5]))
        )
        loc_parts.append(f"列: {cols_str}")
    if loc.get("rows"):
        rows = loc["rows"] if isinstance(loc["rows"], list) else [loc["rows"]]
        rows_str = ", ".join(str(r) for r in rows[:5])
        loc_parts.append(f"Row: {rows_str}")
    loc_str = " ".join(loc_parts) if loc_parts else ""
    sev_mark = "⚠️" if sev == "warning" else ""
    sep = " " if loc_str else ""
    return f"- [{sev}]{sep}{loc_str}{sep}{action} {sev_mark}".rstrip()
