"""
三层输入归一化架构（对齐 Claude Code sanitization.ts + z.preprocess + validateInput）

数据流：
  LLM 输出参数
    → L1 InputNormalizer.normalize()    文本归一化（NFKC + 不可见字符 + strip）
    → L2 MultiValueParser.parse()       多值拆分（分隔符 + 去重 + 上限截断）
    → L3 ValueValidator.validate_*()    格式校验（正则 + 枚举映射）

设计原则：
  - 每层职责单一，可独立调用
  - L1 对所有字段生效，L2 对 eq 字段生效，L3 按需调用
  - 无状态纯函数，线程安全
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Union

from loguru import logger


# ============================================================
# L1: InputNormalizer — 文本归一化
# ============================================================

# 不可见 / 零宽字符（NFKC 不处理的部分）
_INVISIBLE_RE = re.compile(
    "["
    "\u200b"      # zero-width space
    "\u200c"      # zero-width non-joiner
    "\u200d"      # zero-width joiner
    "\u200e"      # left-to-right mark
    "\u200f"      # right-to-left mark
    "\u202a-\u202e"  # bidi embedding/override
    "\u2060"      # word joiner
    "\u2066-\u2069"  # bidi isolate
    "\ufeff"      # BOM / zero-width no-break space
    "\ufff9-\ufffb"  # interlinear annotation
    "]"
)


class InputNormalizer:
    """L1: 所有用户输入的第一道关——文本级归一化

    对齐 Claude Code sanitization.ts 的 NFKC + 危险字符移除。
    NFKC 自动完成全角→半角转换：
      ０１２ → 012, ＡＢＣ → ABC, ，→,, ；→;, ～→~
    """

    @staticmethod
    def normalize(val: Any) -> str | None:
        """归一化单个值，返回清洗后的字符串或 None"""
        if val is None:
            return None
        if not isinstance(val, str):
            val = str(val)
        # NFKC：全角→半角 + 兼容分解
        val = unicodedata.normalize("NFKC", val)
        # 移除不可见字符
        val = _INVISIBLE_RE.sub("", val)
        # strip
        val = val.strip()
        return val or None


# ============================================================
# L2: MultiValueParser — 多值解析
# ============================================================

# NFKC 后中文标点已转半角，只需处理半角分隔符
_SEPARATORS = (",", ";", "\n", "|")

# IN 查询安全上限（PostgREST / Supabase）
DEFAULT_MAX_IN = 200


class MultiValueParser:
    """L2: 单值 / 多值统一解析 + 去重 + 上限截断"""

    @classmethod
    def parse(
        cls,
        val: Any,
        max_values: int = DEFAULT_MAX_IN,
    ) -> str | list[str] | None:
        """解析用户输入为单值 str 或多值 list[str]

        支持格式：
          None / ""           → None
          "ABC"               → "ABC"
          "A,B,C"             → ["A","B","C"]
          "A;B\\nC|D"         → ["A","B","C","D"]
          ["A","B"]           → ["A","B"]
          ["A"]               → "A"
        """
        # list 输入：逐个 L1 归一化
        if isinstance(val, list):
            items = []
            seen: set[str] = set()
            for v in val:
                n = InputNormalizer.normalize(v)
                if n and n not in seen:
                    seen.add(n)
                    items.append(n)
            return cls._apply_limit(items, max_values)

        # str 输入：先 L1 归一化
        normalized = InputNormalizer.normalize(val)
        if normalized is None:
            return None

        # 检测分隔符
        has_sep = any(sep in normalized for sep in _SEPARATORS)
        if not has_sep:
            return normalized

        # 统一替换为逗号后拆分
        tmp = normalized
        for sep in _SEPARATORS:
            tmp = tmp.replace(sep, ",")
        items = []
        seen_set: set[str] = set()
        for part in tmp.split(","):
            part = part.strip()
            if part and part not in seen_set:
                seen_set.add(part)
                items.append(part)

        return cls._apply_limit(items, max_values)

    @staticmethod
    def to_filter(field: str, val: Union[str, list[str]]) -> dict:
        """单值→eq, 多值→in"""
        if isinstance(val, list):
            return {"field": field, "op": "in", "value": val}
        return {"field": field, "op": "eq", "value": val}

    @staticmethod
    def _apply_limit(
        items: list[str], max_values: int,
    ) -> str | list[str] | None:
        """去空 + 单值退化 + 超限截断"""
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        if len(items) > max_values:
            logger.warning(
                f"IN 值超限截断 | 原始={len(items)} | 上限={max_values}"
            )
            items = items[:max_values]
        return items


# ============================================================
# L3: ValueValidator — 值校验
# ============================================================


class ValueValidator:
    """L3: 业务级值校验——格式正则 + 枚举映射

    正则从 plan_fill.py 实际数据验证得出，覆盖所有平台格式。

    _CORE_PATTERNS: 核心正则（不带锚点），single source of truth
    PATTERNS:       校验用（自动加 ^$），validate_format 使用
    SEARCH_PATTERNS: 搜索用（原样），plan_fill 从文本中提取候选值使用
    """

    # ── 核心正则（不带锚点）── single source of truth
    _CORE_PATTERNS: dict[str, tuple[str, int]] = {
        # (pattern_str, flags)
        # 商品编码：字母开头 + 字母数字 + 可选 -后缀
        "product_code": (r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*", 0),
        # 订单号：tb=18位 / jd|sid=16位 / fxg|1688=19位 / xhs=P+18位 / pdd=日期-数字
        "order_no": (r"(?:P\d{18}|\d{16,19}|\d{8}-\d+)", 0),
        # 系统单号：精确 16 位数字
        "system_id": (r"\d{16}", 0),
        # 快递单号：承运商前缀 + 8-20 位数字
        "express_no": (
            r"(?:SF|YT|ZTO|YD|STO|BEST|JD|EMS|YZPY|JDVA|DBL|YUNDA)\d{8,20}",
            re.IGNORECASE,
        ),
        # 单据编号：前缀(DB/AS/RX/RF/PO) + 日期序号
        "doc_code": (r"(?:DB|AS|RX|RF|PO)\d{8,}", 0),
    }

    # 校验用（加 ^$）
    PATTERNS: dict[str, re.Pattern[str]] = {
        k: re.compile(f"^{p}$", flags)
        for k, (p, flags) in _CORE_PATTERNS.items()
    }

    # 搜索用（不加 ^$，从自然语言文本中提取候选值）
    SEARCH_PATTERNS: dict[str, re.Pattern[str]] = {
        k: re.compile(p, flags)
        for k, (p, flags) in _CORE_PATTERNS.items()
    }

    @classmethod
    def validate_format(
        cls, field: str, values: Union[str, list[str]],
    ) -> tuple[list[str], list[str]]:
        """校验值格式，返回 (valid, invalid)

        无对应正则的字段，全部视为合法（不做格式限制）。
        """
        pattern = cls.PATTERNS.get(field)
        if pattern is None:
            # 无正则规则 → 全部合法
            if isinstance(values, str):
                return [values], []
            return list(values), []

        if isinstance(values, str):
            values = [values]

        valid, invalid = [], []
        for v in values:
            if pattern.match(v):
                valid.append(v)
            else:
                invalid.append(v)
        return valid, invalid

    @staticmethod
    def validate_enum(
        val: str, enum_map: dict[str, Any],
    ) -> Any | None:
        """枚举映射：中文/别名 → DB 值，无效返回 None

        先尝试精确匹配，再尝试 L1 归一化后匹配。
        """
        if val in enum_map:
            return enum_map[val]
        # 归一化后重试（处理全角等）
        normalized = InputNormalizer.normalize(val)
        if normalized and normalized in enum_map:
            return enum_map[normalized]
        return None
