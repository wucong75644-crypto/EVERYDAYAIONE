"""Validate explicit clock/calendar values against their user-source evidence.

This is a consistency check on the existing parser, not a second NL scheduler.
Unrecognized clock wording requires form completion rather than a guessed time.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

_NUMBER = r"[0-9零〇一二两三四五六七八九十]+"
_CLOCK = re.compile(
    rf"(?P<period>凌晨|早上|早晨|上午|中午|下午|晚上|晚间|傍晚)?\s*"
    rf"(?P<hour>{_NUMBER})(?:[点时](?:(?P<half>半)|(?P<quarter>[一三])刻|(?P<minute>{_NUMBER})分?)?|[:：](?P<colon>\d{{2}}))"
)


def _number(value: str) -> int:
    if value.isascii() and value.isdigit():
        return int(value)
    digits = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
              '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    if '十' in value:
        tens, units = value.split('十')
        return (digits[tens] if tens else 1) * 10 + (digits[units] if units else 0)
    return int(''.join(str(digits[c]) for c in value))


def _clock_values(source: str) -> set[str]:
    values = set()
    for match in _CLOCK.finditer(source):
        try:
            hour = _number(match['hour'])
            minute = (30 if match['half'] else 15 if match['quarter'] == '一'
                      else 45 if match['quarter'] == '三' else _number(match['minute'] or match['colon'] or '0'))
            period = match['period']
            if period in {'下午', '晚上', '晚间', '傍晚', '中午'} and 1 <= hour < 12:
                hour += 12
            if period in {'凌晨', '早上', '早晨', '上午'} and hour == 12:
                hour = 0
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                continue
            values.add(f'{hour:02d}:{minute:02d}')
        except (ValueError, KeyError):
            continue
    return values


def _clock_source(text: str, quote: str) -> str:
    # Keep a period modifier immediately before a shortened quote: evidence
    # "九点" in "下午九点" must not authorize 09:00.
    sources = []
    for match in re.finditer(re.escape(quote), text):
        start, end = match.span()
        # Expand shortened evidence to the full overlapping clock expression,
        # including both period and minutes (e.g. 九点 in 下午九点半).
        for clock in _CLOCK.finditer(text):
            if clock.start() < end and clock.end() > start:
                start, end = min(start, clock.start()), max(end, clock.end())
        sources.append(text[start:end])
    return '\n'.join(sources)


def inconsistent_schedule_fields(text: str, changes: dict, evidence: dict, tz: str) -> set[str]:
    invalid = set()
    if 'time_str' in changes:
        source = _clock_source(text, evidence.get('time_str', ''))
        if _clock_values(source) != {changes['time_str']}:
            invalid.add('time_str')
    if 'run_at' in changes:
        quote = evidence.get('run_at', '')
        try:
            date = datetime.fromisoformat(str(changes['run_at']).replace('Z', '+00:00'))
            if date.tzinfo is None:
                raise ValueError('timezone missing')
            date = date.astimezone(ZoneInfo(tz))
            clocks = _clock_values(_clock_source(text, quote))
            if clocks != {date.strftime('%H:%M')}:
                invalid.add('run_at')
            explicit = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})[日号]', quote)
            if explicit and tuple(map(int, explicit.groups())) != (date.year, date.month, date.day):
                invalid.add('run_at')
        except (TypeError, ValueError):
            invalid.add('run_at')
    # Validate the simple explicit frequency cases, while retaining existing
    # interpretation of other calendar phrases (e.g. a specific one-shot date).
    if 'schedule_type' in changes:
        quote = evidence.get('schedule_type', '')
        explicit = next((kind for phrase, kind in [('每天', 'daily'), ('每日', 'daily'),
                         ('每周', 'weekly'), ('每星期', 'weekly'), ('每月', 'monthly')]
                         if phrase in quote), None)
        if explicit and changes['schedule_type'] != explicit:
            invalid.add('schedule_type')
    return invalid
