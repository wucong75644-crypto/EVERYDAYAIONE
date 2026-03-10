"""
Agent Result Builder — Agent Loop 结果构建函数

从 AgentLoop 提取的纯函数，负责构建各类 AgentResult 响应。
每个函数对应一种路由/终止场景，返回 AgentResult 数据对象。
"""

from typing import Any, Dict, List

from loguru import logger

from schemas.message import GenerationType
from services.agent_types import AgentResult, PendingAsyncTool

# 工具名 → 前端渲染提示（大脑控制前端显示）
TOOL_RENDER_HINTS: Dict[str, Dict[str, str]] = {
    "generate_image": {"placeholder_text": "图片生成中", "component": "image_grid"},
    "generate_video": {"placeholder_text": "视频生成中", "component": "video_player"},
    "batch_generate_image": {"placeholder_text": "图片生成中", "component": "image_grid"},
}


def build_chat_result(
    text: str, context: List[str], turns: int, tokens: int,
) -> AgentResult:
    """大脑直接文字回复 → 走 ChatHandler"""
    from config.smart_model_config import DEFAULT_CHAT_MODEL

    search_ctx = "\n".join(context) if context else None
    return AgentResult(
        generation_type=GenerationType.CHAT,
        model=DEFAULT_CHAT_MODEL,
        search_context=search_ctx,
        direct_reply=text if text else None,
        turns_used=turns,
        total_tokens=tokens,
    )


def build_terminal_result(
    tool_name: str,
    arguments: Dict[str, Any],
    context: List[str],
    pending_async: List[PendingAsyncTool],
    turns: int,
    tokens: int,
) -> AgentResult:
    """终端工具 → 构建最终结果"""
    model = arguments.get("model", "")
    search_ctx = "\n".join(context) if context else None

    if tool_name == "text_chat":
        return AgentResult(
            generation_type=GenerationType.CHAT,
            model=model,
            system_prompt=arguments.get("system_prompt"),
            search_context=search_ctx,
            tool_params=arguments,
            turns_used=turns,
            total_tokens=tokens,
        )

    if tool_name == "finish" and pending_async:
        return build_async_result(pending_async, context, turns, tokens)

    return AgentResult(
        generation_type=GenerationType.CHAT,
        model="",
        search_context=search_ctx,
        turns_used=turns,
        total_tokens=tokens,
    )


def build_ask_user_result(
    arguments: Dict[str, Any],
    context: List[str],
    pending_async: List[PendingAsyncTool],
    turns: int,
    tokens: int,
    conversation_id: str = "",
) -> AgentResult:
    """ask_user → 大脑主动回复用户（追问/说明）"""
    message = arguments.get("message", "")
    reason = arguments.get("reason", "need_info")
    search_ctx = "\n".join(context) if context else None

    logger.info(
        f"Agent ask_user | reason={reason} | "
        f"conv={conversation_id} | turns={turns}"
    )

    return AgentResult(
        generation_type=GenerationType.CHAT,
        model="",
        search_context=search_ctx,
        direct_reply=message,
        tool_params={"_ask_reason": reason},
        turns_used=turns,
        total_tokens=tokens,
    )


def build_search_result(
    arguments: Dict[str, Any],
    context: List[str],
    turns: int,
    tokens: int,
    conversation_id: str = "",
) -> AgentResult:
    """web_search → 按能力匹配搜索模型

    大脑只负责判断"需要搜索"，实际搜索由模型库中有搜索能力的模型执行。
    模型选择：smart_models.json → web_search.models（按 priority 排序）→ 取第一个。
    """
    from config.smart_model_config import SMART_CONFIG, DEFAULT_CHAT_MODEL

    ws_models = SMART_CONFIG.get("web_search", {}).get("models", [])
    model = ws_models[0]["id"] if ws_models else DEFAULT_CHAT_MODEL

    search_ctx = "\n".join(context) if context else None

    logger.info(
        f"Agent web_search → routed to search model | model={model} | "
        f"query={arguments.get('search_query', '')} | conv={conversation_id}"
    )

    return AgentResult(
        generation_type=GenerationType.CHAT,
        model=model,
        system_prompt=arguments.get("system_prompt"),
        search_context=search_ctx,
        tool_params={
            "_needs_google_search": True,
            "_search_query": arguments.get("search_query", ""),
        },
        turns_used=turns,
        total_tokens=tokens,
    )


def build_async_result(
    pending_async: List[PendingAsyncTool],
    context: List[str],
    turns: int,
    tokens: int,
) -> AgentResult:
    """纯异步工具 → 从第一个异步工具推断 generation_type"""
    from config.smart_model_config import TOOL_TO_TYPE

    if not pending_async:
        return build_chat_result("", context, turns, tokens)

    first = pending_async[0]
    gen_type = TOOL_TO_TYPE.get(first.tool_name, GenerationType.CHAT)
    model = first.arguments.get("model", "")
    search_ctx = "\n".join(context) if context else None
    render_hints = TOOL_RENDER_HINTS.get(first.tool_name)

    if first.tool_name == "batch_generate_image":
        prompts = first.arguments.get("prompts", [])
        return AgentResult(
            generation_type=GenerationType.IMAGE,
            model=model,
            search_context=search_ctx,
            batch_prompts=prompts,
            tool_params=first.arguments,
            render_hints=render_hints,
            turns_used=turns,
            total_tokens=tokens,
        )

    return AgentResult(
        generation_type=gen_type,
        model=model,
        search_context=search_ctx,
        tool_params=first.arguments,
        render_hints=render_hints,
        turns_used=turns,
        total_tokens=tokens,
    )


def build_graceful_timeout(
    pending_async: List[PendingAsyncTool],
    context: List[str],
    turns: int,
    tokens: int,
) -> AgentResult:
    """超出轮次/token → 优雅终止（保存已有进度）"""
    logger.warning(
        f"Agent loop graceful timeout | turns={turns} | "
        f"tokens={tokens} | pending_async={len(pending_async)}"
    )

    if pending_async:
        return build_async_result(pending_async, context, turns, tokens)
    if context:
        return build_chat_result("", context, turns, tokens)

    from config.smart_model_config import DEFAULT_CHAT_MODEL
    return AgentResult(
        generation_type=GenerationType.CHAT,
        model=DEFAULT_CHAT_MODEL,
        turns_used=turns,
        total_tokens=tokens,
    )
