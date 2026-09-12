"""Extract execution requirements without letting a rewrite change their scope.

The parser classifies verbatim source spans. Unclassified source stays in the
execution requirements; only independently grounded metadata may be excluded.
Uncertain segmentation requires an editable form instead of direct submission.
"""
import re
from typing import Any, Mapping


_PUNCTUATION = " \t\r\n，,。.;；：:"
_SCHEDULE_FIELDS = ("schedule_type", "time_str", "run_at", "weekdays", "day_of_month")
_DELIVERY_ENDING = r"(?:看一下|看看|看|查收)"


def _ordered_source_parts(text: str, parts: list[dict]) -> list[dict] | None:
    # Models may reorder classified parts or move separator punctuation. Locate
    # each span in the source and prohibit overlaps. Fill any model omissions
    # from the original, so business wording/order stays authoritative.
    offsets = [i for i, char in enumerate(text) if char not in _PUNCTUATION]
    source = "".join(text[i] for i in offsets)
    used = [False] * len(source)
    ordered = []
    for part in parts:
        quote = "".join(char for char in part["text"] if char not in _PUNCTUATION)
        if not quote:
            return None
        start = source.find(quote)
        while start >= 0 and any(used[start:start + len(quote)]):
            start = source.find(quote, start + 1)
        if start < 0:
            return None
        end = start + len(quote)
        if part.get("kind") in {"schedule", "delivery"} and end < len(offsets):
            suffix = text[offsets[end - 1] + 1:]
            # Recover only grammatical endings omitted from a metadata span.
            # Longer clauses (e.g. 看退款率) remain execution requirements.
            if part["kind"] == "schedule":
                clock_ending = r"钟?" if quote.endswith(("点", "时")) else ""
                pattern = clock_ending + r"(?:(?:执行|运行)(?=[\s，,。.;；：:]|$))?"
            else:
                pattern = _DELIVERY_ENDING + r"(?=[\s，,。.;；：:]|$)"
            ending = re.match(pattern, suffix)
            if ending and ending[0] and not any(used[end:end + len(ending[0])]):
                end += len(ending[0])
        used[start:end] = [True] * (end - start)
        ordered.append((start, {**part, "text": text[offsets[start]:offsets[end - 1] + 1]}))
    index = 0
    while index < len(used):
        if used[index]:
            index += 1
            continue
        start = index
        while index < len(used) and not used[index]:
            index += 1
        ordered.append((start, {"kind": "execution", "text": text[offsets[start]:offsets[index - 1] + 1]}))
    return [part for _, part in sorted(ordered, key=lambda item: item[0])]


def _request_pattern(name: Any) -> str:
    target = r"(?:(?:定时|计划|自动)?任务)"
    if name:
        target += "|" + re.escape(str(name))
    return (r"(?:(?:请|帮我|麻烦|给我|我想|我要|想要|需要)\s*)*"
            r"(?:创建|新建|设置|设定|添加|建立|安排)(?:一(?:个|项|条|份))?"
            rf"(?:{target})")


def execution_content(text: str, raw: Mapping[str, Any], accepted: Mapping[str, Any]) -> str | None:
    parts = raw.get("request_parts")
    if not isinstance(parts, list) or not parts:
        return None
    if any(not isinstance(part, dict) or not isinstance(part.get("text"), str)
           or not part["text"] for part in parts):
        return None
    parts = _ordered_source_parts(text.strip(), parts)
    if parts is None:
        return None
    evidence = raw.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    content = []
    for part in parts:
        kind, source = part.get("kind"), part["text"]
        if kind == "execution":
            if re.match(_request_pattern(accepted.get("name")) + r"(?=[\s，,。.;；：:]|$)", source.strip()):
                return None
            content.append(source.strip(_PUNCTUATION))
        elif kind == "request":
            # A task-management instruction can be omitted; arbitrary business
            # requirements cannot disappear under the parser's classification.
            if not re.fullmatch(_request_pattern(accepted.get("name")), source.strip(_PUNCTUATION)):
                return None
        elif kind == "schedule":
            remaining = source.strip(_PUNCTUATION)
            quotes = sorted({evidence[key] for key in _SCHEDULE_FIELDS
                             if key in accepted and isinstance(evidence.get(key), str)
                             and evidence[key].strip()}, key=len, reverse=True)
            used = False
            for quote in quotes:
                # Schedule evidence must itself be calendar/clock language;
                # quoting a business instruction is not authority to remove it.
                if not re.fullmatch(r"[\d零〇一二两三四五六七八九十百每周星期天日月年号早上午中下午晚凌晨间傍点时钟分半刻整和及、:：/\-T+Z\s]+", quote):
                    continue
                if quote in remaining:
                    # A clock quote may omit 钟 in 八点钟. This suffix is
                    # removable only directly after its grounded clock quote.
                    pattern = re.escape(quote) + (r"钟?" if quote.endswith(("点", "时")) else "")
                    remaining = re.sub(pattern, "", remaining, count=1)
                    used = True
            if not used or not re.fullmatch(r"[\s，,。.;；：:]*?(?:(?:在|于|北京时间|执行|运行|一次|定时|自动)[\s，,。.;；：:]*)*", remaining):
                return None
        elif kind == "delivery":
            source = source.strip(_PUNCTUATION)
            recipient = raw.get("recipient")
            if not isinstance(recipient, str) or not recipient or recipient not in source:
                return None
            delivery = re.search(
                r"(?:并|然后)?(?:将|把)?(?:结果|报告|报表)?"
                r"(?:发送|推送|通知|发|送|给)(?:给|到)?\s*" + re.escape(recipient)
                + rf"(?:{_DELIVERY_ENDING})?$",
                source,
            )
            if not delivery:
                return None
            prefix = source[:delivery.start()].strip(_PUNCTUATION)
            if prefix:
                content.append(prefix)  # e.g. output-format requirements
        else:
            return None
    result = "；".join(value for value in content if value)
    return result or None
