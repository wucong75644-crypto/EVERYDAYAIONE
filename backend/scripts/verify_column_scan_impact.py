"""验证 classified_dist 从 13 样本改全列扫描的影响。

回答 3 个核心问题:
  Q1. 13 样本 vs 全列 — 真实差距有多大?(精度)
  Q2. 全列扫的性能成本能不能扛?(性能)
  Q3. 真实生产里 "罕见型" 单元格(% / placeholder)能不能被 13 样本抓到?(漏检率)
"""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from services.agent.data_query_cache import _classify_cell


def classified_13_sample(series: pd.Series) -> dict:
    """复刻当前 _scan_columns 的 13 样本算法。"""
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
        cls = _classify_cell(series.iat[ridx])
        classified[cls] = classified.get(cls, 0) + 1
    return classified


def classified_full(series: pd.Series) -> dict:
    """全列扫。"""
    classified: dict[str, int] = {}
    for v in series.values:
        cls = _classify_cell(v)
        classified[cls] = classified.get(cls, 0) + 1
    return classified


# ── Q1+Q3: 真实生产场景模拟 ──

def make_profit_col_c(n_rows: int) -> pd.Series:
    """模拟「利润表 合计列 C」: 99% 金额 + 1% 百分比 ragged。"""
    rng = random.Random(42)
    vals = []
    for _ in range(n_rows):
        if rng.random() < 0.01:  # 1% 是 %
            vals.append(f"{rng.uniform(0.1, 99.9):.2f}%")
        else:
            vals.append(round(rng.uniform(100, 99999), 2))
    return pd.Series(vals)


def make_normal_amount_col(n_rows: int) -> pd.Series:
    """模拟正常金额列(无 %)。"""
    rng = random.Random(43)
    return pd.Series([round(rng.uniform(100, 99999), 2) for _ in range(n_rows)])


def make_placeholder_col(n_rows: int, ratio: float = 0.05) -> pd.Series:
    """模拟含 '-' / 'N/A' 占位符的列。"""
    rng = random.Random(44)
    vals = []
    placeholders = ["-", "—", "N/A", "无"]
    for _ in range(n_rows):
        if rng.random() < ratio:
            vals.append(rng.choice(placeholders))
        else:
            vals.append(round(rng.uniform(100, 99999), 2))
    return pd.Series(vals)


def make_text_col(n_rows: int) -> pd.Series:
    """普通文本列(科目名/客户名)。"""
    rng = random.Random(45)
    names = ["销售收入", "采购成本", "管理费用", "财务费用", "其他业务收入"]
    return pd.Series([rng.choice(names) for _ in range(n_rows)])


def main():
    print("=" * 100)
    print("# Q1+Q3: 13 样本 vs 全列扫 — 真实场景对比(漏检率)")
    print("=" * 100)

    cases = [
        ("利润表 C 列(99% 金额 + 1% 百分比)", make_profit_col_c, 154),  # 复刻真实文件
        ("利润表 C 列(99% 金额 + 1% 百分比)", make_profit_col_c, 1000),
        ("利润表 C 列(99% 金额 + 1% 百分比)", make_profit_col_c, 10000),
        ("含占位符列(5% '-' / 'N/A')", lambda n: make_placeholder_col(n, 0.05), 1000),
        ("含占位符列(1% '-' / 'N/A')", lambda n: make_placeholder_col(n, 0.01), 1000),
        ("正常金额列(无杂质)", make_normal_amount_col, 1000),
        ("文本列(科目名)", make_text_col, 1000),
    ]

    print(f"{'场景':<45} {'n':>6}  {'13 样本':<30} {'全列':<30}  {'漏检 %'}")
    print("-" * 100)
    for desc, maker, n in cases:
        series = maker(n)
        c13 = classified_13_sample(series)
        cfull = classified_full(series)

        # 13 样本里是否能看到 % 之类的罕见类
        rare_types = set(cfull.keys()) - {"empty", "numeric", "text"}
        missed = rare_types - set(c13.keys())
        miss_str = ",".join(sorted(missed)) if missed else "✅ 无"
        print(f"{desc:<45} {n:>6}  {str(c13):<30} {str(cfull):<30}  {miss_str}")

    # ── Q2: 性能 ──
    print()
    print("=" * 100)
    print("# Q2: 全列扫性能 benchmark")
    print("=" * 100)
    print(f"{'场景':<35} {'行数':>10}  {'13 样本(ms)':>14} {'全列(ms)':>14} {'倍数'}")
    print("-" * 100)

    for n_rows in [1000, 10_000, 100_000, 600_000]:
        series = make_profit_col_c(n_rows)

        t0 = time.perf_counter()
        for _ in range(10):
            classified_13_sample(series)
        t13 = (time.perf_counter() - t0) / 10 * 1000

        t0 = time.perf_counter()
        classified_full(series)
        tfull = (time.perf_counter() - t0) * 1000

        ratio = tfull / max(t13, 0.001)
        print(f"{'单列(混合金额+%)':<35} {n_rows:>10,}  "
              f"{t13:>14.3f} {tfull:>14.3f} {ratio:>6.0f}x")

    # 多列估算
    print()
    print("# 多列估算(单列时间 × 列数)")
    print(f"{'文件规模':<35} {'估算总耗时'}")
    print("-" * 60)
    series_60w = make_profit_col_c(600_000)
    t0 = time.perf_counter()
    classified_full(series_60w)
    t_per_col_60w = (time.perf_counter() - t0)
    series_10w = make_profit_col_c(100_000)
    t0 = time.perf_counter()
    classified_full(series_10w)
    t_per_col_10w = (time.perf_counter() - t0)

    print(f"{'10w 行 × 30 列(PathA 典型)':<35} {t_per_col_10w * 30:.2f}s")
    print(f"{'60w 行 × 30 列(PathA 极限)':<35} {t_per_col_60w * 30:.2f}s")
    print(f"{'60w 行 × 10 列':<35} {t_per_col_60w * 10:.2f}s")


if __name__ == "__main__":
    main()
