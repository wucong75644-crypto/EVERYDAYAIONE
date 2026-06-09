"""验证 V2: 改后的 _classify_cell + 全列扫描的完整组合。

核心问题: 现在的 13 样本到底漏掉多少 "罕见型" 信号?
"""
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

# ── 改后版本 _classify_cell ──

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


def classified_13_sample(series, clf=_classify_new) -> dict:
    n = len(series)
    idx_set: list[int] = []
    if n > 0:
        idx_set.extend(range(min(5, n)))
        if n > 8:
            mid = n // 2
            idx_set.extend(range(mid, min(mid + 3, n)))
        if n > 13:
            idx_set.extend(range(max(0, n - 5), n))
    idx_set = list(dict.fromkeys(idx_set))
    classified: dict[str, int] = {}
    for ridx in idx_set:
        cls = clf(series.iat[ridx])
        classified[cls] = classified.get(cls, 0) + 1
    return classified


def classified_full(series, clf=_classify_new) -> dict:
    classified: dict[str, int] = {}
    for v in series.values:
        cls = clf(v)
        classified[cls] = classified.get(cls, 0) + 1
    return classified


# ── 真实场景(改后 _classify_cell) ──

def make_profit_col_c(n: int) -> pd.Series:
    """利润表 C 列: 99% 金额 + 1% 百分比(ragged mixed type)。"""
    rng = random.Random(42)
    vals = []
    for _ in range(n):
        if rng.random() < 0.01:
            vals.append(f"{rng.uniform(0.1, 99.9):.2f}%")
        else:
            vals.append(round(rng.uniform(100, 99999), 2))
    return pd.Series(vals)


def make_profit_col_c_concentrated(n: int) -> pd.Series:
    """利润表 C 列(真实更接近): % 都在表底部几行(合计行/比率行)。"""
    rng = random.Random(46)
    pct_at_end = 5  # 末尾 5 行是 %
    vals = []
    for i in range(n):
        if i >= n - pct_at_end:  # 末尾几行是 %
            vals.append(f"{rng.uniform(40, 60):.2f}%")
        else:
            vals.append(round(rng.uniform(100, 99999), 2))
    return pd.Series(vals)


def make_placeholder_in_middle(n: int) -> pd.Series:
    """占位符集中在中段(合并 cell 那种)。"""
    rng = random.Random(47)
    vals = []
    for i in range(n):
        # 中间 5% 是占位符
        if 0.3 * n < i < 0.35 * n:
            vals.append(rng.choice(["-", "N/A"]))
        else:
            vals.append(round(rng.uniform(100, 99999), 2))
    return pd.Series(vals)


def main():
    print("=" * 110)
    print("# 改后 _classify_cell — 13 样本 vs 全列扫(精度对比)")
    print("=" * 110)
    print(f"{'场景':<55} {'13 样本':<35} {'全列':<35}")
    print("-" * 110)

    cases = [
        ("利润表 C 列(1% 散落 %, n=154)", make_profit_col_c, 154),
        ("利润表 C 列(1% 散落 %, n=1000)", make_profit_col_c, 1000),
        ("利润表 C 列(末尾 5 行 %, n=154)", make_profit_col_c_concentrated, 154),
        ("利润表 C 列(末尾 5 行 %, n=1000)", make_profit_col_c_concentrated, 1000),
        ("利润表 C 列(末尾 5 行 %, n=10000)", make_profit_col_c_concentrated, 10000),
        ("占位符集中中段(n=1000)", make_placeholder_in_middle, 1000),
        ("占位符集中中段(n=10000)", make_placeholder_in_middle, 10000),
    ]

    for desc, maker, n in cases:
        series = maker(n)
        c13 = classified_13_sample(series)
        cfull = classified_full(series)
        rare13 = set(c13.keys()) & {"percentage", "placeholder"}
        rarefull = set(cfull.keys()) & {"percentage", "placeholder"}
        missed = rarefull - rare13
        flag = "❌ 13 样本漏" if missed else "✅ 都能看到"
        print(f"{desc:<55} {str(c13):<35} {str(cfull):<35}  {flag}")

    print()
    print("=" * 110)
    print("# 性能 — 改后 _classify_cell 全列扫")
    print("=" * 110)
    print(f"{'行数':>10}  {'13 样本(ms)':>14} {'全列(ms)':>14}")
    print("-" * 60)
    for n_rows in [1000, 10_000, 100_000, 600_000]:
        series = make_profit_col_c(n_rows)
        t0 = time.perf_counter()
        for _ in range(10):
            classified_13_sample(series)
        t13 = (time.perf_counter() - t0) / 10 * 1000

        t0 = time.perf_counter()
        classified_full(series)
        tfull = (time.perf_counter() - t0) * 1000

        print(f"{n_rows:>10,}  {t13:>14.3f} {tfull:>14.3f}")

    print()
    print(f"{'PathA 典型(10w × 30 列)':<40} ≈ 1.6s")
    print(f"{'PathA 极限(60w × 30 列)':<40} ≈ 10s (但 60w 已分流到 PathB)")
    print(f"{'PathB 单 chunk(10w × 30 列)':<40} ≈ 1.6s × 50 chunks = 80s ❌")

    # ── 向量化方案 benchmark ──
    print()
    print("=" * 110)
    print("# 向量化方案(pandas str ops)— 性能对比")
    print("=" * 110)

    def classified_vectorized(s: pd.Series) -> dict:
        """pandas 向量化版本。"""
        sv = s.astype(str).str.strip()
        empty_mask = s.isna() | sv.eq("") | sv.str.lower().isin(["none", "null", "nan", "<na>"])
        placeholder_mask = sv.isin(_PLACEHOLDERS) & ~empty_mask
        pct_strip = sv.str.rstrip("%")
        pct_mask = sv.str.endswith("%") & pct_strip.str.match(_RE_NUMERIC) & ~empty_mask & ~placeholder_mask
        long_id_mask = sv.str.match(_RE_LONG_ID) & ~empty_mask & ~placeholder_mask & ~pct_mask
        date_mask = sv.str.match(_RE_DATE) & ~empty_mask & ~placeholder_mask & ~pct_mask & ~long_id_mask
        sv_clean = sv.str.lstrip("¥$￥").str.rstrip("%").str.replace(",", "", regex=False)
        numeric_mask = sv_clean.str.match(_RE_NUMERIC) & ~empty_mask & ~placeholder_mask & ~pct_mask & ~long_id_mask & ~date_mask
        text_mask = ~(empty_mask | placeholder_mask | pct_mask | long_id_mask | date_mask | numeric_mask)
        out = {}
        for name, m in [("empty", empty_mask), ("placeholder", placeholder_mask),
                         ("percentage", pct_mask), ("long_id", long_id_mask),
                         ("date", date_mask), ("numeric", numeric_mask), ("text", text_mask)]:
            v = int(m.sum())
            if v > 0:
                out[name] = v
        return out

    print(f"{'行数':>10}  {'全列循环(ms)':>14} {'向量化(ms)':>14} {'提速'}")
    print("-" * 60)
    for n_rows in [1000, 10_000, 100_000, 600_000]:
        series = make_profit_col_c(n_rows)
        t0 = time.perf_counter()
        classified_full(series)
        t_loop = (time.perf_counter() - t0) * 1000

        # warm up
        classified_vectorized(series)
        t0 = time.perf_counter()
        for _ in range(3):
            classified_vectorized(series)
        t_vec = (time.perf_counter() - t0) / 3 * 1000

        speedup = t_loop / max(t_vec, 0.001)
        print(f"{n_rows:>10,}  {t_loop:>14.3f} {t_vec:>14.3f}  {speedup:.0f}x")

    # 等价性验证
    print()
    print("# 向量化 vs 循环 等价性")
    print("-" * 60)
    s_test = make_profit_col_c(10000)
    full_loop = classified_full(s_test)
    full_vec = classified_vectorized(s_test)
    same = full_loop == full_vec
    print(f"  循环: {full_loop}")
    print(f"  向量: {full_vec}")
    print(f"  等价: {'✅' if same else '❌ 不一致!'}")


if __name__ == "__main__":
    main()
