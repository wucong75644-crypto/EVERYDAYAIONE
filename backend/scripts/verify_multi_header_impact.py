"""验证 _classify_cell 改动对多级表头识别的影响。

关键测试: 多级表头里如果含占位符 (合并 cell 留空 / 用户填 '-'),
改后 _looks_like_header 是否破坏多级表头识别?

测试链路:
  Row 1 (大类) → _looks_like_header(Row 1)
  Row 2 (具体列名) → _looks_like_header(Row 2)
  Row 3+ (数据) → _is_data_row(Row 3)
  detect_header_row 找表头行号
  detect_header_depth(header_row, merged_ranges) → 多级深度
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 原始实现
from services.agent.data_query_cache import (
    _classify_cell as _classify_old,
    _is_data_row as _is_data_row_old,
    _looks_like_header as _looks_like_header_old,
    detect_header_row as detect_header_row_old,
    detect_header_depth,
)


# ── 改后版本 ──

_RE_NUMERIC = re.compile(r'^-?[\d,]+\.?\d*(e[+-]?\d+)?$', re.IGNORECASE)
_RE_DATE = re.compile(
    r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}'
    r'(\s+\d{1,2}:\d{1,2}(:\d{1,2})?)?$'
)
_RE_LONG_ID = re.compile(r'^\d{10,}$')
_PLACEHOLDERS = frozenset({
    "-", "—", "─", "/", "N/A", "NA", "n/a", "无", "空", "尚未", "未知",
})


def _classify_new(value) -> str:
    """改后: 加 percentage / placeholder."""
    if value is None:
        return "empty"
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "nan", "<na>"):
        return "empty"
    if s in _PLACEHOLDERS:
        return "placeholder"
    if s.endswith("%"):
        s_pct = s.rstrip("%")
        if _RE_NUMERIC.match(s_pct):
            return "percentage"
    if _RE_LONG_ID.match(s):
        return "long_id"
    if _RE_DATE.match(s):
        return "date"
    s_clean = s.lstrip("¥$￥").rstrip("%").replace(",", "")
    if _RE_NUMERIC.match(s_clean):
        return "numeric"
    return "text"


def _is_data_row_new(row_values, threshold: float = 0.3) -> bool:
    classes = [_classify_new(v) for v in row_values]
    non_empty = [c for c in classes if c != "empty"]
    if not non_empty:
        return False
    # 加 percentage
    data_n = sum(1 for c in non_empty
                 if c in ("numeric", "date", "long_id", "percentage"))
    return data_n / len(non_empty) >= threshold


def _looks_like_header_new(row_values, with_placeholder_fix: bool = False) -> bool:
    """两种版本:
    - with_placeholder_fix=False: 直接修 _classify_cell 不动 _looks_like_header (有破坏)
    - with_placeholder_fix=True: placeholder 也算 text (零破坏)
    """
    classes = [_classify_new(v) for v in row_values]
    non_empty = [c for c in classes if c != "empty"]
    if len(non_empty) < 2:
        return False
    if with_placeholder_fix:
        text_ratio = sum(1 for c in non_empty
                         if c in ("text", "placeholder")) / len(non_empty)
    else:
        text_ratio = sum(1 for c in non_empty if c == "text") / len(non_empty)
    if text_ratio < 0.7:
        return False
    vals = [str(v).strip() for v in row_values if v is not None and str(v).strip()]
    if len(set(vals)) <= 1:
        return False
    return True


def detect_header_row_new(rows, with_placeholder_fix=False):
    if not rows:
        return 0
    scan_rows = rows[:50]
    for i in range(min(len(scan_rows) - 1, 50)):
        if _is_data_row_new(scan_rows[i]):
            continue
        if not _looks_like_header_new(scan_rows[i], with_placeholder_fix):
            continue
        if _is_data_row_new(scan_rows[i + 1]):
            return i
    return 0


# ── 多级表头测试场景 ──

MULTI_HEADER_CASES = [
    {
        "name": "标准多级表头(全文本)",
        "rows": [
            ["销售", "销售", "库存", "库存"],
            ["数量", "金额", "入库", "出库"],
            [10, 100, 5, 3],
            [20, 200, 10, 6],
        ],
        "expect_header_row": 1,
    },
    {
        "name": "多级表头(合并 cell 读出 None)",
        "rows": [
            ["销售", None, "库存", None],
            ["数量", "金额", "入库", "出库"],
            [10, 100, 5, 3],
        ],
        "expect_header_row": 1,
    },
    {
        "name": "⚠️ 多级表头(合并 cell 填充 '-' 占位符)",
        "rows": [
            ["销售", "-", "库存", "-"],
            ["数量", "金额", "入库", "出库"],
            [10, 100, 5, 3],
        ],
        "expect_header_row": 1,
    },
    {
        "name": "⚠️ 多级表头(合并 cell 填充 'N/A')",
        "rows": [
            ["销售", "N/A", "库存", "N/A"],
            ["数量", "金额", "入库", "出库"],
            [10, 100, 5, 3],
        ],
        "expect_header_row": 1,
    },
    {
        "name": "单级表头(占位符在列名)",
        "rows": [
            ["科目", "-", "金额", "占比"],
            ["销售", "1", 5826.22, "100%"],
            ["退款", "2", -254.9, "-4%"],
        ],
        "expect_header_row": 0,
    },
    {
        "name": "前置说明 + 多级表头",
        "rows": [
            ["店铺利润表 2026-06", None, None, None],
            ["单位: 元", None, None, None],
            ["销售", "销售", "库存", "库存"],
            ["数量", "金额", "入库", "出库"],
            [10, 100, 5, 3],
        ],
        "expect_header_row": 3,
    },
    {
        "name": "⚠️ 前置说明 + 多级表头(含 '-')",
        "rows": [
            ["店铺利润表 2026-06", None, None, None],
            ["单位: 元", None, None, None],
            ["销售", "-", "库存", "-"],
            ["数量", "金额", "入库", "出库"],
            [10, 100, 5, 3],
        ],
        "expect_header_row": 3,
    },
    {
        "name": "纯数据(无表头,fallback)",
        "rows": [
            [10, 100, 5, 3],
            [20, 200, 10, 6],
        ],
        "expect_header_row": 0,
    },
]


def main():
    print("=" * 110)
    print("# 多级表头识别 — 改前 vs 改后(2 种版本)")
    print("=" * 110)
    print(f"{'场景':<45} {'old':>6} {'new_no_fix':>14} {'new_with_fix':>14}  {'状态'}")
    print("-" * 110)

    breaks_no_fix = 0
    breaks_with_fix = 0

    for case in MULTI_HEADER_CASES:
        rows = case["rows"]
        expected = case["expect_header_row"]

        old = detect_header_row_old(rows)
        new_no_fix = detect_header_row_new(rows, with_placeholder_fix=False)
        new_with_fix = detect_header_row_new(rows, with_placeholder_fix=True)

        def fmt(v, expected):
            if v == expected:
                return f"{v}✓"
            return f"{v}✗"

        # 改后 no_fix 是否破坏
        if old != new_no_fix:
            breaks_no_fix += 1
            status1 = "❌ no_fix 破坏"
        else:
            status1 = "✅ no_fix OK"
        # 改后 with_fix 是否兼容
        if old != new_with_fix:
            breaks_with_fix += 1
            status2 = "with_fix 也破坏!"
        else:
            status2 = "with_fix OK"

        print(f"{case['name']:<45} "
              f"{fmt(old, expected):>6} "
              f"{fmt(new_no_fix, expected):>14} "
              f"{fmt(new_with_fix, expected):>14}  "
              f"{status1} | {status2}")

    print()
    print(f"{'='*110}")
    print(f"# 总结")
    print(f"  改 _classify_cell 不修 _looks_like_header — 破坏: {breaks_no_fix}/{len(MULTI_HEADER_CASES)}")
    print(f"  改 _classify_cell + 修 _looks_like_header(加 placeholder 算 text) — 破坏: {breaks_with_fix}/{len(MULTI_HEADER_CASES)}")
    print()
    if breaks_with_fix == 0:
        print("  ✅ 修复方案(with_fix) 完全兼容多级表头识别")
    else:
        print(f"  ❌ 修复方案仍有 {breaks_with_fix} 个破坏 case")


if __name__ == "__main__":
    main()
