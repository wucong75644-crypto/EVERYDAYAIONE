"""
数据摘要生成器 v6（对标 ydata-profiling / OpenMetadata）。

纯函数，零副作用。接收 DataFrame → 返回 (摘要文本, 结构化 stats_dict)。
用于 staging 写入后 / workspace 文件读取后，替代原始数据进入 LLM 上下文。

板块：元信息 / 字段 / 质量 / 统计（数值+时间+文本） / 预览 / 读取指引 / 警告

v6 变更：
- 返回值从 str 改为 (str, dict) 元组
- 新增 distinct_count / median / p25-p75 / IQR outlier
- 新增时间列摘要（min/max date + 跨度天数）
- 新增文本列摘要（top-5 高频值 + avg_length）
- 新增 max_profile_rows 采样保护
- 预览改 head(2) + sample(1)

设计文档: docs/document/TECH_Agent架构细节对齐_技术设计.md §阶段3
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd


def build_data_profile(
    df: "pd.DataFrame",
    filename: str,
    file_size_kb: float,
    elapsed: float = 0,
    sync_info: str = "",
    max_profile_rows: int = 50000,
) -> tuple[str, dict[str, Any]]:
    """生成标准数据摘要。~400-600 tokens，纯函数。

    Args:
        df: pandas DataFrame（数据已在内存）
        filename: staging 文件名（不含路径）
        file_size_kb: 文件大小（KB）
        elapsed: 查询耗时（秒）
        sync_info: 同步健康信息（可选）
        max_profile_rows: 超过此行数则采样后计算 stats

    Returns:
        (摘要文本, stats_dict)
        stats_dict 结构: {col_name: {sum, min, max, mean, median, ...}}
    """
    rows, cols = df.shape
    stats_dict: dict[str, Any] = {}

    if rows == 0:
        return f"[数据已暂存] {filename}\n无数据（0 条）", stats_dict

    # 采样保护：超过阈值时采样后计算统计（预览仍用原 df）
    sample_df = df.sample(n=max_profile_rows, random_state=42) if rows > max_profile_rows else df
    lines: list[str] = []

    # ── 1. 元信息 ──
    meta = f"[数据已暂存] {filename}\n共 {rows:,} 条 | {cols} 列 | {file_size_kb:.0f}KB"
    if elapsed > 0:
        meta += f" | 耗时 {elapsed:.1f}s"
    if rows > max_profile_rows:
        meta += f" | 统计基于 {max_profile_rows:,} 条采样"
    lines.append(meta)

    # ── 2. 字段（列名 + 类型 + distinct_count） ──
    dtype_map = {
        "object": "text", "string": "text",
        "int64": "int", "int32": "int", "Int64": "int",
        "float64": "float", "float32": "float",
        "datetime64[ns]": "datetime", "datetime64[ns, UTC]": "datetime",
        "bool": "bool",
    }
    field_parts = []
    for col_name in df.columns:
        dtype_str = str(df[col_name].dtype)
        mapped = dtype_map.get(dtype_str, dtype_str)
        n_unique = int(sample_df[col_name].nunique())
        field_parts.append(f"{col_name}({mapped},{n_unique}种)")
        stats_dict.setdefault(col_name, {})["distinct_count"] = n_unique
    lines.append(f"\n[字段] {' | '.join(field_parts)}")

    # ── 3. 质量（空值 + 重复） ──
    null_counts = sample_df.isnull().sum()
    null_cols = null_counts[null_counts > 0]
    dup_count = int(sample_df.duplicated().sum())

    quality_parts: list[str] = []
    if len(null_cols) > 0:
        null_items = []
        for col, cnt in null_cols.items():
            pct = cnt / len(sample_df) * 100
            null_items.append(f"{col}={cnt}条({pct:.1f}%)")
            stats_dict.setdefault(str(col), {})["null_count"] = int(cnt)
        quality_parts.append(f"空值: {', '.join(null_items)}")
    else:
        quality_parts.append("空值: 无")
    quality_parts.append(f"重复: {dup_count}条")
    lines.append(f"\n[质量] {' | '.join(quality_parts)}")

    # ── 4a. 数值列统计（sum/min/max/mean/median/p25/p75/IQR outlier） ──
    numeric_cols = sample_df.select_dtypes(include=["number"]).columns.tolist()
    if numeric_cols:
        stat_lines: list[str] = []
        for col_name in numeric_cols[:5]:
            s = sample_df[col_name].dropna()
            if len(s) == 0:
                continue
            q = s.quantile([0.25, 0.5, 0.75])
            p25, median, p75 = float(q.iloc[0]), float(q.iloc[1]), float(q.iloc[2])
            iqr = p75 - p25
            col_stats = {
                "sum": float(s.sum()), "min": float(s.min()),
                "max": float(s.max()), "mean": float(s.mean()),
                "median": median, "p25": p25, "p75": p75,
            }
            stats_dict.setdefault(col_name, {}).update(col_stats)

            line = (
                f"  {col_name}: 合计{s.sum():,.2f} "
                f"最小{s.min():,.2f} 最大{s.max():,.2f} "
                f"均值{s.mean():,.2f} 中位数{median:,.2f} "
                f"P25={p25:,.2f} P75={p75:,.2f}"
            )
            # IQR outlier
            if iqr > 0:
                outlier_mask = (s < p25 - 1.5 * iqr) | (s > p75 + 1.5 * iqr)
                n_outlier = int(outlier_mask.sum())
                if n_outlier > 0:
                    line += f" | 异常值{n_outlier}个({n_outlier/len(s)*100:.1f}%)"
                    stats_dict[col_name]["outlier_count"] = n_outlier
            stat_lines.append(line)
        if stat_lines:
            lines.append("\n[统计-数值]\n" + "\n".join(stat_lines))

    # ── 4b. 时间列统计（min/max date + 跨度天数） ──
    datetime_cols = sample_df.select_dtypes(include=["datetime", "datetimetz"]).columns.tolist()
    if datetime_cols:
        time_lines: list[str] = []
        for col_name in datetime_cols[:3]:
            s = sample_df[col_name].dropna()
            if len(s) == 0:
                continue
            min_dt, max_dt = s.min(), s.max()
            span_days = (max_dt - min_dt).days
            time_lines.append(
                f"  {col_name}: {min_dt.strftime('%Y-%m-%d')} ~ "
                f"{max_dt.strftime('%Y-%m-%d')}（跨{span_days}天）"
            )
            stats_dict.setdefault(col_name, {}).update({
                "min": str(min_dt), "max": str(max_dt),
                "span_days": span_days,
            })
        if time_lines:
            lines.append("\n[统计-时间]\n" + "\n".join(time_lines))

    # ── 4c. 文本/枚举列统计（top-5 高频值 + avg_length） ──
    text_cols = sample_df.select_dtypes(include=["object", "string"]).columns.tolist()
    if text_cols:
        text_lines: list[str] = []
        for col_name in text_cols[:5]:
            s = sample_df[col_name].dropna()
            if len(s) == 0:
                continue
            n_unique = int(s.nunique())
            avg_len = float(s.astype(str).str.len().mean())
            col_stat: dict[str, Any] = {"avg_length": round(avg_len, 1)}
            # 只对低基数列做 top-5：nunique/行数 < 0.5（重复率>50%）且 nunique≤100
            is_low_cardinality = n_unique <= 100 and (n_unique / len(s) < 0.5)
            if is_low_cardinality:
                top5 = s.value_counts().head(5)
                top5_list = [
                    f"{val}({cnt})" for val, cnt in top5.items()
                ]
                text_lines.append(
                    f"  {col_name}: {n_unique}种 | "
                    f"高频: {', '.join(top5_list)}"
                )
                col_stat["top5"] = [
                    {"value": str(val), "count": int(cnt)}
                    for val, cnt in top5.items()
                ]
            else:
                text_lines.append(
                    f"  {col_name}: {n_unique}种 | 平均长度{avg_len:.0f}字符"
                )
            stats_dict.setdefault(col_name, {}).update(col_stat)
        if text_lines:
            lines.append("\n[统计-文本]\n" + "\n".join(text_lines))

    # ── 5. 结构化预览（前5行连续展示） ──
    n_preview = min(5, rows)
    if n_preview > 0:
        preview_df = df.head(n_preview)
        # 构造 profile 格式（复用 _build_structured_preview）
        col_infos = []
        for col_name in df.columns:
            null_count = int(df[col_name].isna().sum())
            col_infos.append({"name": col_name, "null_count": null_count})
        import pandas as _pd
        preview_rows = []
        for _, row in preview_df.iterrows():
            preview_rows.append({
                col: (None if _pd.isna(row[col]) else row[col])
                for col in df.columns
            })
        preview_lines = _build_structured_preview(
            col_infos, rows, {"preview_rows": preview_rows},
        )
        if preview_lines:
            lines.append("\n" + "\n".join(preview_lines))

    # ── 6. 查询指引 ──
    lines.append(f'\n[查询] data_query(file="{filename}", sql="SELECT ... FROM data")')

    # ── 7. 警告 ──
    warnings: list[str] = []
    if sync_info:
        warnings.append(sync_info)
    if len(null_cols) > 0:
        high_null = [
            col for col, cnt in null_cols.items()
            if cnt / len(sample_df) > 0.1
        ]
        if high_null:
            warnings.append(f"⚠ 高空值率列: {', '.join(high_null)}")
    if warnings:
        lines.append("\n" + " | ".join(warnings))

    return "\n".join(lines), stats_dict


def build_profile_from_duckdb(
    profile: dict,
    filename: str,
    file_size_kb: float,
    elapsed: float = 0,
) -> tuple[str, dict[str, Any]]:
    """从 DuckDB profile_parquet() 结果构建摘要（不加载数据到 Python 内存）。

    Args:
        profile: DuckDBEngine.profile_parquet() 返回的 dict
        filename: staging 文件名
        file_size_kb: 文件大小（KB）
        elapsed: 导出耗时（秒）

    Returns:
        (摘要文本, stats_dict) — 和 build_data_profile 返回格式一致
    """
    columns = profile.get("columns", [])
    row_count = profile.get("row_count", 0)
    top_values = profile.get("top_values", {})
    stats_dict: dict[str, Any] = {}

    if row_count == 0:
        return f"[数据已暂存] {filename}\n无数据（0 条）", stats_dict

    lines: list[str] = []

    # ── 1. 元信息 ──
    meta = f"[数据已暂存] {filename}\n共 {row_count:,} 条 | {len(columns)} 列 | {file_size_kb:.0f}KB"
    if elapsed > 0:
        meta += f" | 耗时 {elapsed:.1f}s"
    lines.append(meta)

    # ── 2. 字段 ──
    dtype_map = {
        "VARCHAR": "text", "BIGINT": "int", "INTEGER": "int",
        "DOUBLE": "float", "FLOAT": "float", "DECIMAL": "float",
        "TIMESTAMP": "datetime", "TIMESTAMP WITH TIME ZONE": "datetime",
        "DATE": "date", "BOOLEAN": "bool",
    }
    field_parts = []
    for col in columns:
        mapped = dtype_map.get(col["type"], col["type"])
        n_unique = col.get("distinct_count", 0)
        field_parts.append(f"{col['name']}({mapped},{n_unique}种)")
        stats_dict[col["name"]] = {"distinct_count": n_unique}
        if col.get("null_count", 0) > 0:
            stats_dict[col["name"]]["null_count"] = col["null_count"]
    lines.append(f"\n[字段] {' | '.join(field_parts)}")

    # ── 3. 质量 ──
    null_items = []
    for col in columns:
        nc = col.get("null_count", 0)
        if nc > 0:
            pct = nc / row_count * 100
            null_items.append(f"{col['name']}={nc}条({pct:.1f}%)")
    quality = f"空值: {', '.join(null_items)}" if null_items else "空值: 无"
    dup_count = profile.get("duplicate_count", 0)
    lines.append(f"\n[质量] {quality} | 重复: {dup_count}条")

    # ── 4a. 数值列统计 ──
    num_lines: list[str] = []
    for col in columns:
        if col.get("avg") is None:
            continue
        name = col["name"]
        s = stats_dict.setdefault(name, {})
        s.update({
            k: col[k] for k in ("sum", "min", "max", "avg", "median", "p25", "p75")
            if col.get(k) is not None
        })
        line = f"  {name}:"
        if col.get("sum") is not None:
            line += f" 合计{col['sum']:,.2f}"
        line += f" 最小{col['min']} 最大{col['max']} 均值{col['avg']:.2f}"
        if col.get("median") is not None:
            line += f" 中位数{col['median']:.2f}"
        if col.get("p25") is not None and col.get("p75") is not None:
            line += f" P25={col['p25']:.2f} P75={col['p75']:.2f}"
        num_lines.append(line)
        if len(num_lines) >= 5:
            break
    if num_lines:
        lines.append("\n[统计-数值]\n" + "\n".join(num_lines))

    # ── 4b. 时间列统计 ──
    time_lines: list[str] = []
    for col in columns:
        if col["type"] not in ("TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "DATE"):
            continue
        min_val, max_val = col.get("min"), col.get("max")
        if min_val and max_val:
            span = col.get("span_days")
            span_str = f"（跨{span}天）" if span is not None else ""
            time_lines.append(f"  {col['name']}: {min_val} ~ {max_val}{span_str}")
            col_stat = {"min": str(min_val), "max": str(max_val)}
            if span is not None:
                col_stat["span_days"] = span
            stats_dict.setdefault(col["name"], {}).update(col_stat)
        if len(time_lines) >= 3:
            break
    if time_lines:
        lines.append("\n[统计-时间]\n" + "\n".join(time_lines))

    # ── 4c. 文本列统计（top-5 来自 DuckDB 聚合） ──
    text_lines: list[str] = []
    for col in columns:
        if col["type"] != "VARCHAR":
            continue
        name = col["name"]
        n_unique = col.get("distinct_count", 0)
        avg_len = col.get("avg_length")
        col_stat = stats_dict.setdefault(name, {})
        if avg_len is not None:
            col_stat["avg_length"] = avg_len
        if name in top_values:
            top5_str = ", ".join(
                f"{t['value']}({t['count']})" for t in top_values[name]
            )
            text_lines.append(f"  {name}: {n_unique}种 | 高频: {top5_str}")
            col_stat["top5"] = top_values[name]
        elif n_unique > 0:
            len_str = f" | 平均长度{avg_len:.0f}字符" if avg_len else ""
            text_lines.append(f"  {name}: {n_unique}种{len_str}")
        if len(text_lines) >= 5:
            break
    if text_lines:
        lines.append("\n[统计-文本]\n" + "\n".join(text_lines))

    # ── 5. 结构化预览（空值率分组 + 列名模式 + 连续行） ──
    preview_lines = _build_structured_preview(columns, row_count, profile)
    if preview_lines:
        lines.append("\n" + "\n".join(preview_lines))

    # ── 7. 查询指引 ──
    lines.append(f'\n[查询] data_query(file="{filename}", sql="SELECT ... FROM data")')

    return "\n".join(lines), stats_dict


# ============================================================
# 结构化预览（空值率分组 + 列名模式 + 连续行展示）
# ============================================================


def _build_structured_preview(
    columns: list[dict], row_count: int, profile: dict,
) -> list[str]:
    """构建结构化预览：按空值率分组列 + 检测列名模式 + 展示连续行。

    不分类、不贴标签，只描述客观事实。
    Agent 看到分组和连续行的实际样貌后自行理解数据结构。
    """
    if row_count == 0:
        return [f"[预览] 共0行"]

    lines: list[str] = [f"[预览] 共{row_count:,}行"]

    # ── 行维度：按空值率分组列 ──
    # 只在列之间空值率有明显差异时输出（否则是普通扁平表，不需要分组）
    data_cols = [c for c in columns if not c["name"].startswith("_is_")]
    if data_cols and row_count > 0:
        fill_rates: dict[str, int] = {}  # col_name → fill_rate_pct
        for col in data_cols:
            null_count = col.get("null_count", 0)
            fill_pct = round((1 - null_count / row_count) * 100)
            fill_rates[col["name"]] = fill_pct

        # 按 10% 粒度分桶
        buckets: dict[int, list[str]] = {}
        for name, pct in fill_rates.items():
            bucket = (pct // 10) * 10  # 0, 10, 20, ..., 100
            buckets.setdefault(bucket, []).append(name)

        # 只在有多个不同桶时才输出分组（说明列之间有结构差异）
        if len(buckets) >= 2:
            for bucket in sorted(buckets.keys()):
                col_names = buckets[bucket]
                pct_label = f"{bucket}~{min(bucket + 9, 100)}%行有值"
                if bucket >= 100:
                    pct_label = "每行都有值"
                elif bucket <= 0:
                    pct_label = "全部为空"
                display = ", ".join(col_names[:8])
                if len(col_names) > 8:
                    display += f" 等{len(col_names)}个"
                lines.append(f"\n  以下字段{pct_label}:")
                lines.append(f"    {display}")

    # ── 列维度：检测列名重复模式 ──
    col_names = [c["name"] for c in data_cols]
    pattern_lines = _detect_column_name_patterns(col_names)
    if pattern_lines:
        lines.append("")
        lines.extend(pattern_lines)

    # ── 连续行展示 ──
    preview_rows = profile.get("preview_rows", [])
    if preview_rows:
        lines.append(f"\n  前{len(preview_rows)}行:")
        for i, row in enumerate(preview_rows, 1):
            parts = []
            for k, v in row.items():
                if k.startswith("_is_"):
                    continue
                sv = str(v) if v is not None and str(v) not in ("nan", "NaT", "None") else "空"
                if len(sv) > 40:
                    sv = sv[:37] + "..."
                parts.append(f"{k}={sv}")
            lines.append(f"    行{i}: {' | '.join(parts)}")

    return lines


def _detect_column_name_patterns(col_names: list[str]) -> list[str]:
    """检测列名中的重复模式（前缀/后缀），用于发现交叉表等列维度结构。

    按 _ 拆分列名，统计每个部分出现次数，≥3次的报出来。
    """
    if len(col_names) < 3:
        return []

    # 按 _ 拆分，统计每个 part 出现在多少个列名中
    from collections import Counter
    part_counter: Counter[str] = Counter()
    for name in col_names:
        parts = name.split("_")
        # 去重（同一列名拆出的重复部分只计一次）
        for part in set(parts):
            if part and len(part) >= 2:  # 忽略空串和单字符
                part_counter[part] += 1

    # 筛选出现 ≥3 次的部分
    repeated = [(part, cnt) for part, cnt in part_counter.items() if cnt >= 3]
    if not repeated:
        return []

    repeated.sort(key=lambda x: x[1], reverse=True)
    lines = ["  列名含重复模式:"]
    for part, cnt in repeated[:5]:
        matching = [n for n in col_names if part in n.split("_")]
        lines.append(f"    \"{part}\" 出现{cnt}次: {', '.join(matching[:5])}")

    return lines
