"""PromptBuilder 测试隔离：跳过外部记忆/Redis，保留测试历史输入。"""

from services.handlers.chat_context.history_loader import build_context_messages


async def isolated_parallel_fetch(builder):
    snapshot = builder.inp.context_snapshot
    history = (
        list(snapshot.history_messages)
        if snapshot is not None
        else await build_context_messages(
            builder.inp.db,
            builder.inp.conversation_id,
            builder.inp.text_content,
        )
    )
    builder._persona_text = ""
    return None, history
