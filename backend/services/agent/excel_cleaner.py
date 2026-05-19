"""Excel 三层清洗模块（结构检测 / 智能清洗 / 质量校验）"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import pandas as pd
from loguru import logger

# ── 安全阀 ──
_MAX_XML_SIZE = 500 * 1024 * 1024  # 500MB: 解压后 XML 超此大小跳过结构检测


@dataclass
class ExcelStructure:
    """Layer 1 检测结果。"""

    merged_ranges: list[tuple[int, int, int, int]] = field(default_factory=list)
    # (min_row, max_row, min_col, max_col) — 1-indexed, 与 Excel 一致
    hidden_rows: set[int] = field(default_factory=set)   # 1-indexed
    hidden_cols: set[int] = field(default_factory=set)    # 1-indexed
    has_auto_filter: bool = False

@dataclass
class CleaningReport:
    """清洗报告，写入 .meta.json 供 executor 探索模式注入 LLM 上下文。"""

    merged_cols_filled: int = 0
    summary_rows_marked: int = 0
    hidden_rows_marked: int = 0
    hidden_cols_names: list[str] = field(default_factory=list)
    empty_cols_removed: int = 0
    empty_rows_removed: int = 0
    int_cols_fixed: int = 0
    has_auto_filter: bool = False
    warnings: list[str] = field(default_factory=list)  # 旧字段保留兼容
    issues: list[dict] = field(default_factory=list)    # 统一结构化标注
    original_shape: tuple[int, int] = (0, 0)
    final_shape: tuple[int, int] = (0, 0)
    # 行号映射参数（file_meta.py 生成完整 .meta.json 时使用）
    header_row: int = 0
    data_start_row: int = 2
    row_offset: int = 1

    def merge(self, other: CleaningReport) -> None:
        """将另一个报告累加到自身（多 Sheet / 分块合并场景）。"""
        for attr in ("merged_cols_filled", "summary_rows_marked",
                      "hidden_rows_marked", "empty_cols_removed",
                      "empty_rows_removed", "int_cols_fixed"):
            setattr(self, attr, getattr(self, attr) + getattr(other, attr))
        self.hidden_cols_names = list(set(self.hidden_cols_names + other.hidden_cols_names))
        self.has_auto_filter = self.has_auto_filter or other.has_auto_filter
        self.warnings = list(set(self.warnings + other.warnings))
        self.issues = self.issues + other.issues
        self.original_shape = (self.original_shape[0] + other.original_shape[0],
                               max(self.original_shape[1], other.original_shape[1]))
        self.final_shape = (self.final_shape[0] + other.final_shape[0],
                            max(self.final_shape[1], other.final_shape[1]))
        # 行号映射：保留首块的值（首块决定了 header 位置）
        if self.header_row == 0 and other.header_row > 0:
            self.header_row = other.header_row
            self.data_start_row = other.data_start_row
            self.row_offset = other.row_offset

    def has_changes(self) -> bool:
        return any([
            self.merged_cols_filled, self.summary_rows_marked,
            self.hidden_rows_marked, self.hidden_cols_names,
            self.empty_cols_removed, self.empty_rows_removed,
            self.int_cols_fixed, self.has_auto_filter, self.warnings,
        ])

    def to_llm_text(self) -> str:
        """生成注入 LLM 上下文的简洁报告。"""
        parts: list[str] = []
        if self.merged_cols_filled:
            parts.append(f"合并单元格精确填充（{self.merged_cols_filled}个）")
        if self.summary_rows_marked:
            parts.append(f"标记合计行（{self.summary_rows_marked}行）")
        if self.hidden_rows_marked:
            parts.append(f"标记隐藏行（{self.hidden_rows_marked}行）")
        if self.empty_cols_removed:
            parts.append(f"删除空列（{self.empty_cols_removed}列）")
        if self.empty_rows_removed:
            parts.append(f"删除空行（{self.empty_rows_removed}行）")
        if self.int_cols_fixed:
            parts.append(f"整数修复（{self.int_cols_fixed}列）")
        if not parts and not self.has_auto_filter:
            return ""

        lines: list[str] = []
        if parts:
            lines.append(f"[数据清洗] {'| '.join(parts)}")
        lines.append(
            f"清洗前: {self.original_shape[0]}行×{self.original_shape[1]}列 "
            f"→ 清洗后: {self.final_shape[0]}行×{self.final_shape[1]}列"
        )
        if self.hidden_cols_names:
            lines.append(f"⚠ 隐藏列: {self.hidden_cols_names}（数据保留，建议按需排除）")
        if self.hidden_rows_marked:
            lines.append("⚠ 建议查询时加: WHERE _is_hidden = false")
        if self.has_auto_filter:
            lines.append("注意: 数据包含自动筛选，已读取全部行（非筛选结果）")
        for w in self.warnings:
            lines.append(f"⚠ {w}")
        return "\n".join(lines)


def clean_excel(
    df: pd.DataFrame,
    excel_path: str,
    sheet_name: str | int,
    header_row: int = 0,
    structure: ExcelStructure | None = None,
    special_rows: dict[str, list[int]] | None = None,
    chunk_row_offset: int = 0,  # 大文件分块时的数据行偏移量（不含 header）
) -> tuple[pd.DataFrame, CleaningReport]:
    """清洗入口：merge 填充 + 表头展平 + 去重 + 合计行标记 + 空行列 + 类型修正。"""
    report = CleaningReport(original_shape=(len(df), len(df.columns)))

    # Step 1: 多级表头展平（MultiIndex → 单行，用 _ 连接）
    _flatten_multi_header(df, report)

    # Step 2: 合并单元格精确填充（按 merge range，不是全列 ffill）
    if structure and structure.merged_ranges:
        _apply_merge_fill(df, structure, header_row, report, chunk_row_offset)

    # Step 3: 列名去重
    _deduplicate_columns(df, report)

    # Step 4: 合计行标记（prescan 识别的 summary 行）
    if special_rows:
        _mark_summary_rows(df, special_rows, header_row, report, chunk_row_offset)

    # Step 5: 空行空列处理（全空行删除，空列标注）
    _remove_empty_rows_cols(df, report, structure)

    # Step 6-7: 类型修正
    _coerce_object_columns(df, report)
    _fix_int_columns(df, report)

    report.final_shape = (len(df), len(df.columns))
    if report.has_changes():
        logger.info(
            f"Excel cleaned | src={Path(excel_path).name} "
            f"| {report.original_shape} → {report.final_shape}"
        )
    return df, report


def write_cleaning_report(cache_path: str, report: CleaningReport) -> None:
    """将清洗报告写入 .meta.json（与 Parquet 缓存同目录）。"""
    if not report.has_changes():
        return
    meta_path = cache_path.replace(".parquet", ".meta.json")
    data = asdict(report)
    # tuple → list for JSON serialization
    data["original_shape"] = list(data["original_shape"])
    data["final_shape"] = list(data["final_shape"])
    Path(meta_path).write_text(json.dumps(data, ensure_ascii=False))


def read_cleaning_report(cache_path: str) -> CleaningReport | None:
    """读取 .meta.json，不存在时返回 None。"""
    meta_path = cache_path.replace(".parquet", ".meta.json")
    p = Path(meta_path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        data["original_shape"] = tuple(data["original_shape"])
        data["final_shape"] = tuple(data["final_shape"])
        return CleaningReport(**data)
    except Exception:
        return None

# Layer 1: regex 模式（预编译）
_RE_MERGE = re.compile(r'<mergeCell\s+ref="([A-Z]+)(\d+):([A-Z]+)(\d+)"')
_RE_HIDDEN_ROW = re.compile(
    r'<row\s[^>]*?r="(\d+)"[^>]*?hidden="1"'
    r'|<row\s[^>]*?hidden="1"[^>]*?r="(\d+)"'
)
_RE_HIDDEN_COL = re.compile(
    r'<col\s[^>]*?min="(\d+)"[^>]*?max="(\d+)"[^>]*?hidden="1"'
    r'|<col\s[^>]*?hidden="1"[^>]*?min="(\d+)"[^>]*?max="(\d+)"'
)
_RE_AUTO_FILTER = re.compile(r'<autoFilter\b')

# workbook.xml sheet 解析
def _parse_sheet_tags(xml: str) -> list[tuple[str, str, str]]:
    """从 workbook.xml 提取 <sheet> 标签，不依赖属性顺序。

    Returns: [(name, sheetId, rId), ...]
    """
    results = []
    for m in re.finditer(r'<sheet\s([^>]*?)/?>', xml):
        attrs = m.group(1)
        name_m = re.search(r'name="([^"]*)"', attrs)
        sid_m = re.search(r'sheetId="(\d+)"', attrs)
        rid_m = re.search(r'r:id="(rId\d+)"', attrs)
        if name_m and rid_m:
            results.append((
                name_m.group(1),
                sid_m.group(1) if sid_m else "0",
                rid_m.group(1),
            ))
    return results
_RE_REL = re.compile(
    r'<Relationship\s[^>]*?Id="(rId\d+)"[^>]*?Target="([^"]*worksheet[^"]*)"'
    r'|<Relationship\s[^>]*?Target="([^"]*worksheet[^"]*)"[^>]*?Id="(rId\d+)"'
)


def _col_letter_to_index(col_str: str) -> int:
    """Excel 列字母 → 1-indexed 数字（A=1, B=2, ..., Z=26, AA=27）。"""
    result = 0
    for ch in col_str.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result


def _resolve_sheet_xml_path(
    zf: ZipFile, sheet_name: str | int,
) -> str | None:
    """解析 xlsx ZIP 内 sheet 名/索引 → XML 路径。"""
    try:
        wb_xml = zf.read("xl/workbook.xml").decode("utf-8", errors="replace")
    except KeyError:
        return None

    sheets = _parse_sheet_tags(wb_xml)
    if not sheets:
        return None

    # 确定目标 rId
    if isinstance(sheet_name, int):
        if sheet_name >= len(sheets):
            return None
        target_rid = sheets[sheet_name][2]
    else:
        target_rid = None
        name_lower = str(sheet_name).lower().strip()
        for name, _, rid in sheets:
            if name.lower().strip() == name_lower:
                target_rid = rid
                break
        if target_rid is None:
            return None

    # rId → XML 路径
    try:
        rels_xml = zf.read("xl/_rels/workbook.xml.rels").decode(
            "utf-8", errors="replace"
        )
    except KeyError:
        return None

    for m in _RE_REL.finditer(rels_xml):
        rid = m.group(1) or m.group(4)
        target = m.group(2) or m.group(3)
        if rid == target_rid:
            # Target 可能是 "/xl/worksheets/sheet1.xml" 或 "worksheets/sheet1.xml"
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            return target
    return None


def _detect_structure(
    excel_path: str, sheet_name: str | int,
) -> ExcelStructure | None:
    """Layer 1: 从 xlsx ZIP 内 XML 提取结构元数据。失败时返回 None（降级）。"""
    if not excel_path.lower().endswith((".xlsx", ".xlsm")):
        return None  # .xls 旧格式不是 ZIP，无法解析

    try:
        with ZipFile(excel_path, "r") as zf:
            xml_path = _resolve_sheet_xml_path(zf, sheet_name)
            if xml_path is None:
                return None

            # 安全阀：检查解压大小
            for info in zf.infolist():
                if info.filename == xml_path:
                    if info.file_size > _MAX_XML_SIZE:
                        logger.warning(
                            f"Excel XML too large ({info.file_size:,} bytes), "
                            f"skip structure detection | {Path(excel_path).name}"
                        )
                        return None
                    break

            raw = zf.read(xml_path).decode("utf-8", errors="replace")
    except (BadZipFile, KeyError, OSError) as e:
        logger.debug(f"Excel structure detection failed: {e}")
        return None

    structure = ExcelStructure()

    # 合并区域
    for m in _RE_MERGE.finditer(raw):
        min_col = _col_letter_to_index(m.group(1))
        min_row = int(m.group(2))
        max_col = _col_letter_to_index(m.group(3))
        max_row = int(m.group(4))
        structure.merged_ranges.append((min_row, max_row, min_col, max_col))

    # 隐藏行
    for m in _RE_HIDDEN_ROW.finditer(raw):
        row_num = int(m.group(1) or m.group(2))
        structure.hidden_rows.add(row_num)

    # 隐藏列
    for m in _RE_HIDDEN_COL.finditer(raw):
        min_c = int(m.group(1) or m.group(3))
        max_c = int(m.group(2) or m.group(4))
        for c in range(min_c, max_c + 1):
            structure.hidden_cols.add(c)

    # 自动筛选
    structure.has_auto_filter = bool(_RE_AUTO_FILTER.search(raw))

    del raw  # 释放 XML 字符串内存
    return structure


def _flatten_multi_header(df: pd.DataFrame, report: CleaningReport | None = None) -> None:
    """将 MultiIndex 列名展平为单行（用 _ 连接）+ 标注原始多级结构。"""
    if not isinstance(df.columns, pd.MultiIndex):
        return
    # 记录原始多级结构
    original_levels = [list(level) for level in df.columns.levels]
    flat: list[str] = []
    for col_tuple in df.columns:
        parts = [str(p).strip() for p in col_tuple if str(p).strip() and str(p) != "nan"]
        name = "_".join(parts) if parts else "Unnamed"
        flat.append(name)
    df.columns = flat
    if report is not None:
        report.issues.append({
            "type": "header_flattened",
            "severity": "info",
            "location": {},
            "preserved": False,
            "action": f"多级表头（{len(original_levels)}层）已用 _ 连接展平为单行",
            "recovery_hint": "原始层级信息见 merged_cells，可根据业务语义重命名列",
        })


def _apply_merge_fill(
    df: pd.DataFrame,
    structure: ExcelStructure,
    header_row: int,
    report: CleaningReport,
    chunk_row_offset: int = 0,
) -> None:
    """按 merge range 精确填充合并单元格。

    只填充 merge 范围内的空值，不做全列 ffill。
    header 区域的合并跳过（由 _flatten_multi_header 处理）。
    """
    if not structure.merged_ranges:
        return

    # 数据起始行（Excel 1-indexed）：header 在 header_row+1，数据从 header_row+2 开始
    data_start_excel = header_row + 2
    # 当前 chunk 对应的 Excel 行范围
    chunk_start_excel = data_start_excel + chunk_row_offset
    chunk_end_excel = chunk_start_excel + len(df) - 1
    filled = 0

    for min_row, max_row, min_col, max_col in structure.merged_ranges:
        # 跳过 header 区域的合并
        if max_row < data_start_excel:
            continue
        # 合并范围限制到数据区域
        eff_min_row = max(min_row, data_start_excel)
        # 跳过不在当前 chunk 范围内的合并
        if eff_min_row > chunk_end_excel or max_row < chunk_start_excel:
            continue
        # 左上角值必须在当前 chunk 内（跨块合并跳过）
        if eff_min_row < chunk_start_excel:
            continue

        # Excel 行号 → DataFrame 行索引
        df_start = eff_min_row - chunk_start_excel
        df_end = min(max_row, chunk_end_excel) - chunk_start_excel
        # Excel 列号 → DataFrame 列索引（1-indexed → 0-indexed）
        col_start = min_col - 1
        col_end = min(max_col - 1, len(df.columns) - 1)

        for ci in range(col_start, col_end + 1):
            if ci >= len(df.columns):
                break
            fill_val = df.iloc[df_start, ci]
            if pd.isna(fill_val):
                continue
            for ri in range(df_start + 1, df_end + 1):
                if ri >= len(df):
                    break
                if pd.isna(df.iloc[ri, ci]):
                    df.iloc[ri, ci] = fill_val
                    filled += 1

    report.merged_cols_filled = filled
    if filled:
        report.issues.append({
            "type": "merge_filled",
            "severity": "info",
            "location": {},
            "preserved": False,
            "action": f"合并单元格精确填充（{filled}个单元格）",
            "recovery_hint": "合并区域内的空值已用左上角值填充，非全列ffill",
        })


def _mark_summary_rows(
    df: pd.DataFrame,
    special_rows: dict[str, list[int]],
    header_row: int,
    report: CleaningReport,
    chunk_row_offset: int = 0,
) -> None:
    """用 prescan 识别的合计行位置标记 _is_summary 列。

    只要 special_rows 有 summary 条目就始终添加 _is_summary 列（默认 False），
    确保分块场景下每个 chunk 都有此列，不会被 _cast_to_schema 丢弃。
    """
    summary_excel_rows = special_rows.get("summary", [])
    if not summary_excel_rows:
        return

    # 始终添加列（分块场景下保证每个 chunk 都有此列）
    df["_is_summary"] = False

    data_start_excel = header_row + 2
    chunk_start_excel = data_start_excel + chunk_row_offset

    matched = []
    for excel_row in summary_excel_rows:
        df_idx = excel_row - chunk_start_excel
        if 0 <= df_idx < len(df):
            matched.append(df_idx)

    if not matched:
        return

    df.loc[matched, "_is_summary"] = True
    report.summary_rows_marked = len(matched)
    report.issues.append({
        "type": "summary_rows_marked",
        "severity": "info",
        "location": {"rows": [i + chunk_start_excel for i in matched]},
        "preserved": True,
        "action": f"标记{len(matched)}个合计行（_is_summary=True）",
        "recovery_hint": "查询时加: WHERE _is_summary = false 排除合计行",
    })


def _mark_hidden_rows(
    df: pd.DataFrame,
    structure: ExcelStructure,
    header_row: int,
    report: CleaningReport,
) -> None:
    """标记隐藏行（_is_hidden 列）。"""
    if not structure.hidden_rows:
        return
    # Excel 1-indexed row → pandas index:
    # pd.read_excel(header=N) 数据从第 N+1 行开始(0-indexed)，即 Excel 第 N+2 行(1-indexed)
    offset = header_row + 2
    pandas_indices = {r - offset for r in structure.hidden_rows if r >= offset}
    valid = pandas_indices & set(df.index)
    if not valid:
        return
    df["_is_hidden"] = False
    df.loc[list(valid), "_is_hidden"] = True
    report.hidden_rows_marked = len(valid)


def _mark_hidden_cols(
    df: pd.DataFrame,
    structure: ExcelStructure,
    report: CleaningReport,
) -> None:
    """标注隐藏列（不删除，数据保留）。"""
    if not structure.hidden_cols:
        return
    hidden_names = []
    for col_1indexed in sorted(structure.hidden_cols):
        pandas_col = col_1indexed - 1
        if pandas_col < len(df.columns):
            hidden_names.append(str(df.columns[pandas_col]))
    if hidden_names:
        report.hidden_cols_names = hidden_names
        report.issues.append({
            "type": "hidden_cols",
            "severity": "info",
            "location": {"cols": hidden_names},
            "preserved": True,
            "action": "数据保留，未删除隐藏列",
            "recovery_hint": "按需排除隐藏列: SELECT 时不选这些列",
        })


def _remove_empty_rows_cols(
    df: pd.DataFrame,
    report: CleaningReport,
    structure: ExcelStructure | None = None,
) -> None:
    """删除全 NaN 行和全 NaN 列，但保留合并区域内的空列。"""
    # 合并区域覆盖的列索引（1-indexed → 0-indexed）
    merged_col_indices: set[int] = set()
    if structure:
        for min_row, max_row, min_col, max_col in structure.merged_ranges:
            for c in range(min_col, max_col + 1):
                merged_col_indices.add(c - 1)

    # 空列：不删除，只标注位置（AI 决定是否需要）
    empty_col_names: list[str] = []
    for i, col in enumerate(df.columns):
        col_str = str(col)
        if col_str.startswith("_is_"):
            continue
        if i in merged_col_indices:
            continue  # 合并区域内的空列不标注（已有 merged_cells 信息）
        if df.iloc[:, i].isna().all():
            empty_col_names.append(col_str)
    report.empty_cols_removed = 0
    if empty_col_names:
        report.issues.append({
            "type": "empty_cols",
            "severity": "info",
            "location": {"cols": empty_col_names},
            "preserved": True,
            "action": "全空列已保留，未删除",
            "recovery_hint": "如确认无用，查询时不选这些列即可",
        })

    # 空行：删除所有全空行（含中间和尾部）
    # 纯空白字符串也视为空值（Excel 常见：空格占位但无业务意义）
    data_cols = [c for c in df.columns if not str(c).startswith("_is_")]
    if data_cols:
        blank_mask = df[data_cols].apply(
            lambda col: col.isna() | col.astype(str).str.strip().eq("") | col.astype(str).eq("nan")
        ).all(axis=1)
    else:
        blank_mask = pd.Series(False, index=df.index)
    empty_row_indices = list(df[blank_mask].index)

    if empty_row_indices:
        df.drop(empty_row_indices, inplace=True)
        df.reset_index(drop=True, inplace=True)
        report.empty_rows_removed = len(empty_row_indices)
        report.issues.append({
            "type": "empty_rows_removed",
            "severity": "info",
            "location": {"rows": [i + 1 for i in empty_row_indices[:20]]},
            "preserved": False,
            "action": f"删除了 {len(empty_row_indices)} 个全空行",
            "recovery_hint": "原始文件中的空行已被删除",
        })
    else:
        report.empty_rows_removed = 0


def _fix_int_columns(df: pd.DataFrame, report: CleaningReport) -> None:
    """将全整数的 float64 列转回 nullable Int64（防止 123 → 123.0）。"""
    fixed = 0
    for col in df.columns:
        if str(col).startswith("_is_"):
            continue
        if df[col].dtype != "float64":
            continue
        non_null = df[col].dropna()
        if len(non_null) == 0:
            continue
        # 检查是否全部为整数值（捕获极大值溢出）
        try:
            if (non_null == non_null.astype("int64")).all():
                df[col] = df[col].astype("Int64")
                fixed += 1
        except (OverflowError, ValueError):
            pass
    report.int_cols_fixed = fixed


def _deduplicate_columns(df: pd.DataFrame, report: CleaningReport) -> None:
    """列名重复时加后缀 _1 _2 去重 + 标注到 report。"""
    cols = list(df.columns)
    seen: dict[str, int] = {}
    new_cols: list[str] = []
    duplicated: list[str] = []
    for c in cols:
        c_str = str(c)
        if c_str in seen:
            seen[c_str] += 1
            new_cols.append(f"{c_str}_{seen[c_str]}")
            if c_str not in duplicated:
                duplicated.append(c_str)
        else:
            seen[c_str] = 0
            new_cols.append(c_str)
    if new_cols != [str(c) for c in cols]:
        df.columns = new_cols
        report.issues.append({
            "type": "column_deduplicated",
            "severity": "info",
            "location": {"cols": duplicated},
            "preserved": False,
            "action": f"重复列名已加后缀 _1/_2: {duplicated}",
            "recovery_hint": "结合 merged_cells 信息重命名（如 3月_金额 / 4月_金额）",
        })

def _coerce_object_columns(df: pd.DataFrame, report: CleaningReport) -> None:
    """混合类型列统一为 str（防止 PyArrow 崩溃）+ 标注到 report。"""
    coerced: list[str] = []
    for col in df.columns:
        if str(col).startswith("_is_"):
            continue
        if df[col].dtype != object:
            continue
        non_null = df[col].dropna()
        if len(non_null) == 0:
            continue
        inferred = pd.api.types.infer_dtype(non_null, skipna=True)
        if inferred in ("mixed", "mixed-integer", "mixed-integer-float"):
            df[col] = df[col].astype(str).replace({"nan": None})
            coerced.append(str(col))
    if coerced:
        report.issues.append({
            "type": "mixed_type_coerced",
            "severity": "warning",
            "location": {"cols": coerced},
            "preserved": False,
            "action": f"混合类型列已转为文本: {coerced}",
            "recovery_hint": "用 pd.to_numeric(df['列名'], errors='coerce') 还原数字",
        })
