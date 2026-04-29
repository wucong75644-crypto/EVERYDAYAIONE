"""
文件元信息实时提取（对标 OpenAI Responses API spreadsheet augmentation）

上传工作区文件时，系统自动提取表格文件的元信息（行列数、类型、范围、分类），
注入 LLM context，让 AI 不用执行代码就知道文件结构。

设计文档：docs/document/TECH_文件元信息提取.md（待创建）
"""

import asyncio
import csv
import io
import os
from collections import Counter
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ============================================================
# 常量
# ============================================================

_SPREADSHEET_EXTS = {".xlsx", ".xls", ".csv", ".tsv"}
_SAMPLE_ROWS = 500  # 采样行数（对标 OpenAI 1000 行，ossfs 折中）
_MAX_PREVIEW_ROWS = 3  # 展示预览行数
_MAX_PREVIEW_COLS = 8  # 预览表格最多展示列数
_MAX_COLUMN_DISPLAY = 12  # 列名列表展示上限
_MAX_CATEGORY_DISPLAY = 5  # 分类值展示上限
_PER_FILE_TIMEOUT = 3.0  # 单文件超时（秒）
_TOTAL_TIMEOUT = 5.0  # 全部文件总超时（秒）
_MAX_METADATA_FILES = 5  # 最多提取文件数
_CSV_MAX_COUNT_SIZE = 50 * 1024 * 1024  # >50MB 跳过行数统计
_CATEGORY_THRESHOLD = 20  # unique ≤ 此值视为分类列


# ============================================================
# 类型推断
# ============================================================

def _is_numeric(value: Any) -> bool:
    """判断值是否为数值"""
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace("，", "")
        if not s:
            return False
        try:
            float(s)
            return True
        except ValueError:
            return False
    return False


def _to_float(value: Any) -> Optional[float]:
    """尝试转为 float"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace("，", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _is_date(value: Any) -> bool:
    """判断值是否为日期"""
    if isinstance(value, (datetime, date)):
        return True
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s or len(s) < 6:
        return False
    # 常见日期格式
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d",
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%m/%d/%Y", "%d/%m/%Y"):
        try:
            datetime.strptime(s[:19], fmt)
            return True
        except ValueError:
            continue
    return False


def _infer_column_meta(
    name: str,
    values: List[Any],
    total_rows: Optional[int],
) -> Dict[str, Any]:
    """从采样值推断单列元信息

    Returns:
        {
            "name": str,
            "dtype": "文本" | "数值" | "日期" | "布尔",
            "non_null": int,       # 估算非空数
            "sample": [str, ...],  # 前 2 个示例值
            "min": float,          # 数值列
            "max": float,          # 数值列
            "categories": [(val, count), ...],  # 分类列
        }
    """
    non_null_vals = [v for v in values if v is not None and str(v).strip() != ""]
    sample_size = len(values)
    non_null_count = len(non_null_vals)

    # 估算全量非空数
    if total_rows and sample_size > 0:
        estimated_non_null = round(total_rows * non_null_count / sample_size)
    else:
        estimated_non_null = non_null_count

    meta: Dict[str, Any] = {
        "name": name,
        "dtype": "文本",
        "non_null": estimated_non_null,
        "sample": [],
    }

    if not non_null_vals:
        return meta

    # 采集前 2 个不重复示例值
    seen = set()
    for v in non_null_vals:
        s = str(v).strip()
        if s and s not in seen:
            seen.add(s)
            meta["sample"].append(s)
            if len(meta["sample"]) >= 2:
                break

    # 类型推断
    numeric_count = sum(1 for v in non_null_vals if _is_numeric(v))
    date_count = sum(1 for v in non_null_vals if _is_date(v))
    bool_vals = {"true", "false", "是", "否", "0", "1", "yes", "no"}

    if numeric_count / len(non_null_vals) > 0.8:
        meta["dtype"] = "数值"
        # 数值范围
        floats = [f for f in (_to_float(v) for v in non_null_vals) if f is not None]
        if floats:
            meta["min"] = min(floats)
            meta["max"] = max(floats)
    elif date_count / len(non_null_vals) > 0.8:
        meta["dtype"] = "日期"
    elif all(str(v).lower().strip() in bool_vals for v in non_null_vals):
        meta["dtype"] = "布尔"

    # 分类检测（unique ≤ 阈值，且非数值/日期列）
    if meta["dtype"] == "文本":
        unique_vals = set(str(v).strip() for v in non_null_vals if str(v).strip())
        if 1 < len(unique_vals) <= _CATEGORY_THRESHOLD:
            counter = Counter(str(v).strip() for v in non_null_vals if str(v).strip())
            # 按出现次数降序，估算全量计数
            categories = []
            for val, cnt in counter.most_common(_MAX_CATEGORY_DISPLAY + 1):
                estimated = round(cnt * (total_rows or sample_size) / sample_size)
                categories.append((val, estimated))
            meta["categories"] = categories
            meta["_unique_count"] = len(unique_vals)

    return meta


# ============================================================
# 编码检测（复用 wecom/file_parser.py 模式）
# ============================================================

def _decode_bytes(data: bytes) -> str:
    """尝试 UTF-8 → GBK → Latin-1 解码"""
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace")


# ============================================================
# 核心提取函数
# ============================================================

def extract_spreadsheet_metadata(abs_path: str) -> Optional[Dict[str, Any]]:
    """从表格文件提取元信息（同步，在线程池中执行）

    Args:
        abs_path: 文件绝对路径

    Returns:
        元信息 dict 或 None（提取失败）
    """
    if not os.path.exists(abs_path):
        return None

    ext = Path(abs_path).suffix.lower()
    try:
        if ext == ".xlsx":
            return _extract_xlsx(abs_path)
        elif ext == ".xls":
            # openpyxl 不支持 .xls (Excel 97-2003)，降级返回 None
            # 用户上传 .xls 时 LLM 仍可用 code_execute 读取
            logger.debug(f"Skipping .xls metadata extraction (openpyxl unsupported) | path={abs_path}")
            return None
        elif ext in (".csv", ".tsv"):
            return _extract_csv(abs_path, delimiter="\t" if ext == ".tsv" else ",")
        return None
    except Exception as e:
        logger.warning(
            f"File metadata extraction failed | path={abs_path} | "
            f"error={type(e).__name__}: {e}"
        )
        return None


def _extract_xlsx(abs_path: str) -> Optional[Dict[str, Any]]:
    """提取 xlsx/xls 元信息"""
    from openpyxl import load_workbook

    wb = load_workbook(abs_path, read_only=True, data_only=True)
    try:
        # 提取所有 sheet 名称和行列数
        sheet_names = wb.sheetnames
        sheets_info = []
        for sn in sheet_names:
            s = wb[sn]
            sheets_info.append({
                "name": sn,
                "rows": (s.max_row or 0) - 1 if s.max_row and s.max_row > 0 else 0,
                "cols": s.max_column or 0,
            })

        # 选第一个 sheet 做详细提取
        ws = wb[sheet_names[0]]
        total_rows = ws.max_row
        total_cols = ws.max_column

        # 减去表头行
        data_rows = (total_rows - 1) if total_rows and total_rows > 0 else 0

        # 读取采样行（表头 + 数据行）
        max_read = min(_SAMPLE_ROWS + 1, (total_rows or _SAMPLE_ROWS + 1))
        all_rows = []
        for row in ws.iter_rows(min_row=1, max_row=max_read, values_only=True):
            all_rows.append(list(row))
            if len(all_rows) >= max_read:
                break

        if not all_rows:
            return None

        # 表头
        header = [str(c).strip() if c is not None else f"列{i+1}"
                  for i, c in enumerate(all_rows[0])]
        data = all_rows[1:]

        # 按列聚合
        columns_meta = []
        for col_idx, col_name in enumerate(header):
            col_values = [row[col_idx] if col_idx < len(row) else None for row in data]
            columns_meta.append(_infer_column_meta(col_name, col_values, data_rows))

        # 预览行
        preview_rows = []
        for row in data[:_MAX_PREVIEW_ROWS]:
            preview_rows.append([
                _format_cell(row[i] if i < len(row) else None)
                for i in range(min(len(header), _MAX_PREVIEW_COLS))
            ])

        result = {
            "row_count": data_rows,
            "col_count": total_cols or len(header),
            "columns": columns_meta,
            "preview_rows": preview_rows,
        }
        # 多 sheet 信息（仅多 sheet 时注入，单 sheet 不展示）
        if len(sheets_info) > 1:
            result["sheets"] = sheets_info
        return result
    finally:
        wb.close()


def _extract_csv(abs_path: str, delimiter: str = ",") -> Optional[Dict[str, Any]]:
    """提取 csv/tsv 元信息

    单次读取策略：流式扫描文件，前 500 行进入采样，之后只计数不存储。
    避免 ossfs 上的双次 IO 延迟。
    """
    file_size = os.path.getsize(abs_path)

    # 读取整个文件的原始字节（编码检测需要）
    # 对于大文件（>50MB），只读头部用于采样，跳过行数统计
    skip_count = file_size > _CSV_MAX_COUNT_SIZE
    if skip_count:
        with open(abs_path, "rb") as f:
            raw = f.read(2 * 1024 * 1024)  # 2MB 足够覆盖 500 行宽表
    else:
        with open(abs_path, "rb") as f:
            raw = f.read()

    text = _decode_bytes(raw)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    # 单次扫描：前 _SAMPLE_ROWS+1 行进入采样，之后只计数
    sample_rows: List[List[str]] = []
    total_line_count = 0
    for row in reader:
        total_line_count += 1
        if total_line_count <= _SAMPLE_ROWS + 1:
            sample_rows.append(row)

    if not sample_rows:
        return None

    header = [c.strip() if c.strip() else f"列{i+1}" for i, c in enumerate(sample_rows[0])]
    data = sample_rows[1:]

    # 行数：total_line_count 包含表头，减 1 得数据行数
    # 大文件跳过计数时，如果采样不足 _SAMPLE_ROWS 说明 2MB 内全部读完
    total_data_rows: Optional[int] = None
    if not skip_count:
        total_data_rows = max(0, total_line_count - 1)
    elif len(data) < _SAMPLE_ROWS:
        total_data_rows = len(data)

    # 按列聚合
    columns_meta = []
    for col_idx, col_name in enumerate(header):
        col_values = [row[col_idx] if col_idx < len(row) else None for row in data]
        columns_meta.append(_infer_column_meta(col_name, col_values, total_data_rows))

    # 预览行
    preview_rows = []
    for row in data[:_MAX_PREVIEW_ROWS]:
        preview_rows.append([
            _format_cell(row[i] if i < len(row) else None)
            for i in range(min(len(header), _MAX_PREVIEW_COLS))
        ])

    return {
        "row_count": total_data_rows,
        "col_count": len(header),
        "columns": columns_meta,
        "preview_rows": preview_rows,
    }


def _format_cell(value: Any) -> str:
    """格式化单元格值用于预览展示"""
    if value is None:
        return ""
    s = str(value).strip()
    # 截断过长的值
    if len(s) > 30:
        return s[:27] + "..."
    return s


# ============================================================
# 异步批量包装
# ============================================================

async def extract_metadata_for_files(
    workspace_files: List[Dict[str, Any]],
    workspace_dir: str,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """批量提取文件元信息（并行 + 超时保护）

    Args:
        workspace_files: workspace 文件列表 [{workspace_path, name, size, ...}]
        workspace_dir: 用户 workspace 绝对路径

    Returns:
        {workspace_path: metadata_dict_or_None}
    """
    result: Dict[str, Optional[Dict[str, Any]]] = {}

    # 过滤出表格文件
    spreadsheets = []
    for f in workspace_files:
        wp = f.get("workspace_path", "")
        ext = Path(wp).suffix.lower() if wp else ""
        if ext in _SPREADSHEET_EXTS:
            spreadsheets.append(f)

    if not spreadsheets:
        return result

    # 限制最大提取数
    spreadsheets = spreadsheets[:_MAX_METADATA_FILES]

    loop = asyncio.get_running_loop()

    async def _extract_one(ws_file: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
        wp = ws_file["workspace_path"]
        abs_path = os.path.join(workspace_dir, wp)
        try:
            meta = await asyncio.wait_for(
                loop.run_in_executor(None, extract_spreadsheet_metadata, abs_path),
                timeout=_PER_FILE_TIMEOUT,
            )
            return wp, meta
        except Exception as e:
            logger.debug(f"Metadata extraction skipped | path={wp} | error={e}")
            return wp, None

    try:
        tasks = [_extract_one(f) for f in spreadsheets]
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=_TOTAL_TIMEOUT,
        )
        for r in results:
            if isinstance(r, tuple):
                result[r[0]] = r[1]
            # BaseException 忽略
    except asyncio.TimeoutError:
        logger.warning(
            f"File metadata extraction global timeout | "
            f"files={[f['workspace_path'] for f in spreadsheets]}"
        )

    return result


# ============================================================
# 格式化输出
# ============================================================

def format_workspace_files_prompt(
    workspace_files: List[Dict[str, Any]],
    metadata_map: Dict[str, Optional[Dict[str, Any]]],
) -> str:
    """将工作区文件信息格式化为 LLM system prompt

    自动选择档位：
    - ≤2 个表格文件 → 标准档（详细表格）
    - 3~5 个表格文件 → 紧凑档
    - >5 个 → 前 5 个紧凑档 + 提示
    """
    if not workspace_files:
        return ""

    _BINARY_EXTS = {
        ".doc", ".docx", ".ppt", ".pptx",
        ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".zip", ".tar", ".gz", ".parquet",
    }

    # 单次遍历分类：表格文件 vs 其他文件
    spreadsheet_entries: List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]] = []
    other_entries: List[str] = []

    for f in workspace_files:
        wp = f.get("workspace_path", "")
        ext = Path(wp).suffix.lower() if wp else ""
        size_str = _fmt_size(f.get("size"))

        if ext in _SPREADSHEET_EXTS:
            spreadsheet_entries.append((f, metadata_map.get(wp)))
        elif ext in _BINARY_EXTS:
            other_entries.append(f"📄 {wp} ({size_str})\n  用 code_execute 读取")
        else:
            other_entries.append(f"📄 {wp} ({size_str})\n  用 file_read 读取")

    # 选择档位
    spreadsheet_count = len(spreadsheet_entries)
    use_compact = spreadsheet_count > 2

    parts = ["用户工作区中有以下文件，可直接按文件名引用：\n"]

    for i, (f, meta) in enumerate(spreadsheet_entries):
        if i >= _MAX_METADATA_FILES:
            remaining = spreadsheet_count - _MAX_METADATA_FILES
            parts.append(f"\n另有 {remaining} 个表格文件，用 file_list 查看")
            break

        wp = f.get("workspace_path", "")
        size_str = _fmt_size(f.get("size"))

        if meta is None:
            # 提取失败降级
            parts.append(
                f"📊 {wp} ({size_str})\n"
                f"  用 code_execute 分析: pd.read_excel('{wp}')"
            )
            continue

        if use_compact:
            parts.append(_format_compact(wp, size_str, meta))
        else:
            parts.append(_format_standard(wp, size_str, meta))

    # 非表格文件
    if other_entries:
        parts.append("")
        parts.extend(other_entries)

    return "\n".join(parts)


def _format_standard(
    wp: str, size_str: str, meta: Dict[str, Any]
) -> str:
    """标准档格式化（详细表格，~200-300 tokens）"""
    row_count = meta.get("row_count")
    col_count = meta.get("col_count", 0)
    columns = meta.get("columns", [])
    preview_rows = meta.get("preview_rows", [])

    row_label = f"{row_count:,}" if row_count is not None else "未知"

    lines = [
        f"📊 {wp} | {row_label} 行 × {col_count} 列",
        f"路径: {wp}",
        "",
    ]

    # 列元信息表格（最多 _MAX_PREVIEW_COLS 列）
    display_cols = columns[:_MAX_PREVIEW_COLS]
    if display_cols:
        lines.append("| 列名 | 类型 | 非空 | 示例值 |")
        lines.append("|------|------|------|--------|")

        for col in display_cols:
            name = col["name"]
            dtype = col["dtype"]
            non_null = col.get("non_null", "")
            non_null_str = f"{non_null:,}" if isinstance(non_null, int) else str(non_null)

            # 构建示例值
            sample_parts = []
            if col.get("categories"):
                cats = col["categories"]
                for val, cnt in cats[:_MAX_CATEGORY_DISPLAY]:
                    sample_parts.append(f'"{val}"({cnt:,})')
                unique = col.get("_unique_count", len(cats))
                if unique > _MAX_CATEGORY_DISPLAY:
                    sample_parts.append(f"+{unique - _MAX_CATEGORY_DISPLAY}类")
            elif col.get("sample"):
                for s in col["sample"]:
                    sample_parts.append(f'"{s}"' if dtype != "数值" else s)
                if col.get("min") is not None and col.get("max") is not None:
                    mn, mx = col["min"], col["max"]
                    mn_s = f"{mn:,.0f}" if float(mn).is_integer() else f"{mn:,.2f}"
                    mx_s = f"{mx:,.0f}" if float(mx).is_integer() else f"{mx:,.2f}"
                    sample_parts.append(f"[范围: {mn_s}~{mx_s}]")

            sample_str = ", ".join(sample_parts) if sample_parts else ""
            lines.append(f"| {name} | {dtype} | {non_null_str} | {sample_str} |")

        if len(columns) > _MAX_PREVIEW_COLS:
            lines.append(f"| ... | | | (共{col_count}列，显示前{_MAX_PREVIEW_COLS}列) |")

    # 多 sheet 提示
    sheets = meta.get("sheets")
    if sheets:
        sheet_parts = [f"{s['name']}({s['rows']}行)" for s in sheets]
        lines.append(f"\n📑 多 Sheet: {', '.join(sheet_parts)}")
        lines.append(f"  以上为第一个 Sheet「{sheets[0]['name']}」的信息。"
                     f"读其他 Sheet: pd.read_excel('{wp}', sheet_name='Sheet名')")

    # 读取指引
    ext = Path(wp).suffix.lower()
    if ext in (".csv", ".tsv"):
        lines.append(f"\n用 code_execute 分析: pd.read_csv('{wp}')")
    else:
        if not sheets:
            lines.append(f"\n用 code_execute 分析: pd.read_excel('{wp}')")

    return "\n".join(lines)


def _format_compact(
    wp: str, size_str: str, meta: Dict[str, Any]
) -> str:
    """紧凑档格式化（单行摘要，~80 tokens）"""
    row_count = meta.get("row_count")
    col_count = meta.get("col_count", 0)
    columns = meta.get("columns", [])

    row_label = f"{row_count:,}" if row_count is not None else "?"

    # 列摘要
    col_parts = []
    for col in columns[:_MAX_COLUMN_DISPLAY]:
        name = col["name"]
        dtype = col["dtype"]
        extra = ""
        if col.get("min") is not None and col.get("max") is not None:
            mn, mx = col["min"], col["max"]
            mn_s = f"{mn:,.0f}" if float(mn).is_integer() else f"{mn:.1f}"
            mx_s = f"{mx:,.0f}" if float(mx).is_integer() else f"{mx:.1f}"
            extra = f",{mn_s}~{mx_s}"
        elif col.get("_unique_count"):
            extra = f",{col['_unique_count']}类"

        # 标注空值
        non_null = col.get("non_null", 0)
        row_total = row_count or 0
        null_count = row_total - non_null if row_total and non_null else 0
        if null_count > 0:
            extra += f",{null_count}空"

        col_parts.append(f"{name}({dtype}{extra})")

    cols_str = ", ".join(col_parts)
    if len(columns) > _MAX_COLUMN_DISPLAY:
        cols_str += ", ..."

    ext = Path(wp).suffix.lower()
    read_cmd = "pd.read_csv" if ext in (".csv", ".tsv") else "pd.read_excel"

    # 多 sheet 紧凑提示
    sheets = meta.get("sheets")
    sheet_hint = ""
    if sheets:
        sheet_names = [s["name"] for s in sheets]
        sheet_hint = f"\n  📑 {len(sheets)} Sheets: {', '.join(sheet_names)}"

    return (
        f"📊 {wp} | {row_label}×{col_count} | 路径: {wp}\n"
        f"  列: {cols_str}{sheet_hint}\n"
        f"  用 {read_cmd}('{wp}') 读取"
    )


def _fmt_size(size: Optional[int]) -> str:
    """格式化文件大小"""
    if not size:
        return "未知大小"
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / 1024 / 1024:.1f}MB"
