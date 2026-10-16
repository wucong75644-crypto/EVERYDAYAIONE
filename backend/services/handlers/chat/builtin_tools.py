"""Project provider-managed activity into the existing tool_step/text channel."""

import json
import re


def present_builtin_event(event: dict) -> dict:
    status = event.get("status")
    sources = event.get("sources") or []
    return {
        "type": "tool_step", "tool_name": event["name"],
        "tool_call_id": "builtin:" + event["id"],
        "status": "completed" if status == "completed" else "error" if status in {"failed", "cancelled"} else "running",
        "input": json.dumps({"queries": event.get("queries", [])}, ensure_ascii=False),
        "output": "\n".join(s["url"] for s in sources) if sources else (
            "执行失败，未取得可核验资料" if status in {"failed", "cancelled"} else
            "工具执行完成；未返回来源链接" if status == "completed" else None),
    }


def source_text(sources: dict[str, str]) -> str:
    # Only URLs already validated at the provider boundary; never fabricate citation offsets.
    return "\n\n检索来源（未与正文逐句对应）：\n" + "\n".join(
        f"- [{_source_title(title, url)}](<{url}>)"
        for url, title in list(sources.items())[:20]
    )


def _source_title(title: str, url: str) -> str:
    """Keep provider titles as literal Markdown link labels."""
    title = " ".join((title or url).split())
    return re.sub(r"([\\`*{}\[\]<>_])", r"\\\1", title)
