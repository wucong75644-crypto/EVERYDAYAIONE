"""Allowlisted, bounded summaries for non-SAFE tool confirmation."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

MAX_SUMMARY_BYTES = 2048


class ConfirmationSummaryError(ValueError):
    pass


def _fixed(description: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    return lambda _args: {"description": description}


def _enum(value: Any, allowed: set[str], fallback: str = "other") -> str:
    text = str(value or "").lower()
    return text if text in allowed else fallback


def _code(args: Mapping[str, Any]) -> dict[str, Any]:
    code = args.get("code")
    return {
        "description": "执行代码",
        "runtime": _enum(args.get("runtime"), {"python", "javascript", "bash"}),
        "code_characters": len(code) if isinstance(code, str) else 0,
        "timeout_seconds": min(max(int(args.get("timeout", 60)), 1), 300),
    }


def _media(args: Mapping[str, Any]) -> dict[str, Any]:
    model = _enum(args.get("model"), {
        "default", "dall-e-3", "gpt-image-1", "imagen-3", "imagen-4",
        "veo-2", "veo-3", "kling", "seedream", "seedance",
    })
    size = _enum(args.get("size") or args.get("resolution"), {
        "default", "1024x1024", "1024x1536", "1536x1024",
        "720p", "1080p", "square", "portrait", "landscape",
    })
    return {
        "description": "生成媒体内容",
        "model": model,
        "size": size,
        "count": min(max(int(args.get("n") or args.get("count") or 1), 1), 20),
    }


def _operation(description: str, allowed: set[str]):
    def build(args: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "description": description,
            "operation": _enum(args.get("action") or args.get("operation"), allowed),
        }
    return build


_BUILDERS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "file_analyze": _fixed("分析所选文件并生成临时分析数据"),
    "fetch_all_pages": lambda args: {
        "description": "读取ERP分页数据并生成临时分析文件",
        "record_type": _enum(args.get("record_type") or args.get("type"), {
            "order", "trade", "product", "stock", "aftersale", "purchase",
        }),
    },
    "erp_agent": _fixed("执行ERP分析"),
    "erp_analyze": _fixed("执行ERP分析"),
    "web_search": lambda args: {
        "description": "搜索公开网络信息",
        "query_category": _enum(args.get("category"), {"news", "product", "company", "general"}),
        "site_category": _enum(args.get("site_category"), {"official", "news", "public", "general"}),
    },
    "generate_image": _media,
    "generate_video": _media,
    "image_agent": _media,
    "code_execute": _code,
    "erp_execute": _operation("执行ERP业务操作", {"create", "update", "cancel", "submit", "other"}),
    "trigger_erp_sync": _operation("触发ERP同步", {"full", "incremental", "other"}),
    "file_delete": _fixed("删除所选文件"),
    "restore_file": _fixed("恢复一个文件"),
    "manage_scheduled_task": _operation(
        "管理计划任务", {"create", "update", "pause", "resume", "delete"},
    ),
}


def registered_preview_tools() -> frozenset[str]:
    return frozenset(_BUILDERS)


def build_confirmation_summary(
    tool_name: str, arguments: Mapping[str, Any],
) -> dict[str, Any]:
    builder = _BUILDERS.get(tool_name)
    if builder is None:
        raise ConfirmationSummaryError("summary builder is not registered")
    try:
        summary = builder(arguments)
        encoded = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ConfirmationSummaryError("summary cannot be built") from exc
    if len(encoded.encode("utf-8")) > MAX_SUMMARY_BYTES:
        raise ConfirmationSummaryError("summary exceeds size limit")
    forbidden = ("/", "oss_", "secret", "token", "prompt")
    value_text = " ".join(str(value) for value in summary.values()).lower()
    if any(term in value_text for term in forbidden):
        raise ConfirmationSummaryError("summary contains a sensitive field")
    return summary
