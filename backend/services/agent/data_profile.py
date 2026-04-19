"""
数据摘要生成器（Anthropic content_and_artifact 模式）。

纯函数，零副作用。接收 DataFrame → 返回标准 7 板块摘要文本。
用于 staging 写入后 / workspace 文件读取后，替代原始数据进入 LLM 上下文。

板块：元信息 / 字段 / 质量 / 统计 / 预览 / 读取指引 / 警告

设计文档: docs/document/TECH_数据摘要标准化.md §2.2
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def build_data_profile(
    df: "pd.DataFrame",
    filename: str,
    file_size_kb: float,
    elapsed: float = 0,
    sync_info: str = "",
) -> str:
    """生成标准数据摘要。~300-500 tokens，纯函数。

    Args:
        df: pandas DataFrame（数据已在内存，不重新读文件）
        filename: staging 文件名（不含路径，如 trade_1713520081.parquet）
        file_size_kb: 文件大小（KB）
        elapsed: 查询耗时（秒）
        sync_info: 同步健康信息（可选）
    """
    rows, cols = df.shape
    lines: list[str] = []

    # ── 1. 元信息 ──
    meta = f"[数据已暂存] {filename}\n共 {rows:,} 条 | {cols} 列 | {file_size_kb:.0f}KB"
    if elapsed > 0:
        meta += f" | 耗时 {elapsed:.1f}s"
    lines.append(meta)

    # ── 2. 字段（列名 + 类型） ──
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
        field_parts.append(f"{col_name}({mapped})")
    lines.append(f"\n[字段] {' | '.join(field_parts)}")

    # ── 3. 质量（空值 + 重复） ──
    null_counts = df.isnull().sum()
    null_cols = null_counts[null_counts > 0]
    dup_count = int(df.duplicated().sum())

    quality_parts: list[str] = []
    if len(null_cols) > 0:
        null_items = [
            f"{col}={cnt}条({cnt/rows*100:.1f}%)"
            for col, cnt in null_cols.items()
        ]
        quality_parts.append(f"空值: {', '.join(null_items)}")
    else:
        quality_parts.append("空值: 无")
    quality_parts.append(f"重复: {dup_count}条")
    lines.append(f"\n[质量] {' | '.join(quality_parts)}")

    # ── 4. 统计（数值列 sum/min/max/avg） ──
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    if numeric_cols:
        stat_lines: list[str] = []
        for col_name in numeric_cols[:5]:  # 最多 5 列，避免太长
            s = df[col_name].dropna()
            if len(s) == 0:
                continue
            stat_lines.append(
                f"  {col_name}: 合计{s.sum():,.2f} "
                f"最小{s.min():,.2f} 最大{s.max():,.2f} "
                f"均值{s.mean():,.2f}"
            )
        if stat_lines:
            lines.append("\n[统计]\n" + "\n".join(stat_lines))

    # ── 5. 预览（前 3 行） ──
    preview_rows = df.head(3)
    preview_lines: list[str] = []
    for i, (_, row) in enumerate(preview_rows.iterrows(), 1):
        parts = []
        for col_name in df.columns:
            v = row[col_name]
            if v is not None and str(v) != "nan":
                sv = str(v)
                if len(sv) > 40:
                    sv = sv[:37] + "..."
                parts.append(f"{col_name}={sv}")
        preview_lines.append(f"  {i}. {' | '.join(parts)}")
    if preview_lines:
        lines.append("\n[预览] 前3条:\n" + "\n".join(preview_lines))

    # ── 6. 读取指引 ──
    lines.append(f"\n[读取] df = pd.read_parquet(STAGING_DIR + '/{filename}')")

    # ── 7. 警告 ──
    warnings: list[str] = []
    if sync_info:
        warnings.append(sync_info)
    if len(null_cols) > 0:
        high_null = [
            col for col, cnt in null_cols.items() if cnt / rows > 0.1
        ]
        if high_null:
            warnings.append(f"⚠ 高空值率列: {', '.join(high_null)}")
    if warnings:
        lines.append("\n" + " | ".join(warnings))

    return "\n".join(lines)
