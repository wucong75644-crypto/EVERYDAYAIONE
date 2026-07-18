"""与业务无关的最终回答数值声明提取。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from services.agent.runtime.evidence_guard.models import NumericClaim


_NUMBER = re.compile(
    r"(?<![\d.])"
    r"(?P<currency>[¥￥$])?"
    r"(?P<number>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?P<percent>%|％)?"
    r"(?P<unit>单|笔|件|个|组|元|万元|亿元|天|小时|分钟|秒)?"
    r"(?![\d.])"
)


def extract_numeric_claims(text: str) -> tuple[NumericClaim, ...]:
    claims: list[NumericClaim] = []
    for match in _NUMBER.finditer(text):
        if _is_ordinal(text, match.start(), match.end()):
            continue
        value = _decimal(match.group("number"))
        if value is None:
            continue
        percent = match.group("percent")
        unit = percent or match.group("unit") or match.group("currency")
        claims.append(
            NumericClaim(
                raw=match.group(0),
                value=value,
                unit=unit,
                context=text[max(0, match.start() - 24):match.end() + 24],
                start=match.start(),
                end=match.end(),
            )
        )
    return tuple(claims)


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _is_ordinal(text: str, start: int, end: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    if text[line_start:start].strip():
        return False
    suffix = text[end:end + 2]
    return bool(suffix) and suffix[0] in ".)、" and (
        len(suffix) == 1 or suffix[1].isspace()
    )
