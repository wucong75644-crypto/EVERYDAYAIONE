"""沙盒数据处理 helpers — 注入 globals 供 LLM 直接调用。

设计:
  对齐 OpenAI Code Interpreter ace_tools 模式 — sandbox 内置 helper 函数,
  让 LLM 用一行代码处理 Excel 财务报表的混合类型场景(ragged data:
  金额 + 百分比 + 中文占位符 + 公式错误值)。

LLM 通过 file_analyze 返回的 data_quality_notes warning 看到引导,
通过 code_execute description 的 DATA HELPERS 段知道 helper 存在,
然后在沙盒里直接调用,保留数据精度。
"""
from __future__ import annotations
from typing import Any


# 占位符值集合(中文 + 英文 + Excel 常见占位符)
# safe_float 进入此集合的值直接返回 default,不抛异常
_PLACEHOLDERS = frozenset({
    "", "-", "—", "─", "──", "——", "/",
    "N/A", "NA", "na", "n/a", "NaN", "null", "None", "nan", "<NA>",
    "无", "空", "尚未", "未知",
})


def safe_float(v: Any, default: float = 0.0) -> float:
    """处理 Excel 财务报表混合类型,保留数据精度。

    支持:
      - None / NaN → default
      - int / float → float(v)
      - '47.40%' → 0.474 (百分比转小数,保留精度)
      - '-' / '—' / '无' / 'N/A' 等占位符 → default
      - '1,234.56' / '1,234,567' → 1234.56 / 1234567 (千分位剥离)
      - '¥99.99' / '$99.99' / '￥99.99' → 99.99 (货币符号剥离)
      - 中文逗号/中文空格也剥离
      - 解析失败 → default (异常路径兜底)
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


def safe_int(v: Any, default: int = 0) -> int:
    """safe_float + 取整, 处理混合类型转 int 场景。"""
    return int(safe_float(v, float(default)))


def install_data_helpers_in_globals(g: dict) -> None:
    """注入 safe_float / safe_int 到沙盒 globals。

    对应 code_tools.py 里 DATA HELPERS 段的声明,LLM 在沙盒里可直接调用。
    """
    g["safe_float"] = safe_float
    g["safe_int"] = safe_int
