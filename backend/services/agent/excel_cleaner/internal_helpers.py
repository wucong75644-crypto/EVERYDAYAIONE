"""excel_cleaner 内部清洗工具(私有,不暴露给沙盒)。

V3.3: 清洗职责完全归属 cleaning 层 — safe_float / safe_int 从沙盒注入
回归 cleaning 内部,生成 {col}_num 干净列,LLM 拿到的就是 float,不需要现场清洗。

设计:
  对齐 dataprep.clean / pyjanitor / Trifacta 的"universal action"模式
  一个 safe_float 覆盖 90% 字符串→数字场景(%/¥/$/, /占位符/None)
"""
from __future__ import annotations
from typing import Any

# 占位符值集合(中文 + 英文 + Excel 常见占位符)
_PLACEHOLDERS = frozenset({
    "", "-", "—", "─", "──", "——", "/",
    "N/A", "NA", "na", "n/a", "NaN", "null", "None", "nan", "<NA>",
    "无", "空", "尚未", "未知",
})


def safe_float(v: Any, default: float = 0.0) -> float:
    """字符串/Mixed → float 的 universal 清洗。

    支持:
      - None / NaN → default
      - int / float → float(v)
      - '47.40%' → 0.474(百分比转小数,保留精度)
      - '-' / '—' / '无' / 'N/A' 等占位符 → default
      - '1,234.56' / '1,234,567' → 1234.56 / 1234567(千分位剥离)
      - '¥99.99' / '$99.99' / '￥99.99' → 99.99(货币符号剥离)
      - 中文逗号/中文空格也剥离
      - 解析失败 → default(异常路径兜底)
    """
    import pandas as pd
    if v is None:
        return default
    if isinstance(v, float) and pd.isna(v):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s in _PLACEHOLDERS:
        return default
    # 百分比 - 保留小数精度(47.40% → 0.474)
    if s.endswith("%"):
        try:
            return float(s.rstrip("%")) / 100
        except ValueError:
            return default
    # 剥离千分位/货币符号/中文符号/全角空格
    s = (
        s.replace(",", "")
        .replace("，", "")
        .replace("¥", "")
        .replace("$", "")
        .replace("￥", "")
        .replace(" ", "")
        .replace("　", "")
    )
    try:
        return float(s)
    except ValueError:
        return default


def smart_parse_date(series):
    """字符串/Excel serial → datetime 的 universal 清洗。

    支持:
      - ISO 格式: 2026-06-09 / 2026/6/9
      - 中文格式: 2026年6月9日
      - Excel serial: 40000-60000 区间整数 → 对应日期
      - 失败 → NaT
    """
    import pandas as pd
    # 1. 尝试 Excel serial(40000-60000 区间整数,对应 ~2009-2064)
    try:
        numeric = pd.to_numeric(series, errors="coerce")
        # 如果列里大部分是 Excel serial 范围的整数,按 Excel serial 解析
        in_range = numeric.between(40000, 60000)
        if in_range.sum() > 0.5 * numeric.notna().sum() and numeric.notna().sum() > 0:
            # Excel epoch: 1899-12-30
            return pd.to_datetime(numeric, unit="D", origin="1899-12-30", errors="coerce")
    except Exception:
        pass
    # 2. 通用日期解析(支持多格式)
    try:
        return pd.to_datetime(series, errors="coerce", format="mixed")
    except Exception:
        return pd.to_datetime(series, errors="coerce")


def make_unique_col_name(existing_cols, base_name: str) -> str:
    """命名冲突保护 — 如果 base_name 已存在,加 _1 / _2 后缀。"""
    if base_name not in existing_cols:
        return base_name
    i = 1
    while f"{base_name}_{i}" in existing_cols:
        i += 1
    return f"{base_name}_{i}"
