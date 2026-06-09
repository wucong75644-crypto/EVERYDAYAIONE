"""file_analyze 重构 - AI prompt 模板。

从 file_ai_judge 拆出，保持 judge 文件聚焦于调用链与错误处理。

variant:
  - "default":     完整 prompt（含全部 evidence）
  - "simplified":  retry 时减小 prompt（仅头尾样本 + 表头候选）

V3：极简普适 prompt — 不教 LLM 业务关键词（货币/订单/金额...），
让 LLM 用训练得来的常识看 sample 自判。

设计文档：docs/document/TECH_file_analyze_V3_骨架抽取重构.md §1 / §4
"""
from __future__ import annotations

from services.agent.file_evidence import EvidencePool


# 输出 JSON Schema —— 每字段只标类型 + 一句话用途，不列业务关键词
JSON_SCHEMA_TEMPLATE = """
{
  "header_row": <int, Excel 1-indexed>,
  "data_start_row": <int, Excel 1-indexed; 必须 > header_row>,
  "header_type": "single | multi_level",
  "header_note": "<可选,特殊说明>",

  "column_semantics": [
    {
      "letter": "A",                                       // Excel 列字母
      "business_name": "<推断的业务列名;空列填 ''>",
      "semantic_type": "id|name|datetime|amount|quantity|address|note|category|other",
      "is_order_level": <bool>,                            // 同主键内值是否重复(订单级字段 SUM 前需 DISTINCT)
      "is_id_column": <bool>,                              // 是否 ID 类(清洗保 string 不转 int)
      "notes": "<可选>"
    }
  ],

  "summary_rows": [<Excel 行号>],                           // 你识别的汇总/合计/累计/小计 行
  "unit_rows": [<Excel 行号>],                              // 含 (单位:xxx) / 计量说明 的行
  "note_rows": [<Excel 行号>],                              // 你识别的备注/版权/数据来源 行

  "merged_cell_actions": [
    {
      "range_str": "A2:H2",
      "action": "treat_as_header | fill_down | preserve_as_group | skip",
      "reason": "<可选>"
    }
  ],
  "mixed_type_handling": [
    {
      "col_letter": "F",
      "action": "force_str | extract_unit_number | extract_currency_amount | to_datetime",
      "unit": "<extract_unit_number 时必填,提取的单位字符串>",
      "reason": "<可选>"
    }
  ],
  "preserve_empty_rows": [{"row": <int>, "reason": "<可选>"}],

  "regions": [
    {
      "region_id": 1,
      "range_str": "A1:H100",
      "role": "primary | secondary | metadata | skip",
      "relation_to_primary": "<可选>",
      "skip_reason": "<可选>"
    }
  ],
  "sheets": [
    {
      "name": "<sheet 名>",
      "role": "data | meta | aggregated | skip",
      "merge_group": "<同组合并键,留空=独立>",
      "skip_reason": "<可选>"
    }
  ],

  "data_quality_notes": [
    {
      "severity": "info | warning | error",
      "note": "<给主 Agent 的提示,结构异常/数据缺陷/风险提示等>",
      "affected_rows": [<int>],
      "affected_cols": ["<列字母>"]
    }
  ],

  "overall_summary": "<简明总结:文件用途/规模/关键字段/使用注意,约 100-300 字>",

  "table_role": "fact | dimension | log | wide | snapshot | unknown",
  "table_role_note": "<一句话理由>"
}
""".strip()


# 顶部任务说明 + 思考流程 + 强制规则（V3）
TASK_BLOCK = """
# 角色
你是 Excel/CSV 结构分析专家。代码已经扫了文件结构,把"骨架位置"和"原始值"打包好了。
你的任务: 看完所有证据,一次性输出 AIDecision JSON,不中途纠结。

# 思考流程(按顺序,一次走完)
1. 看「表头候选」前 5 行 → 判定 header_row / data_start_row
   - 哪几行是标题/标语 → note_rows
   - 哪一行是计量单位/币种 说明 → unit_rows
   - 哪一行是真表头 → header_row

2. 看「列证据 + 样本」→ 给每列填 business_name + semantic_type
   - 看样本内容用你的常识判断业务语义,不要凭列名瞎猜
   - is_id_column=true: 看 sample 是 ID 类(长数字串/UUID/带连字符或前缀的编码等,
     清洗时要保 string 避免 int 精度丢失)
   - is_order_level=true: 该数值列在同业务主键内值重复(SUM 前需 DISTINCT)
     用 sample + unique_count + 常识判断,不确定填 false (默认安全)

3. 看「可疑行原始值」→ 判定每行业务角色
   - 看 raw_values 内容,你自己判断是汇总/单位说明/备注/数据起始/异常
   - 把行号分别写入 summary_rows / unit_rows / note_rows

4. 看「关键样本 + 整体规模」→ 判 table_role
   - fact:      明细级数据,有 ID + 多个数值聚合字段,同 ID 多行(一对多)
   - dimension: 维度/映射表,列以 string 为主,无聚合数值,有候选 join key
   - log:       日志/事件流,按时间排序,无主键聚合
   - wide:      宽表,列数明显多于行数特征数,每行一个 entity 多个指标列
   - snapshot:  快照表,某时点全量,无时间维度
   - unknown:   无法判断时填(不强求选)

5. 看「合并单元格 / 公式 / 多区域 / 多 sheet」→ 决定清洗动作

6. 看「列样本」→ 识别 ragged 混合类型(同列业务异构)
   - 触发: 同列样本同时出现 数字('123.45') + 百分比('47.40%') + 占位符('-')
     典型: 利润表"合计"列(金额行 vs 率类行混合)、KPI 宽表
   - 处理:
     a. mixed_type_handling: action='force_str'(整列保留字符串,不破坏原值)
     b. data_quality_notes 加 severity='warning':
        note 模板: "列 X 含 ragged 混合(金额+率+占位符),用沙盒内置 safe_float() 按行处理:
                    df['X_num'] = df['X'].apply(safe_float)
                    (47.40%→0.474, '-'/NaN→0, 数字保留)"
        affected_cols=[相关列字母]
   - 禁止: 用 extract_percentage 整列转换(会把金额行也 ÷ 100,毁数据)

7. 看「列样本」→ 识别中文占位符变体
   - 触发: 样本含 '无'/'空'/'/' / '——'/'─'/'尚未'/'未知'/'N/A' 等中文/混合占位符
   - 处理: mixed_type_handling action='force_str',data_quality_notes 加 severity='info':
     "列 X 含中文占位符 [示例],数值计算时用 sandbox safe_float() 自动转 0"
     (safe_float 通过 try/except 路径已兜底,无需改 helper)

8. 看「列样本 + 关键样本」→ 识别 Excel 公式错误值
   - 触发: 样本中出现 '#DIV/0!' / '#REF!' / '#NAME?' / '#VALUE!' / '#N/A' / '#NULL!' / '#NUM!'
   - 含义: Excel 公式计算失败,这些是错误状态值(不是真实数据)
   - 处理: data_quality_notes 加 severity='error':
     "列 X 含 N 个 Excel 公式错误值 (#DIV/0! 等),会被 safe_float 转 0 污染统计。
      计算前必须过滤: df[~df['X'].astype(str).str.startswith('#')]"
     affected_cols=[列字母]

9. 看「列证据」→ 识别 Excel 日期 serial number
   - 触发: 列名含"日期/时间/月份/年份" + sample 是 5 位整数(40000~50000 区间)
     原因: Excel 把日期 "2026-05-01" 内部存为 serial 45414(1900-01-01 起的天数)
   - 处理: column_semantics.semantic_type='datetime',mixed_type_handling action='to_datetime'
     data_quality_notes 加 severity='info':
     "列 X 是 Excel 日期 serial,清洗后已转 datetime"

10. 看「列证据」→ 识别 pandas 同名列后缀
    - 触发: 原始表头含 '.1' / '.2' / '.3' 后缀(如"金额.1"、"日期.1")
    - 含义: 原 Excel 有重名列,pandas 读时自动加后缀
    - 处理: data_quality_notes 加 severity='warning':
      "列 X 是同名列(pandas 自动加 .N 后缀),原始 Excel 有 2+ 列同名 [原名]。
       LLM 引用时需明确用后缀名,避免误用"
      affected_cols=[列字母]

# 强制规则
- MUST 严格 JSON 输出,不要 markdown 代码块包裹
- MUST 所有文本字段(business_name / overall_summary / table_role_note / data_quality_notes / 各 reason / notes)用与文件内容一致的语言(文件中文则中文,英文则英文),不要混语
- MUST 每列都要有 1 条 column_semantics(包括空列,business_name='')
- MUST 不确定时填默认值(other / unknown / false / 空 list),不要瞎猜
- MUST 用你的常识看 sample 内容,不要因列名长得像就贴标签
- MUST 一行同时像 summary/unit/note 时,按 summary > unit > note 优先级只填一处,不重复
- MUST NOT 输出超出 schema 的字段
- MUST 一次性给出全部判断,不要思考过程的中间推理

""".strip() + "\n\n"


def build_prompt(evidence: EvidencePool, variant: str = "default") -> str:
    """构造 AI 裁决 prompt。

    V3 结构:
      1. 角色 + 思考流程 + 强制规则(TASK_BLOCK)
      2. 数据证据(文件信息 / 表头候选 / 列证据 / 可疑行 / 关键样本 / 多区域 / 多 sheet / 公式 / 结构)
      3. 输出 JSON Schema(JSON_SCHEMA_TEMPLATE)
    """
    parts: list[str] = []

    # ── 1. 任务 + 思考流程 + 规则 ──
    parts.append(TASK_BLOCK)

    # ── 2. 数据证据 ──
    parts.append("# 文件信息\n")
    parts.append(f"- 文件名: {evidence.file_name}\n")
    parts.append(f"- 总行数: {evidence.total_rows:,}\n")
    parts.append(f"- 总列数: {evidence.total_cols}\n")
    parts.append(f"- 当前 Sheet: {evidence.target_sheet}\n")
    parts.append(f"- 处理路径: {evidence.path_type}\n\n")

    parts.append("# 表头候选(前 5 行原始)\n")
    for i, row in enumerate(evidence.header_candidates, start=1):
        parts.append(f"Row {i}: {_truncate_row(row)}\n")
    parts.append(f"\n代码兜底检测表头行: Row {evidence.detected_header_row_code + 1}\n\n")

    # 列证据
    parts.append("# 列证据\n")
    for col_ev in evidence.columns:
        # V3：仅保留纯统计驱动的 flag(long_id_candidate)
        flags = []
        if col_ev.is_long_id_candidate:
            flags.append("⚠️ 长ID候选(清洗时应保 string)")
        flag_str = (" " + " ".join(flags)) if flags else ""
        parts.append(
            f"列 {col_ev.col_letter}: 原始表头='{col_ev.raw_header}', "
            f"类型分布={col_ev.classified_dist}, null率={col_ev.null_ratio:.2%}, "
            f"unique={col_ev.unique_count}{flag_str}\n"
        )
        if variant != "simplified":
            sample_preview = col_ev.sample_values[:8] if col_ev.sample_values else []
            parts.append(f"  样本: {_truncate_list(sample_preview)}\n")
    parts.append("\n")

    # 可疑行
    if evidence.suspicious_rows:
        limit = 10 if variant == "simplified" else 50
        parts.append(f"# 可疑行(共 {len(evidence.suspicious_rows)} 条,展示前 {limit})\n")
        for sr in evidence.suspicious_rows[:limit]:
            parts.append(
                f"Row {sr.row}: null率={sr.null_ratio:.0%}\n"
                f"  原始值: {_truncate_list(sr.raw_values[:10])}\n"
            )
        parts.append("\n")

    # 关键样本
    if evidence.key_samples:
        limit = 6 if variant == "simplified" else 30
        parts.append("# 关键样本\n")
        for sample in evidence.key_samples[:limit]:
            parts.append(f"Row {sample['row']}: {_truncate_list(sample['cells'])}\n")
        parts.append("\n")

    # 路径 C 多区域
    if evidence.path_type == "C" and evidence.regions:
        parts.append("# 候选数据区域(路径 C,你裁决每个区域的 role)\n")
        for r in evidence.regions:
            parts.append(
                f"Region {r.region_id} ({r.range_str}): {r.row_count} 行, 表头={_truncate_list(r.header_cells)}\n"
            )
            if r.head_sample:
                parts.append(f"  Head: {_truncate_row(r.head_sample[0])}\n")
            if r.tail_sample:
                parts.append(f"  Tail: {_truncate_row(r.tail_sample[-1])}\n")
        parts.append("\n")

    # 路径 D 多 sheet
    if evidence.path_type == "D" and evidence.sheets:
        parts.append("# 所有 Sheet 元信息(路径 D,你裁决每个 sheet 的 role / merge_group)\n")
        for s in evidence.sheets:
            rows_str = "未采样" if s.rows == -1 else f"{s.rows} 行"
            parts.append(
                f"Sheet '{s.name}': {rows_str} × {s.cols} 列, 列名={_truncate_list(s.column_names)}\n"
            )
            if variant != "simplified" and s.head_sample:
                parts.append(f"  Head: {_truncate_row(s.head_sample[0])}\n")
            # V3.2: 每个 sheet 各自的列证据(unique/null/类型),让 AI 在多 sheet 场景
            # 判 is_order_level 时有数据支撑(对齐 PathA 体验)
            if variant != "simplified" and s.columns:
                parts.append(f"  列证据({len(s.columns)} 列):\n")
                for col_ev in s.columns:
                    flags = []
                    if col_ev.is_long_id_candidate:
                        flags.append("⚠️ 长ID候选")
                    flag_str = (" " + " ".join(flags)) if flags else ""
                    parts.append(
                        f"    {col_ev.col_letter} {col_ev.raw_header!r}: "
                        f"类型分布={col_ev.classified_dist}, "
                        f"null率={col_ev.null_ratio:.1%}, "
                        f"unique={col_ev.unique_count}{flag_str}\n"
                    )
                    sample_preview = col_ev.sample_values[:5] if col_ev.sample_values else []
                    parts.append(f"      样本: {_truncate_list(sample_preview)}\n")
        parts.append("\n")

    # 公式
    if evidence.formulas:
        parts.append(
            f"# 公式(共 {evidence.formula_total_count} 个,展示前 10)\n"
        )
        for f in evidence.formulas[:10]:
            parts.append(f"- {f.cell}: {f.expression} = {f.value}\n")
        parts.append("\n")

    # 结构元信息（V3 稀疏：全空时不输出）
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

    # ── 3. 输出 schema ──
    parts.append("# 输出格式(严格 JSON,不要 markdown 代码块包裹)\n")
    parts.append(JSON_SCHEMA_TEMPLATE)

    return "".join(parts)


# ── 辅助函数：截断长值避免 prompt 爆炸 ──

def _truncate_str(val, maxlen: int = 60) -> str:
    s = str(val) if val is not None else ""
    if len(s) <= maxlen:
        return s
    return s[:maxlen - 3] + "..."


def _truncate_row(row, max_cells: int = 25) -> str:
    """单元格行 → 字符串,长值截断。"""
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
