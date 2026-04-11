"""时间事实校验中间件 (L4)。

扫描 LLM 最终输出中的"日期+星期"对，校验一致性并自动 patch。

设计原则（神经-符号分离的兜底层）：
- L1+L2+L3 已让 ERP 数据工具返回带正确 weekday 的时间块
- L4 拦截模型"违反逐字复述硬规则、仍然自己算 weekday"的残余幻觉
- 只 patch 明确的 weekday 错误（高置信度），不做日期数字重算（误判风险大）
- 检测到偏离时写 loguru 结构化日志 + tool_audit_log metadata

设计文档: docs/document/TECH_ERP时间准确性架构.md §14
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from loguru import logger

from utils.time_context import CN_TZ, RequestContext


# ────────────────────────────────────────────────────────────────────
# 正则：日期 + 星期
# ────────────────────────────────────────────────────────────────────

# 支持的日期格式（按优先级）：
#   - 2026-04-10 / 2026/04/10 / 2026.04.10 / 2026-04-10T13:05:00
#   - 2026年4月10日 / 2026年04月10日
#   - 4月10日（不含年份，用 ctx.now.year 补全）
# ISO 日期后的可选时间部分 (T13:05:00 或 ' 13:05') 被吸收进 date 内，
# 避免被 connector 的"禁数字"规则阻断。
_DATE_CORE = r"""
    (?:
        (?:(?P<y1>20\d{2})[-/.])?(?P<m1>\d{1,2})[-/.](?P<d1>\d{1,2})
        (?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?
      | (?:(?P<y2>20\d{2})年)?(?P<m2>\d{1,2})月(?P<d2>\d{1,2})日
    )
"""

# 支持的星期表达：周X / 星期X / 礼拜X
# "日" = "天"（都指周日）
# 精确匹配前缀，禁止 "期X" / "拜X" 这种错误切分
_WEEKDAY_CORE = r"(?:周|星期|礼拜)[一二三四五六日天]"

# 中间允许的"连接词"：不跨换行/分句，且不能包含日期分隔符
# 禁止字符集解释：
#   \n \r      — 跨行
#   ，。；！？;,.!?  — 跨分句
#   月日        — 会吃掉下一个日期（"4月3日...4月7日" 不能连成一个 match）
#   周星礼      — 会吃掉下一个星期词（防止星期在前日期在后时跨过另一个星期）
#   0-9        — 会吃掉另一个日期的数字开头
# 限制长度 15：连接词通常是 "（" / "是" / "的" / " " 等短字符
_CONNECTOR = r"[^\n\r，。；！？;,.!?0-9月日周星礼]{0,15}"

# 两个独立正则，扫两次后合并（避免组名冲突）：
# 模式 A: 日期 + 连接词 + 星期
_PATTERN_DATE_THEN_WD = re.compile(
    rf"""{_DATE_CORE}{_CONNECTOR}(?P<wd>{_WEEKDAY_CORE})""",
    re.VERBOSE,
)

# 模式 B: 星期 + 连接词 + 日期
_PATTERN_WD_THEN_DATE = re.compile(
    rf"""(?P<wd>{_WEEKDAY_CORE}){_CONNECTOR}{_DATE_CORE}""",
    re.VERBOSE,
)


# 跳过扫描的上下文标记（如"例如"、"假设"等）
_SKIP_CONTEXT_MARKERS = (
    "例如", "比如", "假设", "举例", "举个例子", "for example", "e.g.",
)

# 中文星期 → 0-6（周一=0, 周日=6）
_WEEKDAY_CN_MAP = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
}


# ────────────────────────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────────────────────────


@dataclass
class TemporalDeviation:
    """单个时间偏离记录。"""

    date_str: str              # 原始日期字符串 "2026-04-03"
    parsed_date: date          # 解析后的 date
    claimed_weekday: str       # 模型声称的 "周四"
    actual_weekday: str        # 正确的 "周五"
    snippet: str               # 原始片段 "4月3日（上周四）"
    snippet_start: int         # 在原文中的起始偏移
    snippet_end: int           # 终止偏移


# ────────────────────────────────────────────────────────────────────
# 解析 + 校验
# ────────────────────────────────────────────────────────────────────


def _parse_date(m: re.Match, default_year: int) -> Optional[date]:
    """从匹配中提取 date（年份缺失时用 default_year）。"""
    y = m.group("y1") or m.group("y2")
    mo = m.group("m1") or m.group("m2")
    d = m.group("d1") or m.group("d2")
    if not mo or not d:
        return None
    try:
        year = int(y) if y else default_year
        return date(year, int(mo), int(d))
    except (ValueError, TypeError):
        return None


def _normalize_weekday(wd_str: str) -> Optional[int]:
    """把 '周四' / '星期四' / '礼拜四' 统一解析为 0-6。"""
    last = wd_str[-1]
    return _WEEKDAY_CN_MAP.get(last)


def _is_in_skip_context(text: str, pos: int, window: int = 30) -> bool:
    """检查匹配点的前 N 个字符是否含"例如/假设"等标记。"""
    start = max(0, pos - window)
    before = text[start:pos]
    return any(marker in before for marker in _SKIP_CONTEXT_MARKERS)


def _is_in_code_block(text: str, pos: int) -> bool:
    """检查匹配点是否在 markdown 代码块内（```...```）。"""
    # 数一下 pos 之前有几个 ``` —— 奇数个说明在代码块内
    count = text[:pos].count("```")
    return count % 2 == 1


def _find_matches(text: str) -> list[re.Match]:
    """扫描 text 找所有日期+星期对，过滤代码块和 skip 上下文。

    关键：两个正则（DATE_THEN_WD / WD_THEN_DATE）可能在同一片段上产生
    **重叠匹配**（例：``4月3日（周四）和 4月7日（周三）`` 中，"周四"
    既是 4月3日 的星期，也可能被 WD_THEN_DATE 误识别为 4月7日 的星期）。
    为避免假阳性和 patch offset 冲突，按 start 排序后丢弃与前一个区间
    相交的匹配，仅保留首个非重叠匹配链。
    """
    raw: list[re.Match] = []

    for pattern in (_PATTERN_DATE_THEN_WD, _PATTERN_WD_THEN_DATE):
        for m in pattern.finditer(text):
            if _is_in_code_block(text, m.start()):
                continue
            if _is_in_skip_context(text, m.start()):
                continue
            raw.append(m)

    # 按起始位置排序
    raw.sort(key=lambda m: (m.start(), -m.end()))

    # 去重叠：保留首个，丢弃所有与之相交的后续匹配
    results: list[re.Match] = []
    last_end = -1
    for m in raw:
        if m.start() < last_end:
            continue  # 与前一个匹配相交，丢弃
        results.append(m)
        last_end = m.end()
    return results


def _extract_claimed_weekday(m: re.Match) -> Optional[str]:
    """从匹配中提取模型声称的星期字符串（如"周四"）。"""
    return m.group("wd")


_WEEKDAY_CHAR_BY_INDEX = ("一", "二", "三", "四", "五", "六", "日")


def _build_actual_weekday(claimed: str, actual_idx: int) -> str:
    """按 claimed 的前缀（周/星期/礼拜）构造正确的 actual 字符串，保持用户风格。

    例：
        claimed="星期二", actual_idx=0 → "星期一"
        claimed="礼拜六", actual_idx=4 → "礼拜五"
        claimed="周四", actual_idx=4 → "周五"
        claimed="周天", actual_idx=6 → "周日"（统一为"日"）
    """
    # 提取前缀（去掉最后一个字符）
    prefix = claimed[:-1]
    return f"{prefix}{_WEEKDAY_CHAR_BY_INDEX[actual_idx]}"


def _compute_deviation(
    m: re.Match, text: str, default_year: int,
) -> Optional[TemporalDeviation]:
    """对一个匹配做一致性校验，返回偏离对象（无偏离返回 None）。"""
    parsed = _parse_date(m, default_year=default_year)
    if parsed is None:
        return None
    claimed = _extract_claimed_weekday(m)
    if claimed is None:
        return None
    claimed_idx = _normalize_weekday(claimed)
    if claimed_idx is None:
        return None
    actual_idx = parsed.weekday()
    if claimed_idx == actual_idx:
        return None  # 一致，无偏离

    # 保持 claimed 的前缀风格构造 actual
    actual_str = _build_actual_weekday(claimed, actual_idx)

    return TemporalDeviation(
        date_str=parsed.strftime("%Y-%m-%d"),
        parsed_date=parsed,
        claimed_weekday=claimed,
        actual_weekday=actual_str,
        snippet=text[m.start():m.end()],
        snippet_start=m.start(),
        snippet_end=m.end(),
    )


# ────────────────────────────────────────────────────────────────────
# 主入口：校验 + patch
# ────────────────────────────────────────────────────────────────────


def validate_and_patch(
    text: str,
    *,
    ctx: Optional[RequestContext] = None,
    default_year: Optional[int] = None,
) -> tuple[str, list[TemporalDeviation]]:
    """扫描文本中的"日期+星期"对，校验并自动 patch 错误的星期。

    Args:
        text: 模型合成的最终文本
        ctx: RequestContext（用于获取 default_year）
        default_year: 日期没有年份时使用的默认年，None 时从 ctx.now.year 取

    Returns:
        (patched_text, deviations)
        - patched_text: 修正后的文本（weekday 错误已替换）
        - deviations: 发现的偏离清单（供 L5 写日志）

    Raises:
        TypeError: 当 text 不是 str 类型时（防御 None 等意外输入）。
    """
    # 防御：非 str 类型（尤其是 None）应明确拒绝，不能 silently 传递回 None
    if text is None:
        return "", []
    if not isinstance(text, str):
        raise TypeError(
            f"validate_and_patch 要求 text: str，收到 {type(text).__name__}"
        )
    if not text:
        return text, []

    if default_year is None:
        if ctx is not None:
            default_year = ctx.now.year
        else:
            default_year = datetime.now(CN_TZ).year

    matches = _find_matches(text)
    if not matches:
        return text, []

    deviations: list[TemporalDeviation] = []
    # 从后往前 patch，避免 offset 漂移
    patched = text
    for m in reversed(matches):
        dev = _compute_deviation(m, text, default_year=default_year)
        if dev is None:
            continue
        deviations.append(dev)
        # 替换声称的 weekday → 实际 weekday
        snippet_patched = dev.snippet.replace(
            dev.claimed_weekday, dev.actual_weekday,
        )
        patched = (
            patched[:dev.snippet_start]
            + snippet_patched
            + patched[dev.snippet_end:]
        )

    deviations.reverse()  # 按原文顺序返回

    if deviations:
        logger.warning(
            f"[L4] TemporalValidator patched {len(deviations)} weekday deviation(s) | "
            f"first={deviations[0].date_str} claimed={deviations[0].claimed_weekday} "
            f"actual={deviations[0].actual_weekday}"
        )

    return patched, deviations
