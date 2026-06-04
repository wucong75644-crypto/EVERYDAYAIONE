"""FileMeta IO + 格式化视图。

- write_file_meta / read_file_meta: .meta.json 落盘读写
- format_file_view: U 形 attention 优化的 AI context 注入文本
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from services.agent.file_meta.dataclass import FileMeta


# 行业标准映射（dbt/Spark/DuckDB CLI 风格）：view 直接显示 DuckDB native type。
# AI 看到的是真实 SQL 类型 → 写 SQL 时不会再用 MySQL/PG 方言（CAST AS DATETIME 等）。
# 注：schema["type"] 字段本身保持原值（integer/decimal/...），只在渲染层映射。
_DUCKDB_TYPE_MAP: dict[str, str] = {
    "integer":  "BIGINT",
    "decimal":  "DOUBLE",
    "datetime": "TIMESTAMP",
    "boolean":  "BOOLEAN",
    "string":   "VARCHAR",
}


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

    # ── 多 Sheet 章节（仅当文件实际多 sheet 才输出）──
    sheets_info = (meta.evidence_summary or {}).get("sheets") or {}
    merged_sheets = sheets_info.get("merged") or []
    skipped_sheets = sheets_info.get("skipped") or []
    if len(merged_sheets) > 1 or skipped_sheets:
        lines.append("")
        lines.append("## 📋 多 Sheet")
        if len(merged_sheets) > 1:
            lines.append(
                f"- ✅ 已合并 {len(merged_sheets)} 个数据 sheet 到同一 Parquet："
                f"{', '.join(merged_sheets)}"
            )
            lines.append(
                "  来源 sheet 用 `_sheet` 列区分（"
                f"`WHERE _sheet = '{merged_sheets[0]}'` 可过滤单个 sheet）"
            )
        for sk in skipped_sheets:
            sk_name = sk.get("name", "?")
            sk_role = sk.get("role", "?")
            role_hint = {
                "meta": "元信息表（无数据）",
                "aggregated": "汇总表（含小计/合计，跳过避免重复）",
                "skip": "AI 判定无关",
            }.get(sk_role, sk_role)
            lines.append(
                f"- ⏭️ **{sk_name}** 被跳过 ({role_hint})"
            )
        if skipped_sheets:
            lines.append(
                "  如需读取跳过的 sheet："
                "`pd.read_excel(get_file('文件名'), sheet_name='Sheet 名')`"
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
            raw_type = info.get("type", "unknown")
            # 行业标准：渲染层直接显示 DuckDB native type（VARCHAR/BIGINT/TIMESTAMP）
            # 让 AI 看到的就是写 SQL 时要用的类型名，避免 MySQL/PG 方言混入
            dtype = _DUCKDB_TYPE_MAP.get(raw_type, raw_type)
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

    同行 missing_value 合并为客观陈述，不再附加任何归因猜测。
    汇总行的判断由 AI 一次裁决（AIDecision.summary_rows）负责，
    此函数只输出代码可验证的事实。
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
            cols_display = ", ".join(cols[:5]) + ("..." if len(cols) > 5 else "")
            out.append(f"- Row {row} 多列缺失（{cols_display}）")
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
