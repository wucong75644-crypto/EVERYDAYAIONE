"""Bounded page-local public-tool continuation; no platform or model calls."""
from services.agent.agent_reach.contracts import ReachError
from .repository import display_url

MAX_COMMENT_CHUNKS = 16
MAX_PENDING_CHARS = 4500 * MAX_COMMENT_CHUNKS


def comment_chunk(item, result):
    state = dict(item.get("result_meta", {}).get("cursor_state", {}))
    count = state.get("chunks", 0) + 1
    pagination = result.metadata.get("pagination") or {}
    more = pagination.get("has_more_text") is True
    next_cursor = pagination.get("next_cursor") or ""
    current = item["request"].get("cursor", "")
    seen = state.get("seen", [])
    if count > MAX_COMMENT_CHUNKS or (more and count >= MAX_COMMENT_CHUNKS):
        raise ReachError("COMMENT_CURSOR_LIMIT", "评论页内续读达到有限分片上限，进度已保留")
    if more and (not next_cursor or next_cursor == current or next_cursor in seen):
        raise ReachError("INVALID_CURSOR", "评论续读游标未前进，进度已保留")
    sources = [dict(s) for s in result.metadata.get("sources", [])]
    pending = state.get("pending")
    if pending:
        start = pagination.get("range_start", [])
        if (not sources or display_url(sources[0]["url"]) != pending["url"]
                or len(start) != 2 or start[1] != len(pending["content"])):
            raise ReachError("CONTENT_CHANGED", "评论片段与已保存断点不一致，未拼接")
        sources[0]["content"] = pending["content"] + sources[0].get("content", "")
    end = pagination.get("range_end", [0, 0])
    pending = None
    if more:
        if not sources or len(end) != 2:
            raise ReachError("INVALID_CURSOR", "评论续读没有可验证的进度")
        if end[1]:
            pending = sources.pop()
            pending["url"] = display_url(pending["url"])
            if len(pending.get("content", "")) > MAX_PENDING_CHARS:
                raise ReachError("COMMENT_CURSOR_LIMIT", "单条评论片段超过有限续读容量")
    state = {"chunks": count, "seen": [*seen, current], "pending": pending}
    return sources, state, next_cursor if more else ""


def unique_comment_observations(rows):
    """Collapse repeated platform IDs; caller supplies newest observations first."""
    unique = {}
    for row in rows:
        unique.setdefault(row["source_key"], row)
    return list(unique.values())
