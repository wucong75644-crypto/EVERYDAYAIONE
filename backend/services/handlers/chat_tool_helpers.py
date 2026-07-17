"""Chat 工具调用的纯参数与流增量辅助函数。"""

from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger


def partition_tool_calls(
    tool_calls: List[Dict[str, Any]],
) -> List[tuple]:
    """按并发安全性把连续只读调用合并、写调用独立分批。"""
    from config.chat_tools import is_concurrency_safe

    batches: List[tuple] = []
    current_batch: List[Dict[str, Any]] = []
    current_safe = True
    for tool_call in tool_calls:
        safe = is_concurrency_safe(tool_call["name"])
        if safe and current_safe and current_batch:
            current_batch.append(tool_call)
        elif safe and not current_batch:
            current_safe = True
            current_batch = [tool_call]
        else:
            if current_batch:
                batches.append((current_safe, current_batch))
            current_batch = [tool_call]
            current_safe = safe
    if current_batch:
        batches.append((current_safe, current_batch))
    return batches


def resolve_file_ids(
    args: Dict[str, Any],
    conversation_id: str,
    tool_name: str = "",
) -> Dict[str, Any]:
    """按工具用途把文件 ID 转换为已注册的安全路径。"""
    from services.agent.file_path_cache import get_file_cache

    if tool_name == "file_analyze":
        usage = "analyze"
    elif tool_name == "file_delete":
        usage = "delete"
    else:
        return args
    cache = get_file_cache(conversation_id)
    path_value = args.get("path")
    if isinstance(path_value, str) and path_value:
        try:
            resolved = cache.resolve_path(path_value, usage=usage)
            logger.debug(
                f"get_file | {tool_name} | {path_value} → {resolved}"
            )
            args["path"] = resolved
        except FileNotFoundError:
            pass
    files_value = args.get("files")
    if isinstance(files_value, list):
        translated = []
        for item in files_value:
            if not isinstance(item, str):
                translated.append(item)
                continue
            try:
                translated.append(cache.resolve_path(item, usage=usage))
            except FileNotFoundError:
                translated.append(item)
        args["files"] = translated
    return args


def accumulate_tool_call_delta(
    acc: Dict[int, Dict[str, Any]],
    deltas: list,
) -> None:
    """将流式 tool_call 增量累积到索引字典。"""
    for delta in deltas:
        entry = acc.setdefault(
            delta.index,
            {"id": "", "name": "", "arguments": ""},
        )
        if delta.id:
            entry["id"] = delta.id
        if delta.name:
            entry["name"] = delta.name
        if delta.arguments_delta:
            entry["arguments"] += delta.arguments_delta
