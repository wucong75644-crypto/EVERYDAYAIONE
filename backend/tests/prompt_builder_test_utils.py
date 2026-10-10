"""PromptBuilder 测试隔离：跳过外部记忆/Redis，只保留真实 DB 历史构建。"""

from services.handlers.chat_context.history_loader import build_context_messages


async def isolated_parallel_fetch(builder):
    history = await build_context_messages(
        builder.inp.db,
        builder.inp.conversation_id,
        builder.inp.text_content,
    )
    builder._persona_text = ""
    return None, builder.inp.prefetched_summary, history


def normalize_cache_transport(sent, expected):
    """Ignore only provider-added cache markers/required string-to-text wrapping."""
    import copy
    result = copy.deepcopy(sent)
    for actual, original in zip(result, expected):
        content = actual.get("content")
        reference = original.get("content")
        if isinstance(content, list):
            for index, part in enumerate(content):
                if "cache_control" not in part:
                    continue
                # Existing host markers are preserved; new markers carry no content.
                original_part = reference[index] if isinstance(reference, list) and index < len(reference) else {}
                if "cache_control" not in original_part:
                    part.pop("cache_control")
            if isinstance(reference, str) and len(content) == 1 and content[0].get("type") == "text":
                assert set(content[0]) == {"type", "text"}
                actual["content"] = content[0]["text"]
    return result
