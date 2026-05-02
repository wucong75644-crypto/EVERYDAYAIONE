"""
data_query 结果格式化

Markdown 表格、统计摘要、大结果分档——纯函数，零副作用。
从 data_query_executor.py 拆分，降低主文件行数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def format_markdown_table(df: Any, max_cell_len: int = 50) -> str:
    """DataFrame → Markdown 表格字符串。"""
    cols = list(df.columns)
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"

    lines = [header, sep]
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            sv = "" if (v is None or str(v) == "nan" or str(v) == "NaT") else str(v)
            if len(sv) > max_cell_len:
                sv = sv[:max_cell_len - 3] + "..."
            cells.append(sv)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def format_numeric_summary(df: Any, max_cols: int = 5) -> str:
    """数值列统计摘要。无数值列返回空字符串。"""
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    if not numeric_cols:
        return ""
    lines = ["**统计摘要**"]
    for col in numeric_cols[:max_cols]:
        s = df[col].dropna()
        if len(s) == 0:
            continue
        lines.append(
            f"- {col}: 合计 {s.sum():,.2f} | "
            f"均值 {s.mean():,.2f} | "
            f"最小 {s.min():,.2f} | 最大 {s.max():,.2f}"
        )
    return "\n".join(lines) if len(lines) > 1 else ""


def format_full_result(df: Any, rows: int, elapsed: float) -> str:
    """≤100 行完整表格 + 元信息。"""
    table = format_markdown_table(df)
    return f"{table}\n\n共 {rows:,} 行 | 耗时 {elapsed:.2f}s"


def format_large_result_from_parquet(
    con: Any,
    parquet_escaped: str,
    filename: str,
    row_count: int,
    elapsed: float,
) -> str:
    """>100 行：从 Parquet 取预览和统计，数据不经过 Python 内存。

    Args:
        con: DuckDB 连接（已创建 view、可读 parquet）
        parquet_escaped: Parquet 文件路径（已转义）
        filename: 暂存文件名（展示用）
        row_count: 行数（已从 metadata 获取）
        elapsed: 耗时
    """
    # 前 5 行预览（只读 5 行到 Python）
    preview_df = con.execute(
        f"SELECT * FROM read_parquet('{parquet_escaped}') LIMIT 5"
    ).fetchdf()
    col_count = len(preview_df.columns)
    preview = format_markdown_table(preview_df)

    # 统计摘要（DuckDB 列式扫描，不加载全量到 Python）
    summary = _summarize_parquet(con, parquet_escaped)

    # 文件大小
    size_kb = Path(parquet_escaped.replace("''", "'")).stat().st_size / 1024

    parts = [
        f"共 {row_count:,} 行 | {col_count} 列 | 耗时 {elapsed:.2f}s",
        f"结果已暂存: {filename}（{size_kb:.0f}KB）",
    ]
    if summary:
        parts.append(summary)
    parts.append(f"\n**前 5 行预览：**\n{preview}")

    return "\n\n".join(parts)


def _summarize_parquet(con: Any, parquet_escaped: str) -> str:
    """用 DuckDB SUMMARIZE 对 Parquet 文件算数值列统计。"""
    try:
        rows = con.execute(
            f"SUMMARIZE SELECT * FROM read_parquet('{parquet_escaped}')"
        ).fetchall()
        desc = con.description
    except Exception:
        return ""

    if not desc:
        return ""

    col_names = [d[0] for d in desc]
    name_idx = col_names.index("column_name") if "column_name" in col_names else 0
    type_idx = col_names.index("column_type") if "column_type" in col_names else 1
    min_idx = col_names.index("min") if "min" in col_names else 2
    max_idx = col_names.index("max") if "max" in col_names else 3
    avg_idx = col_names.index("avg") if "avg" in col_names else 5

    lines = ["**统计摘要**"]
    numeric_types = {"BIGINT", "INTEGER", "DOUBLE", "FLOAT", "DECIMAL", "SMALLINT", "TINYINT", "HUGEINT"}
    count = 0
    for row in rows:
        if count >= 5:
            break
        if str(row[type_idx]) not in numeric_types:
            continue
        name = str(row[name_idx])
        try:
            avg_val = float(row[avg_idx]) if row[avg_idx] is not None else None
        except (ValueError, TypeError):
            avg_val = None
        line = f"- {name}: 最小 {row[min_idx]} | 最大 {row[max_idx]}"
        if avg_val is not None:
            line += f" | 均值 {avg_val:,.2f}"
        lines.append(line)
        count += 1

    return "\n".join(lines) if len(lines) > 1 else ""


def format_sql_error(error_msg: str, columns: list[str]) -> "AgentResult":
    """SQL 错误 → 结构化 AgentResult，高亮 DuckDB 建议。"""
    import re
    from services.agent.agent_result import AgentResult

    # 提取 DuckDB 的 "Did you mean" 建议
    match = re.search(r'Did you mean "([^"]+)"', error_msg)
    suggestion = match.group(1) if match else None

    if suggestion:
        summary = (
            f"SQL 错误：列名不存在\n"
            f"→ 修正：使用 \"{suggestion}\" 替代\n"
            f"→ 示例：SELECT \"{suggestion}\" FROM data"
        )
    else:
        cols_str = ", ".join(f'"{c}"' for c in columns[:30]) if columns else "(无法获取列名)"
        if len(columns) > 30:
            cols_str += f" ... 共 {len(columns)} 列"
        summary = (
            f"SQL 错误：{error_msg}\n\n"
            f"可用列名：{cols_str}\n"
            f"提示：中文列名需用双引号包裹"
        )

    return AgentResult(
        summary=summary,
        status="error",
        error_message=error_msg,
        metadata={"suggestion": suggestion, "retryable": True},
    )
