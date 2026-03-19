"""
Agent Result Builder — Agent Loop 结果构建函数

从 AgentLoop 提取的纯函数，负责构建各类 AgentResult 响应。
- build_final_result: 从路由决策构建最终结果
- build_chat_result: 大脑直接文字回复 → 兜底
- build_graceful_timeout: 超时/超预算 → 优雅终止
"""

from typing import Any, Dict, List

from loguru import logger

from schemas.message import GenerationType
from services.agent_types import AgentResult

# 路由工具名 → 前端渲染提示
TOOL_RENDER_HINTS: Dict[str, Dict[str, str]] = {
    "route_to_image": {
        "placeholder_text": "图片生成中",
        "component": "image_grid",
    },
    "route_to_video": {
        "placeholder_text": "视频生成中",
        "component": "video_player",
    },
}


def build_chat_result(
    text: str,
    context: List[str],
    turns: int,
    tokens: int,
    model: str = "",
) -> AgentResult:
    """大脑直接文字回复 → 走 ChatHandler（兜底）

    Args:
        model: 指定模型（v2 传入 Phase 1 选定的模型，空=用默认）
    """
    if not model:
        from config.smart_model_config import DEFAULT_CHAT_MODEL
        model = DEFAULT_CHAT_MODEL

    search_ctx = "\n".join(context) if context else None
    return AgentResult(
        generation_type=GenerationType.CHAT,
        model=model,
        search_context=search_ctx,
        direct_reply=text if text else None,
        turns_used=turns,
        total_tokens=tokens,
    )


def build_final_result(
    routing_holder: Dict[str, Any],
    context: List[str],
    turns: int,
    tokens: int,
) -> AgentResult:
    """从路由决策构建最终 AgentResult"""
    decision = routing_holder.get("decision")
    search_ctx = "\n".join(context) if context else None

    if not decision:
        return build_chat_result("", context, turns, tokens)

    tool_name = decision["tool_name"]
    args = decision["arguments"]

    if tool_name == "route_to_chat":
        return AgentResult(
            generation_type=GenerationType.CHAT,
            model=args.get("model", ""),
            system_prompt=args.get("system_prompt"),
            search_context=search_ctx,
            tool_params={
                **args,
                "_needs_google_search": args.get(
                    "needs_google_search", False,
                ),
            },
            turns_used=turns,
            total_tokens=tokens,
        )

    if tool_name == "route_to_image":
        prompts = args.get("prompts", [])
        model = args.get("model", "")
        render_hints = TOOL_RENDER_HINTS.get("route_to_image")

        if len(prompts) == 1:
            return AgentResult(
                generation_type=GenerationType.IMAGE,
                model=model,
                search_context=search_ctx,
                tool_params={
                    "prompt": prompts[0]["prompt"],
                    "aspect_ratio": prompts[0].get("aspect_ratio", "1:1"),
                },
                render_hints=render_hints,
                turns_used=turns,
                total_tokens=tokens,
            )
        return AgentResult(
            generation_type=GenerationType.IMAGE,
            model=model,
            search_context=search_ctx,
            batch_prompts=prompts,
            tool_params=args,
            render_hints=render_hints,
            turns_used=turns,
            total_tokens=tokens,
        )

    if tool_name == "route_to_video":
        return AgentResult(
            generation_type=GenerationType.VIDEO,
            model=args.get("model", ""),
            search_context=search_ctx,
            tool_params=args,
            render_hints=TOOL_RENDER_HINTS.get("route_to_video"),
            turns_used=turns,
            total_tokens=tokens,
        )

    if tool_name == "ask_user":
        return AgentResult(
            generation_type=GenerationType.CHAT,
            model="",
            search_context=search_ctx,
            direct_reply=args.get("message", ""),
            tool_params={"_ask_reason": args.get("reason", "need_info")},
            turns_used=turns,
            total_tokens=tokens,
        )

    # 未知路由工具（理论上不会到这里）
    return build_chat_result("", context, turns, tokens)


def build_graceful_timeout(
    context: List[str],
    turns: int,
    tokens: int,
    model: str = "",
) -> AgentResult:
    """超出轮次/token → 优雅终止（保存已有进度）

    Args:
        model: 指定模型（v2 传入 Phase 1 选定的模型，空=用默认）
    """
    logger.warning(
        f"Agent loop graceful timeout | turns={turns} | tokens={tokens}"
    )

    if context:
        return build_chat_result("", context, turns, tokens, model=model)

    if not model:
        from config.smart_model_config import DEFAULT_CHAT_MODEL
        model = DEFAULT_CHAT_MODEL
    return AgentResult(
        generation_type=GenerationType.CHAT,
        model=model,
        turns_used=turns,
        total_tokens=tokens,
    )
