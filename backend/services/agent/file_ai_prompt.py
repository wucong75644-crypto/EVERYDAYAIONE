"""file_analyze 重构 - AI prompt 模板。

从 file_ai_judge 拆出，保持 judge 文件聚焦于调用链与错误处理。

variant:
  - "default":     完整 prompt（含全部 evidence）
  - "simplified":  retry 时减小 prompt（仅头尾样本 + 表头候选）

设计文档：docs/document/TECH_file_analyze_重构.md §5.2 §5.3
"""
from __future__ import annotations

from services.agent.file_evidence import EvidencePool


JSON_SCHEMA_TEMPLATE = """
{
  "header_row": <int, Excel 1-indexed>,
  "data_start_row": <int, Excel 1-indexed>,
  "header_type": "single" | "multi_level",
  "header_note": "<可选，特殊说明>",
  "column_semantics": [
    {
      "letter": "A",
      "business_name": "<推断的业务列名>",
      "semantic_type": "id" | "name" | "datetime" | "amount" | "quantity" | "address" | "note" | "category" | "other",
      "is_order_level": <bool>,
      "is_id_column": <bool>,
      "notes": "<可选>"
    }
  ],
  "summary_rows": [<Excel 1-indexed>],
  "unit_rows": [],
  "note_rows": [],
  "regions": [
    {
      "region_id": 1,
      "range_str": "A1:H100",
      "role": "primary" | "secondary" | "metadata" | "skip",
      "relation_to_primary": "<可选>",
      "skip_reason": "<可选>"
    }
  ],
  "sheets": [
    {
      "name": "<sheet 名>",
      "role": "data" | "meta" | "aggregated" | "skip",
      "merge_group": "<同组合并>",
      "skip_reason": "<可选>"
    }
  ],
  "merged_cell_actions": [
    {
      "range_str": "A2:H2",
      "action": "treat_as_header" | "fill_down" | "preserve_as_group" | "skip",
      "reason": "<可选>"
    }
  ],
  "mixed_type_handling": [
    {
      "col_letter": "F",
      "action": "force_str" | "extract_unit_number" | "extract_currency_amount" | "to_datetime",
      "unit": "<当 extract_unit_number 时>",
      "reason": "<可选>"
    }
  ],
  "preserve_empty_rows": [{"row": <int>, "reason": "<可选>"}],
  "data_quality_notes": [
    {
      "severity": "info" | "warning" | "error",
      "note": "<给主 Agent 看的提示>",
      "affected_rows": [<int>],
      "affected_cols": ["<列字母>"]
    }
  ],
  "overall_summary": "<100-300 字总结>",
  "table_role": "fact" | "dimension" | "log" | "wide" | "snapshot" | "unknown",
  "table_role_note": "<一句话理由，例如：维度表，主要用于 JOIN 补充店铺所属信息>"
}

# table_role 判断指南
- fact:      事实表 / 订单明细表 / 交易流水。有 ID + 多个数值聚合字段（金额/数量），
             同一 ID 多行（一对多粒度）。例：销售订单明细、退款流水。
- dimension: 维度表 / 映射表 / 字典表。行数不多，列以字符串为主，无聚合数值字段，
             有高基数 string 列（候选 join key）。例：店铺映射、地区编码表、商品类目表。
- log:       日志 / 事件表。按时间排序，无主键聚合概念，每行一个事件。
             例：系统日志、操作记录。
- wide:      宽表 / 指标表。高列数（≥50 列），每行一个 entity 多个指标。
             例：KPI 周报、月度运营报表。
- snapshot:  快照表。某时点全量数据，无时间维度。例：当日库存、月末余额。
- unknown:   无法判断时填此值（不强求选）。""".strip()


def build_prompt(evidence: EvidencePool, variant: str = "default") -> str:
    """构造 AI 裁决 prompt。"""
    parts: list[str] = []

    parts.append("# 任务\n")
    parts.append(
        "你将看到一份 Excel/CSV 文件的代码扫描结果。请基于这些证据做出一次性裁决，"
        "包括表头位置、列业务语义、是否有汇总行、清洗策略等。"
        "你的输出会被代码直接执行，所以必须精确。\n\n"
    )

    parts.append("# 文件信息\n")
    parts.append(f"- 文件名: {evidence.file_name}\n")
    parts.append(f"- 总行数: {evidence.total_rows:,}\n")
    parts.append(f"- 总列数: {evidence.total_cols}\n")
    parts.append(f"- 当前 Sheet: {evidence.target_sheet}\n")
    parts.append(f"- 处理路径: {evidence.path_type}\n\n")

    parts.append("# 表头候选（前 5 行原始）\n")
    for i, row in enumerate(evidence.header_candidates, start=1):
        parts.append(f"Row {i}: {_truncate_row(row)}\n")
    parts.append(f"\n代码兜底检测表头行: Row {evidence.detected_header_row_code + 1}\n\n")

    # 列证据
    parts.append("# 列证据\n")
    for col_ev in evidence.columns:
        # V3：仅保留纯统计驱动的 flag（long_id_candidate）
        # 业务格式（货币/单位/UUID/ASIN）改由 AI 看 sample 自识别
        flags = []
        if col_ev.is_long_id_candidate:
            flags.append("⚠️ 长ID候选(可能是订单号/编码,清洗时应保 string)")
        flag_str = (" " + " ".join(flags)) if flags else ""
        parts.append(
            f"列 {col_ev.col_letter}: 原始表头='{col_ev.raw_header}', "
            f"类型分布={col_ev.classified_dist}, null率={col_ev.null_ratio:.2%}{flag_str}\n"
        )
        # simplified 不输出 sample_values
        if variant != "simplified":
            sample_preview = col_ev.sample_values[:8] if col_ev.sample_values else []
            parts.append(f"  样本: {_truncate_list(sample_preview)}\n")
    parts.append("\n")

    # 可疑行（simplified 限 10 条）
    if evidence.suspicious_rows:
        limit = 10 if variant == "simplified" else 50
        parts.append(f"# 可疑行（共 {len(evidence.suspicious_rows)} 条，展示前 {limit}）\n")
        # V3：可疑行只给位置 + null 率 + 原始值，由 AI 自判是 summary/note/unit/异常
        for sr in evidence.suspicious_rows[:limit]:
            parts.append(
                f"Row {sr.row}: null率={sr.null_ratio:.0%}\n"
                f"  原始值: {_truncate_list(sr.raw_values[:10])}\n"
            )
        parts.append("\n")

    # 关键样本（simplified 仅头尾 3 行）
    if evidence.key_samples:
        limit = 6 if variant == "simplified" else 30
        parts.append("# 关键样本\n")
        for sample in evidence.key_samples[:limit]:
            parts.append(f"Row {sample['row']}: {_truncate_list(sample['cells'])}\n")
        parts.append("\n")

    # 路径 C 多区域证据
    if evidence.path_type == "C" and evidence.regions:
        parts.append("# 候选数据区域（路径 C，待你裁决每个区域的 role）\n")
        for r in evidence.regions:
            parts.append(
                f"Region {r.region_id} ({r.range_str}): {r.row_count} 行, 表头={_truncate_list(r.header_cells)}\n"
            )
            if r.head_sample:
                parts.append(f"  Head: {_truncate_row(r.head_sample[0])}\n")
            if r.tail_sample:
                parts.append(f"  Tail: {_truncate_row(r.tail_sample[-1])}\n")
        parts.append("\n")

    # 路径 D 多 sheet 证据
    if evidence.path_type == "D" and evidence.sheets:
        parts.append("# 所有 Sheet 元信息（路径 D，待你裁决每个 sheet 的 role / merge_group）\n")
        for s in evidence.sheets:
            rows_str = "未采样" if s.rows == -1 else f"{s.rows} 行"
            parts.append(
                f"Sheet '{s.name}': {rows_str} × {s.cols} 列, 列名={_truncate_list(s.column_names)}\n"
            )
            if variant != "simplified" and s.head_sample:
                parts.append(f"  Head: {_truncate_row(s.head_sample[0])}\n")
        parts.append("\n")

    # 公式
    if evidence.formulas:
        parts.append(
            f"# 公式（共 {evidence.formula_total_count} 个，展示前 10）\n"
        )
        for f in evidence.formulas[:10]:
            parts.append(f"- {f.cell}: {f.expression} = {f.value}\n")
        parts.append("\n")

    # 结构元信息
    if evidence.merged_ranges or evidence.hidden_cols or evidence.has_auto_filter:
        parts.append("# 结构元信息\n")
        if evidence.merged_ranges:
            parts.append(
                f"- 合并单元格: {len(evidence.merged_ranges)} 个区域 "
                f"(前 5: {evidence.merged_ranges[:5]})\n"
            )
        if evidence.hidden_cols:
            parts.append(f"- 隐藏列: {evidence.hidden_cols}\n")
        if evidence.has_auto_filter:
            parts.append("- 含 autofilter\n")
        parts.append("\n")

    # 输出 schema
    parts.append("# 你的输出格式（严格 JSON，不要 markdown）\n")
    parts.append(JSON_SCHEMA_TEMPLATE)

    return "".join(parts)


# ── 辅助函数：截断长值避免 prompt 爆炸 ──

def _truncate_str(val, maxlen: int = 60) -> str:
    s = str(val) if val is not None else ""
    if len(s) <= maxlen:
        return s
    return s[:maxlen - 3] + "..."


def _truncate_row(row, max_cells: int = 25) -> str:
    """单元格行 → 字符串，长值截断。"""
    if not row:
        return "[]"
    cells = [_truncate_str(v) for v in list(row)[:max_cells]]
    suffix = " ..." if len(row) > max_cells else ""
    return "[" + ", ".join(repr(c) for c in cells) + "]" + suffix


def _truncate_list(lst, max_items: int = 15) -> str:
    if not lst:
        return "[]"
    items = list(lst)[:max_items]
    truncated = [_truncate_str(v) for v in items]
    suffix = " ..." if len(lst) > max_items else ""
    return "[" + ", ".join(repr(t) for t in truncated) + "]" + suffix
