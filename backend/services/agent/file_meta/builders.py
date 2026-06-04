"""FileMeta 构建器：schema / sample / 粒度检测 / issues / 类型推断。"""
from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from services.agent.excel_cleaner import CleaningReport
from services.agent.file_meta.dataclass import (
    FileMeta,
    _CATEGORY_THRESHOLD,
    _MAX_ISSUES,
    _SAMPLE_BOUNDARY_MAX,
    _SAMPLE_HEAD,
    _SAMPLE_MIDDLE,
    _SAMPLE_TAIL,
    _col_index_to_letter,
)


def generate_file_meta(
    df: pd.DataFrame,
    cleaning_report: CleaningReport,
    source_file: str,
    sheet_count: int = 1,
    formulas: list[dict[str, Any]] | None = None,
    formula_skip_reason: str = "",
    merged_ranges: list[tuple[int, int, int, int]] | None = None,
    ai_decision: Any = None,
) -> FileMeta:
    """从 DataFrame + CleaningReport 生成完整 FileMeta。

    ai_decision 接受 AIDecision 或 _AIDecisionAdapter（兼容 V1 PrescanResult 接口）。
    """
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
    meta.sample = _build_sample(df, cleaning_report.data_start_row, ai_decision)

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
    df: pd.DataFrame, data_start_row: int, ai_decision: Any = None,
) -> dict[str, list[dict]]:
    """提取代表性样本数据，带 Excel 原始行号。

    覆盖三段（head + middle + tail），共约 10 行：
    - head 4 行：开头
    - middle 2 行：中段（避免"lost in the middle"）
    - tail 4 行：末尾（含可能的汇总行）

    边界补充（boundary，可选 0-2 行）：
    - 复用 ai_decision.anomalies[*].sample_rows ——
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

    # boundary 段：复用 AI 决策的 anomalies 行号（零额外扫描）
    boundary_df = df.iloc[0:0]
    if ai_decision is not None:
        anomalies = getattr(ai_decision, "anomalies", None) or []
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
        """生成行签名。

        Bug-6 修复：
          - 数值列：保留前 4 位有效数字（用 %.4g 格式化），区分接近的数值
          - 字符串列：前 16 字符 hash + 长度，避免长字符串（如订单号）前 8 位
            相同就被误判为同一类
        """
        sig: list = []
        for col, t in col_types.items():
            val = row.get(col)
            if val is None or val == "":
                sig.append("∅")
            elif t == "number":
                try:
                    fv = float(val)
                    sig.append(f"{fv:.4g}")
                except (TypeError, ValueError):
                    s = str(val)
                    sig.append((hash(s[:16]), len(s)))
            else:
                s = str(val)
                sig.append((hash(s[:16]), len(s)))
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
