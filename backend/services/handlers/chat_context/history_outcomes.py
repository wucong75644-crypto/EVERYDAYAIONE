"""Completed-turn outcomes projected from existing blocks, independently of text.

This is a model-history view, not a new persisted format or a source of resource
authorization. Never replay old tool arguments/code or infer mutable form state.
"""
from __future__ import annotations

import json
import hashlib
from typing import Any

from .content_extractors import extract_text_from_content

_OUTCOMES_MARKER = "[历史交付与工具状态：仅记录已发生的事实]"


def archived_outcome_content(content: Any) -> str | None:
    """Retain deterministic delivery facts when the prose is budgeted away."""
    if not isinstance(content, str):
        return None
    if content.startswith(_OUTCOMES_MARKER + "\n"):
        facts = content
    elif "\n" + _OUTCOMES_MARKER + "\n" in content:
        facts = _OUTCOMES_MARKER + content.rsplit(_OUTCOMES_MARKER, 1)[1]
    else:
        return None
    return "[已归档]\n" + facts


def content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            return []
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _fields(block: dict, *names: str) -> dict:
    return {name: block[name] for name in names
            if isinstance(block.get(name), str) and block[name]}


def completed_tool_names(content: Any) -> set[str]:
    return {b.get("tool_name", "") for b in content_blocks(content)
            if b.get("type") == "tool_step" and b.get("status") in {"completed", "error", "cancelled"}}


def project_completed_assistant(content: Any) -> str:
    text = extract_text_from_content(content)
    outcomes: dict[tuple, tuple[str, dict]] = {}

    def add(kind: str, label: str, facts: dict, source: dict | None = None) -> None:
        identity = next((facts[k] for k in ("call_id", "form_id", "workspace_path", "url") if facts.get(k)), None)
        # Exact duplicates and duplicate references are one historical delivery.
        # Distinct files with the same display name remain distinct.
        fingerprint = hashlib.sha256(json.dumps(source or facts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        key = (kind, label, identity or fingerprint)
        if key in outcomes:
            previous = outcomes[key][1]
            facts = {**facts, **previous}
        outcomes[key] = (label, facts)

    for block in content_blocks(content):
        kind = block.get("type")
        if kind == "form":
            # status/result_message/next_form mutate after turn closure without a
            # context revision. Only the original delivery is snapshot-stable.
            add(kind, "已提供表单", _fields(block, "form_id", "form_type", "title"))
        elif kind == "file":
            add(kind, "已提供文件", _fields(block, "name", "workspace_path", "url", "mime_type"))
        elif kind == "table":
            facts = _fields(block, "title")
            rows = block.get("rows")
            if isinstance(rows, list):
                facts["displayed_rows"] = len(rows)
            facts["truncated"] = block.get("truncated") is True
            add(kind, "已展示表格", facts, block)
        elif kind in {"chart", "diagram", "ecom_plan"}:
            label = {"chart": "已提供图表", "diagram": "已提供图示", "ecom_plan": "已提供图片方案"}[kind]
            add(kind, label, _fields(block, "title", "chart_type", "format"), block)
        elif kind in {"image", "video", "audio"}:
            facts = _fields(block, "name", "alt", "workspace_path", "url")
            noun = {"image": "图片", "video": "视频", "audio": "音频"}[kind]
            if block.get("failed") is True:
                label = f"{noun}生成失败"
                facts.update(_fields(block, "error"))
            elif not block.get("url") and not block.get("workspace_path"):
                label = f"{noun}尚无可用结果"
            else:
                label = "📊 [已生成图表]" if kind == "image" else f"已提供{noun}"
            add(kind, label, facts, block)
        elif kind == "tool_step":
            label = {"completed": "调用已返回", "error": "调用失败", "cancelled": "已取消"}.get(block.get("status"))
            if label:
                facts = _fields(block, "tool_name")
                if block.get("tool_call_id"):
                    facts["call_id"] = block["tool_call_id"]
                add(kind, label, facts, block)
        elif kind == "tool_result":
            # This is a visible agent conclusion, unlike raw tool_step.output.
            conclusion = block.get("text")
            if isinstance(conclusion, str) and conclusion and conclusion not in text:
                text = "\n".join(filter(None, (text, conclusion)))
            add(kind, "已提供工具结论", _fields(block, "tool_name"))
            for file in block.get("files") or []:
                if isinstance(file, dict):
                    add("file", "已提供文件", _fields(file, "name", "workspace_path", "url", "mime_type"))

    if not outcomes:
        return text
    lines = [text] if text else []
    lines.append(_OUTCOMES_MARKER)
    lines.extend(f"- {label}: {json.dumps(facts, ensure_ascii=False)}" for label, facts in outcomes.values())
    return "\n".join(lines)
