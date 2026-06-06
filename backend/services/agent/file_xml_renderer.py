"""file_analyze 重构 - XML 渲染器（V2 输出格式）。

把 FileMeta v2（含 ai_decision / cleaning_strategy / related_files）渲染为
主 Agent 友好的 XML，包含：
  • data_access      干净 Parquet 地址 + quick_start CDATA SQL
  • file_meta        文件基本信息
  • ai_decision      AI 一次裁决总结
  • usage_hints      聚合 SQL 范式（CDATA 保留代码）
  • column_schema    每列详细信息
  • grain            数据粒度（订单级/明细级）
  • sample_data      head/mid/tail 自适应行数
  • related_files    跨文件关联（从 session_files.json）
  • cleaning_result  AI 决策的清洗动作摘要
  • formulas         公式列表（仅有公式时）

设计文档：docs/document/TECH_file_analyze_重构.md §8
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from services.agent.file_meta import FileMeta


# ── 动态 sample 行数自适应 ──

def sample_segment_sizes(total_rows: int) -> tuple[int, int, int]:
    """按文件规模返回 (head, mid, tail) 行数。"""
    if total_rows <= 10_000:
        return 3, 0, 3
    elif total_rows <= 100_000:
        return 4, 2, 4
    elif total_rows <= 1_000_000:
        return 5, 3, 5
    else:
        return 6, 6, 6


# ── 主入口 ──

def render_xml(
    meta: FileMeta,
    parquet_path: str,
    original_path: str = "",
    related_files: list[dict] | None = None,
) -> str:
    """把 FileMeta v2 渲染为完整 XML。"""
    parts: list[str] = ["<file_analysis>\n"]

    # 1. 数据访问地址（主 Agent 第一眼看的关键节点）
    parts.append(_render_data_access(parquet_path, original_path))

    # 2. 文件基本信息
    parts.append(_render_file_meta(meta, original_path))

    # 3. AI 裁决总结
    parts.append(_render_ai_decision(meta))

    # 4. 查询规则（usage_hints + code_example）
    parts.append(_render_usage_hints(meta, parquet_path))

    # 5. 列 schema
    parts.append(_render_column_schema(meta))

    # V3：删 _render_grain 调用（grain 章节已废弃）

    # 7. 样本数据（动态行数）
    parts.append(_render_sample_data(meta))

    # 8. 跨文件关联（仅有时）
    if related_files:
        parts.append(_render_related_files(related_files))

    # 9. 清洗结果
    parts.append(_render_cleaning_result(meta))

    # 10. 公式（仅有时）
    if meta.formulas:
        parts.append(_render_formulas(meta))

    parts.append("</file_analysis>\n")
    return "".join(parts)


# ── 各节点渲染 ──

def _render_data_access(parquet_path: str, original_path: str) -> str:
    quick_start = (
        f"duckdb.sql(\"SELECT * FROM read_parquet('{parquet_path}') LIMIT 5\").df()"
    )
    return (
        '\n  <data_access priority="critical" ready="true">\n'
        f"    <parquet_path>{escape(parquet_path)}</parquet_path>\n"
        f"    <original_path>{escape(original_path or parquet_path)}</original_path>\n"
        f"    <quick_start><![CDATA[\n{quick_start}\n]]></quick_start>\n"
        "  </data_access>\n"
    )


def _render_file_meta(meta: FileMeta, original_path: str) -> str:
    s = meta.summary or {}
    # 路径协议:展示给 LLM 的 <path> 用 caller 传的相对路径(沙盒视角)
    # I/O 用 meta.source_file(host 绝对路径,主进程文件系统操作)
    display_path = original_path or (Path(meta.source_file).name if meta.source_file else "")
    src_for_stat = meta.source_file or original_path
    size_bytes = 0
    if src_for_stat and Path(src_for_stat).exists():
        try:
            size_bytes = Path(src_for_stat).stat().st_size
        except OSError:
            pass
    size_mb = round(size_bytes / 1024 / 1024, 2) if size_bytes else 0
    path_type = (meta.ai_decision or {}).get("path_type", "") if isinstance(meta.ai_decision, dict) else ""

    return (
        '\n  <file_meta priority="high">\n'
        f"    <path>{escape(display_path)}</path>\n"
        f"    <filename>{escape(Path(display_path).name if display_path else '')}</filename>\n"
        f"    <size_mb>{size_mb}</size_mb>\n"
        f"    <rows>{s.get('row_count', 0)}</rows>\n"
        f"    <cols>{s.get('col_count', 0)}</cols>\n"
        f"    <sheet_count>{s.get('sheet_count', 1)}</sheet_count>\n"
        f"    <processed_at>{escape(meta.processed_at)}</processed_at>\n"
        f"    <path_type>{escape(path_type)}</path_type>\n"
        "  </file_meta>\n"
    )


def _render_ai_decision(meta: FileMeta) -> str:
    ai = meta.ai_decision or {}
    if not ai:
        # v1 兼容：没有 ai_decision 时跳过
        return ""

    model = ai.get("model_used", "")
    elapsed_ms = ai.get("elapsed_ms", 0)
    attempt = ai.get("attempt_count", 1)
    header_row = ai.get("header_row", 1)
    data_start_row = ai.get("data_start_row", 2)
    header_note = ai.get("header_note", "")
    summary = ai.get("overall_summary", "")

    # V3：table_role 作为 ai_decision 根属性输出（unknown 不渲染）
    table_role = ai.get("table_role") or ""
    role_attr = f' table_role="{escape(table_role)}"' if table_role and table_role != "unknown" else ""

    parts: list[str] = [
        f'\n  <ai_decision priority="critical" status="ok"'
        f' model="{escape(str(model))}" attempt="{attempt}" elapsed_ms="{elapsed_ms}"{role_attr}>\n',
        f"    <header_row>{header_row}</header_row>\n",
        f"    <data_start_row>{data_start_row}</data_start_row>\n",
    ]
    if header_note:
        parts.append(f"    <header_note>{escape(header_note)}</header_note>\n")
    # table_role_note 单独成行（如果有）
    table_role_note = ai.get("table_role_note") or ""
    if table_role_note:
        parts.append(f"    <table_role_note>{escape(table_role_note)}</table_role_note>\n")

    # column_semantics
    cs_list = ai.get("column_semantics", []) or []
    parts.append("    <column_semantics>\n")
    for cs in cs_list:
        attrs = (
            f'letter="{escape(cs.get("letter", ""))}" '
            f'name="{escape(cs.get("business_name", ""))}" '
            f'type="{escape(cs.get("semantic_type", "other"))}"'
        )
        if cs.get("is_order_level"):
            attrs += ' order_level="true"'
        if cs.get("is_id_column"):
            attrs += ' is_id="true"'
        parts.append(f"      <col {attrs}/>\n")
    parts.append("    </column_semantics>\n")

    # V3 稀疏渲染：summary_rows 为空时不输出该节点（删除"AI 确认无汇总行"空标签）
    sr = ai.get("summary_rows", []) or []
    if sr:
        parts.append(f"    <summary_rows>{','.join(map(str, sr))}</summary_rows>\n")

    # regions（路径 C）
    regions = ai.get("regions", []) or []
    if regions:
        parts.append("    <regions>\n")
        for r in regions:
            attrs = (
                f'id="{r.get("region_id", 0)}" '
                f'range="{escape(r.get("range_str", ""))}" '
                f'role="{escape(r.get("role", "unknown"))}"'
            )
            reason = r.get("skip_reason") or r.get("relation_to_primary") or ""
            content = escape(reason) if reason else ""
            parts.append(f"      <region {attrs}>{content}</region>\n")
        parts.append("    </regions>\n")

    # sheets（路径 D）
    sheets = ai.get("sheets", []) or []
    if sheets:
        parts.append("    <sheets>\n")
        for s in sheets:
            attrs = (
                f'name="{escape(s.get("name", ""))}" '
                f'role="{escape(s.get("role", "unknown"))}"'
            )
            if s.get("merge_group"):
                attrs += f' merge_group="{escape(s["merge_group"])}"'
            content = escape(s.get("skip_reason", "") or "")
            parts.append(f"      <sheet {attrs}>{content}</sheet>\n")
        parts.append("    </sheets>\n")

    # data_quality_notes
    notes = ai.get("data_quality_notes", []) or []
    if notes:
        parts.append("    <data_quality_notes>\n")
        for n in notes:
            sev = n.get("severity", "info")
            parts.append(
                f'      <note severity="{escape(sev)}">{escape(n.get("note", ""))}</note>\n'
            )
        parts.append("    </data_quality_notes>\n")

    parts.append(f"    <overall_summary>{escape(summary)}</overall_summary>\n")
    parts.append("  </ai_decision>\n")
    return "".join(parts)


def _render_usage_hints(meta: FileMeta, parquet_path: str) -> str:
    """V3 P4：按 ai_decision.table_role 分支渲染 usage_hints。

    - fact/snapshot: 订单级 SUM DISTINCT 范式 + 明细级直接 SUM
    - dimension:     JOIN 范式提示（关联键候选）
    - log:           时间过滤范式 + 禁止 SELECT *
    - wide:          列选取范式（高列数，避免 SELECT *）
    - unknown:       通用 hint（has_summary / 大文件 OOM）

    稀疏渲染：没有任何 hint 和 code 时整段不输出。
    """
    parts: list[str] = []
    hint_lines: list[str] = []
    code_lines: list[str] = []

    ai = meta.ai_decision or {}
    table_role = ai.get("table_role") or "unknown"
    has_summary = bool(ai.get("summary_rows") or [])
    summary_filter = ' WHERE "_is_summary" = false' if has_summary else ""
    rows = (meta.summary or {}).get("row_count", 0)

    # 从 column_semantics 提取候选列
    cs_list = ai.get("column_semantics", []) or []
    order_level_numeric: list[str] = []
    detail_level_numeric: list[str] = []
    id_columns: list[str] = []
    string_high_card: list[str] = []
    for cs in cs_list:
        name = cs.get("business_name") or ""
        if not name:
            continue
        info = meta.schema.get(name, {})
        col_type = info.get("type")
        if col_type in ("integer", "decimal"):
            if cs.get("is_order_level"):
                order_level_numeric.append(name)
            else:
                detail_level_numeric.append(name)
        if cs.get("is_id_column") or cs.get("semantic_type") == "id":
            id_columns.append(name)
        if col_type == "string" and info.get("unique_count", 0) >= 5:
            string_high_card.append(name)

    group_key_candidate = id_columns[0] if id_columns else ""

    # ── 分角色渲染 ──
    if table_role in ("fact", "snapshot"):
        if order_level_numeric and group_key_candidate:
            cols_str = "/".join(order_level_numeric)
            hint_lines.append(
                '    <hint severity="must">\n'
                f"      {escape(cols_str)} 是订单级字段，在同一 {escape(group_key_candidate)} 内值重复，"
                f"SUM 前必须先 DISTINCT {escape(group_key_candidate)}，否则数字会虚高。\n"
                "    </hint>\n"
            )
            first_ol = order_level_numeric[0]
            from_clause = f"FROM read_parquet('{parquet_path}'){summary_filter}"
            code_lines.append(
                '    <code_example title="订单级聚合范式（必用）"><![CDATA[\n'
                f'SELECT SUM("{first_ol}") AS 总额\n'
                "FROM (\n"
                f'    SELECT DISTINCT "{group_key_candidate}", "{first_ol}"\n'
                f"    {from_clause}\n"
                ")\n"
                "]]></code_example>\n"
            )
        if detail_level_numeric:
            hint_lines.append(
                '    <hint severity="info">\n'
                f"      {escape('/'.join(detail_level_numeric))} 是明细级字段，可直接 SUM。\n"
                "    </hint>\n"
            )
            first_ll = detail_level_numeric[0]
            code_lines.append(
                '    <code_example title="明细级聚合（直接 SUM）"><![CDATA[\n'
                f"SELECT SUM(\"{first_ll}\") FROM read_parquet('{parquet_path}'){summary_filter}\n"
                "]]></code_example>\n"
            )

    elif table_role == "dimension":
        join_keys = id_columns or string_high_card
        if join_keys:
            keys_str = "/".join(join_keys[:3])
            hint_lines.append(
                '    <hint severity="info">\n'
                f"      本文件是维度/映射表（{rows:,} 行 × {len(cs_list)} 列），"
                f"主要用于 JOIN 补充信息，不是聚合主体。\n"
                f"      关联键候选: {escape(keys_str)}。\n"
                "    </hint>\n"
            )
            hint_lines.append(
                '    <hint severity="must">\n'
                "      多表 JOIN 时，SELECT/GROUP BY 引用列必须用列原始来源的别名，"
                "不要把维度列错挂到事实表别名上。\n"
                "    </hint>\n"
            )
            first_key = join_keys[0]
            code_lines.append(
                '    <code_example title="维度 JOIN 范式"><![CDATA[\n'
                "SELECT fact.*, dim.\"<维度列名>\"\n"
                "FROM read_parquet('<事实表路径>') fact\n"
                f"JOIN read_parquet('{parquet_path}') dim\n"
                f'  ON fact."<事实表关联列>" = dim."{first_key}"\n'
                "]]></code_example>\n"
            )

    elif table_role == "log":
        # 找时间列候选
        datetime_cols = [
            cs.get("business_name", "")
            for cs in cs_list
            if cs.get("semantic_type") == "datetime" and cs.get("business_name")
        ]
        if datetime_cols:
            time_col = datetime_cols[0]
            hint_lines.append(
                '    <hint severity="must">\n'
                f"      本文件是日志/事件流（{rows:,} 行），按 \"{escape(time_col)}\" 排序。\n"
                "      查询时应先按时间窗口过滤，避免全表扫描。\n"
                "    </hint>\n"
            )
            code_lines.append(
                '    <code_example title="时间窗口过滤"><![CDATA[\n'
                f'SELECT * FROM read_parquet(\'{parquet_path}\')\n'
                f'WHERE "{time_col}" >= \'YYYY-MM-DD\' AND "{time_col}" < \'YYYY-MM-DD\'\n'
                "]]></code_example>\n"
            )

    elif table_role == "wide":
        hint_lines.append(
            '    <hint severity="must">\n'
            f"      本文件是宽表（{len(cs_list)} 列指标），禁止 SELECT *，"
            "必须只选用到的指标列以节省内存。\n"
            "    </hint>\n"
        )

    # ── 通用 hint（所有角色都加）──
    if has_summary:
        hint_lines.append(
            '    <hint severity="must">\n'
            "      文件含合计行（_is_summary=true），所有聚合/统计 SQL 必须加 "
            '<code>WHERE "_is_summary" = false</code>，否则会把合计算入明细，数字虚高 2 倍。\n'
            "    </hint>\n"
        )
    if rows >= 100_000:
        hint_lines.append(
            '    <hint severity="info">\n'
            f"      {rows:,} 行规模，禁止 SELECT * .df() 全量加载会 OOM，"
            "先 SQL 聚合再 .df()。\n"
            "    </hint>\n"
        )

    # ── 稀疏渲染：没有任何 hint 和 code 时整段不输出 ──
    if not hint_lines and not code_lines:
        return ""

    parts.append('\n  <usage_hints priority="critical">\n')
    parts.extend(hint_lines)
    parts.extend(code_lines)
    parts.append("  </usage_hints>\n")
    return "".join(parts)


def _render_column_schema(meta: FileMeta) -> str:
    """V3：order_level 改读 ai_decision.column_semantics[i].is_order_level（不再依赖 grain）。"""
    parts: list[str] = ['\n  <column_schema priority="high">\n']
    ai = meta.ai_decision or {}
    order_level_set = {
        cs.get("business_name", "")
        for cs in (ai.get("column_semantics") or [])
        if cs.get("is_order_level") and cs.get("business_name")
    }

    for col_name, info in meta.schema.items():
        attrs = [
            f'letter="{escape(info.get("col", ""))}"',
            f'name="{escape(str(col_name))}"',
            f'type="{escape(info.get("type", "unknown"))}"',
            f'null_ratio="{info.get("null_ratio", 0):.3f}"',
        ]
        if "min" in info and "max" in info:
            attrs.append(f'min="{info["min"]}"')
            attrs.append(f'max="{info["max"]}"')
        elif "range" in info:
            attrs.append(f'range="{info["range"][0]} ~ {info["range"][1]}"')
        if "categories" in info:
            cats = ",".join(info["categories"][:5])
            attrs.append(f'categories="{escape(cats)}"')
        if "unique_count" in info:
            attrs.append(f'unique="{info["unique_count"]}"')
        if col_name in order_level_set:
            attrs.append('order_level="true"')
        parts.append(f"    <column {' '.join(attrs)}/>\n")

    parts.append("  </column_schema>\n")
    return "".join(parts)


# V3：删 _render_grain 函数。表角色识别下沉到 AI ai_decision.table_role（P4 接入）。


def _render_sample_data(meta: FileMeta) -> str:
    """动态采样行数 + 顶部 column_index 映射。"""
    import json as _json

    sample = meta.sample or {}
    if not any(sample.get(seg) for seg in ("head", "middle", "tail", "boundary")):
        return ""

    rows = (meta.summary or {}).get("row_count", 0)
    n_head, n_mid, n_tail = sample_segment_sizes(rows)

    parts: list[str] = ['\n  <sample_data priority="medium">\n']

    # column_index 顶部映射
    ai = meta.ai_decision or {}
    cs_list = ai.get("column_semantics", []) or []
    if cs_list:
        idx_parts = [f"{cs.get('letter', '?')}={cs.get('business_name', '?')}" for cs in cs_list]
        parts.append(f"    <column_index>{escape(' | '.join(idx_parts))}</column_index>\n")

    def _emit_segment(name: str, rows_list: list, limit: int):
        if not rows_list or limit <= 0:
            return
        parts.append(f'    <segment name="{name}">\n')
        for rd in rows_list[:limit]:
            row_num = rd.get("_row", "?")
            fields = {k: v for k, v in rd.items() if k != "_row"}
            parts.append(
                f'      <row n="{row_num}">{escape(_json.dumps(fields, ensure_ascii=False, default=str))}</row>\n'
            )
        parts.append("    </segment>\n")

    _emit_segment("head", sample.get("head") or [], n_head)
    _emit_segment("middle", sample.get("middle") or [], n_mid)
    _emit_segment("tail", sample.get("tail") or [], n_tail)
    boundary = sample.get("boundary") or []
    if boundary:
        _emit_segment("boundary", boundary, len(boundary))

    parts.append("  </sample_data>\n")
    return "".join(parts)


def _render_related_files(related: list[dict]) -> str:
    parts: list[str] = ['\n  <related_files priority="high">\n']
    for rel in related:
        rtype = rel.get("type", "")
        conf = rel.get("confidence", 0)
        parts.append(
            f'    <relation type="{escape(str(rtype))}" confidence="{conf}">\n'
        )
        other = rel.get("other_file") or rel.get("files", ["", ""])[-1]
        parts.append(f"      <file>{escape(str(other))}</file>\n")
        common_cols = rel.get("common_columns") or []
        if common_cols:
            parts.append(
                f"      <common_columns>{escape(','.join(map(str, common_cols)))}</common_columns>\n"
            )
        if rel.get("hint"):
            parts.append(f"      <hint>{escape(rel['hint'])}</hint>\n")
        parts.append("    </relation>\n")
    parts.append("  </related_files>\n")
    return "".join(parts)


def _render_cleaning_result(meta: FileMeta) -> str:
    """汇总清洗动作（AI 决策 vs 代码执行）。"""
    ai_decided: list[dict] = []
    code_executed: list[dict] = []

    for issue in (meta.issues or []):
        itype = issue.get("type", "")
        if itype in {
            "summary_rows_marked", "column_renamed", "mixed_type_extracted",
            "merge_preserved_as_group", "empty_rows_preserved",
        }:
            ai_decided.append(issue)
        elif itype in {
            "merge_filled", "int_cols_fixed", "empty_rows_removed",
            "header_flattened", "column_deduplicated", "mixed_type_coerced",
            "hidden_cols", "empty_cols",
        }:
            code_executed.append(issue)

    if not ai_decided and not code_executed:
        return ""

    parts: list[str] = ['\n  <cleaning_result priority="medium">\n']
    parts.append("    <strategy_summary>\n")
    if ai_decided:
        parts.append("      <ai_decided>\n")
        for i in ai_decided:
            parts.append(
                f'        <action type="{escape(i.get("type", ""))}">{escape(i.get("action", ""))}</action>\n'
            )
        parts.append("      </ai_decided>\n")
    if code_executed:
        parts.append("      <code_executed>\n")
        for i in code_executed:
            parts.append(
                f'        <action type="{escape(i.get("type", ""))}">{escape(i.get("action", ""))}</action>\n'
            )
        parts.append("      </code_executed>\n")
    parts.append("    </strategy_summary>\n")
    parts.append("  </cleaning_result>\n")
    return "".join(parts)


def _render_formulas(meta: FileMeta) -> str:
    formulas = meta.formulas or []
    parts: list[str] = [
        f'\n  <formulas priority="medium" total_count="{len(formulas)}">\n'
    ]
    for f in formulas[:20]:
        parts.append(
            f'    <formula cell="{escape(str(f.get("cell", "")))}"'
            f' expression="{escape(str(f.get("formula", "")))}"'
            f' value="{escape(str(f.get("value", "")))}"/>\n'
        )
    parts.append("  </formulas>\n")
    return "".join(parts)
