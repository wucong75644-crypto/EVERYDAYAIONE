"""Decode completed planner replies without changing any execution text."""
from __future__ import annotations

import json
import re

from .contracts import text_hash

DELIVERY_VERSION = "ecom-design.v3"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("PLANNER_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("PLANNER_NON_JSON_CONSTANT")


def _advisory_tail(tail: str) -> bool:
    # Only a narrow, bounded continuation menu is compatible. Never select a
    # JSON candidate from prose, discard a contradictory verdict or fix syntax.
    if len(tail) > 512 or len(tail.splitlines()) > 12:
        return False
    if re.search(r"[{}\[\]`]|status|questions|images|review_records|ready|needs_input|blocked|failed|error|失败|错误|不通过|未通过|不合格|不可执行|未完成|需修复|信息不足|停止|取消|拒绝|不要生成|重新生成|替换|切换|改为|调用", tail, re.I):
        return False
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    if not lines or not re.fullmatch(r"(?:继续完善(?:这组|本组)?(?:商品)?主图|(?:后续|可选)建议[：:]?)", lines[0]):
        return False
    return all(re.fullmatch(r"[-*•]\s*(?:优化|挑选|比较|查看)[^\n]+", line) for line in lines[1:])


def decode_delivery(text: str):
    """Called only after the Gateway reports a normally completed response."""
    body = text.lstrip()
    if not body.startswith("{"):
        raise ValueError("PLANNER_JSON_OBJECT_REQUIRED")
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    value, end = decoder.raw_decode(body)
    tail = body[end:].strip()
    if tail and not _advisory_tail(tail):
        raise ValueError("PLANNER_AMBIGUOUS_OUTPUT_TAIL")
    return value, {"delivery_version": DELIVERY_VERSION, "response_sha256": text_hash(text),
        "json_sha256": text_hash(body[:end]), "trailing_characters": len(tail),
        "normalization": "advisory_tail" if tail else "none"}


def render_ready_plan(row):
    """Render persisted native items verbatim; never ask the model to resave them."""
    items=sorted(row.get('items') or [],key=lambda item:item['position'])
    if row.get('status')!='ready' or not items or [item['position'] for item in items]!=list(range(1,len(items)+1)):
        raise ValueError('PLANNER_DELIVERY_NOT_READY')
    sections=[]
    for item in items:
        text=item['request_text']
        if not isinstance(text,str) or text_hash(text)!=item['request_text_sha256']:
            raise ValueError('PLANNER_DELIVERY_CONTENT_CHANGED')
        sections.append(f"## {item['position']}. {item['name']}\n\n"+text)
    return '\n\n'.join(sections)
