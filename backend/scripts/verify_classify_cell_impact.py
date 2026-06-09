"""验证 _classify_cell 加 percentage/placeholder 对 _is_data_row 和 _looks_like_header 的影响。

不动生产代码,在脚本里:
1. 实现"假修改后"的 _classify_cell
2. 同步修改"_is_data_row"(加 percentage)
3. 不动 "_looks_like_header" (评估它的破坏)
4. 跑各种真实/边缘场景,对比改前 vs 改后判断结果
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 导入原始实现作为对比基线
from services.agent.data_query_cache import (
    _classify_cell as _classify_old,
    _is_data_row as _is_data_row_old,
    _looks_like_header as _looks_like_header_old,
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
    """改后版本: 加 percentage / placeholder 分类。"""
    if value is None:
        return "empty"
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "nan", "<na>"):
        return "empty"
    # 🆕 占位符独立分类
    if s in _PLACEHOLDERS:
        return "placeholder"
    # 🆕 百分比独立分类
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
    """改后: percentage 也算数据。"""
    classes = [_classify_new(v) for v in row_values]
    non_empty = [c for c in classes if c != "empty"]
    if not non_empty:
        return False
    # 🆕 加 percentage
    data_n = sum(1 for c in non_empty
                 if c in ("numeric", "date", "long_id", "percentage"))
    return data_n / len(non_empty) >= threshold


def _looks_like_header_new(row_values) -> bool:
    """不动. 评估是否破坏。"""
    classes = [_classify_new(v) for v in row_values]
    non_empty = [c for c in classes if c != "empty"]
    if len(non_empty) < 2:
        return False
    text_ratio = sum(1 for c in non_empty if c == "text") / len(non_empty)
    if text_ratio < 0.7:
        return False
    vals = [str(v).strip() for v in row_values if v is not None and str(v).strip()]
    if len(set(vals)) <= 1:
        return False
    return True


# ── 测试场景 ──

DATA_ROW_CASES = [
    # (描述, row, 期望)
    ("纯金额行", ["销售额", "1001", "5826.22", "100.00%", "5826.22"], True),
    ("含 - 占位符", ["特殊单销售额", "1002", "0", "0.00%", "0"], True),
    ("含 - placeholder", ["毛利率", "", "55.51%", "-", "55.51%"], True),  # ⚠️ 关键 case
    ("含 — 长破折号", ["净利率", "", "-13.73%", "—", "-13.73%"], True),  # ⚠️
    ("纯文本(注释行)", ["这是一条说明", "", "", "", ""], False),
    ("含'无'占位符", ["商品 A", "无", "100", "20%", "无"], True),  # ⚠️
    ("全空行", [None, None, None, None, None], False),
    ("订单号 + 数字 + 百分比", ["20231201001", "客户A", "1000.50", "10.5%", "已发货"], True),
    ("ID 类长数字", ["12345678901234", "客户名", "100", "", ""], True),
]

HEADER_ROW_CASES = [
    # (描述, row, 期望)
    ("纯文本表头(理想)", ["科目", "科目编码", "金额", "占比", "店铺金额"], True),
    ("含 - 占位符表头(1/5)", ["科目", "编码", "金额", "-", "店铺金额"], "?"),
    ("含全角破折号 — 表头(1/5)", ["科目", "编码", "—", "占比", "店铺金额"], "?"),
    ("含 N/A 表头(1/5)", ["科目", "编码", "N/A", "占比", "金额"], "?"),
    ("纯英文表头", ["Order", "Date", "Amount", "Status", "Note"], True),
    ("含百分比的表头(罕见)", ["科目", "占比%", "金额", "数量", "状态"], "?"),
    ("全合并标题行", ["报表", "报表", "报表", "报表", "报表"], False),
    ("含数字的'表头'(应判否)", ["列1", "100", "200", "300", "状态"], False),
    ("含长数字的'表头'", ["列1", "12345678901234", "金额", "状态", "备注"], False),
    # 🔥 边界压力测试
    ("⚠️ 2/5 占位符表头", ["科目", "-", "金额", "-", "状态"], "?"),
    ("⚠️ 3/5 占位符(极端)", ["-", "-", "-", "金额", "状态"], "?"),
    ("⚠️ 中文'无'表头(1/4)", ["科目", "无", "金额", "状态"], "?"),
    ("⚠️ '/'占位符表头(1/5)", ["科目", "编码", "/", "占比", "金额"], "?"),
    ("⚠️ 多占位符极端 4/5", ["-", "—", "N/A", "/", "状态"], "?"),
    ("⚠️ 空 cell 全空", ["", "", "", "", ""], False),
    ("⚠️ 2 列 + 1 占位符", ["科目", "金额", "-"], "?"),
]

DATA_ROW_CASES.extend([
    # 🔥 边界压力测试
    ("⚠️ 全占位符 5/5", ["-", "—", "N/A", "/", "无"], "?"),
    ("⚠️ 4 占位符 + 1 数字", ["-", "-", "1234.56", "-", "-"], True),
    ("⚠️ 全空 + 1 占位符", [None, None, "-", None, None], False),
    ("⚠️ 百分比+占位符混合行", ["毛利率", "55.51%", "-", "-", "55.51%"], True),
])


def main():
    print("=" * 80)
    print("# _is_data_row 影响验证")
    print("=" * 80)
    print(f"{'描述':<35} {'old':>6} {'new':>6}  {'状态'}")
    print("-" * 80)
    breaks = 0
    for desc, row, expected in DATA_ROW_CASES:
        old = _is_data_row_old(row)
        new = _is_data_row_new(row)
        status = "✅ 一致" if old == new else f"❌ **变化** old={old} new={new}"
        if old != new:
            breaks += 1
        print(f"{desc:<35} {str(old):>6} {str(new):>6}  {status}")
    print(f"\n{'='*80}\n# _is_data_row 总结: {breaks} 个差异")

    print(f"\n{'='*80}\n# _looks_like_header 影响验证\n{'='*80}")
    print(f"{'描述':<35} {'old':>6} {'new':>6}  {'状态'}")
    print("-" * 80)
    breaks2 = 0
    for desc, row, expected in HEADER_ROW_CASES:
        old = _looks_like_header_old(row)
        new = _looks_like_header_new(row)
        status = "✅ 一致" if old == new else f"⚠️  **变化** old={old} new={new}"
        if old != new:
            breaks2 += 1
        print(f"{desc:<35} {str(old):>6} {str(new):>6}  {status}")
    print(f"\n{'='*80}\n# _looks_like_header 总结: {breaks2} 个差异")

    print(f"\n# 总结")
    print(f"  _is_data_row 差异:        {breaks}")
    print(f"  _looks_like_header 差异:  {breaks2}")
    print(f"  改动安全: {'✅ 是' if breaks == 0 else '❌ 否 - 需评估'}")


if __name__ == "__main__":
    main()
