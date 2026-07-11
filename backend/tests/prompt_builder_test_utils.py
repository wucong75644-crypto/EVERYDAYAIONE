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
