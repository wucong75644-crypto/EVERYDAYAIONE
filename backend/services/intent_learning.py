"""
意图学习服务

ask_user 确认 → 记录意图模式 → 写入知识库 → 下次自动路由

流程：
1. ask_user 触发时 → record_ask_user_context() 存 pending 到 knowledge_metrics
2. 用户回复后路由成功 → check_and_record_intent() 匹配 pending → 写知识库
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger

from services.knowledge_config import get_pg_connection, is_kb_available
from services.knowledge_metrics import record_metric

# pending 有效期（秒）— 超过此时间的 ask_user 上下文不再匹配
_PENDING_TTL_SECONDS = 1800  # 30 分钟


async def record_ask_user_context(
    *,
    conversation_id: str,
    user_id: str,
    original_message: str,
    ask_options: str,
) -> None:
    """
    ask_user 触发时，存储上下文到 knowledge_metrics（pending 状态）

    Args:
        conversation_id: 会话 ID（用于后续匹配）
        user_id: 用户 ID
        original_message: 用户的原始消息（触发 ask_user 的那句话）
        ask_options: ask_user 的选项文本
    """
    await record_metric(
        task_type="intent_pending",
        model_id="agent_loop",
        status="pending",
        user_id=user_id,
        params={
            "conversation_id": conversation_id,
            "original_message": original_message[:500],
            "ask_options": ask_options[:1000],
        },
    )
    logger.debug(
        f"Intent pending recorded | conv={conversation_id} | "
        f"msg={original_message[:80]}"
    )


async def check_and_record_intent(
    *,
    conversation_id: str,
    user_id: str,
    user_response: str,
    confirmed_tool: str,
) -> None:
    """
    路由成功后，检查是否有 pending 的 ask_user → 记录意图模式

    Args:
        conversation_id: 会话 ID
        user_id: 用户 ID
        user_response: 用户回复的消息（选了哪个选项）
        confirmed_tool: 最终确认的路由工具名（route_to_image 等）
    """
    if not is_kb_available():
        return

    # 1. 查最近的 intent_pending（同会话、30分钟内）
    pending = await _find_recent_pending(conversation_id)
    if not pending:
        return

    original_message = pending.get("original_message", "")
    ask_options = pending.get("ask_options", "")

    if not original_message:
        return

    # 2. 写入知识库
    await _write_intent_pattern(
        original_expression=original_message,
        confirmed_tool=confirmed_tool,
        user_response=user_response,
        ask_options=ask_options,
    )

    # 3. 标记已处理
    await record_metric(
        task_type="intent_confirmed",
        model_id="agent_loop",
        status="confirmed",
        user_id=user_id,
        params={
            "conversation_id": conversation_id,
            "original_message": original_message[:500],
            "confirmed_tool": confirmed_tool,
        },
    )

    logger.info(
        f"Intent learned | expr={original_message[:80]} | "
        f"tool={confirmed_tool} | conv={conversation_id}"
    )


async def _find_recent_pending(conversation_id: str) -> Optional[Dict[str, Any]]:
    """查询最近 30 分钟内同会话的 intent_pending"""
    try:
        conn_ctx = await get_pg_connection()
        if conn_ctx is None:
            return None

        async with conn_ctx as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT params FROM knowledge_metrics
                    WHERE task_type = 'intent_pending'
                      AND status = 'pending'
                      AND params->>'conversation_id' = %(conv_id)s
                      AND created_at > NOW() - make_interval(secs => %(ttl)s)
                    ORDER BY created_at DESC
                    LIMIT 1;
                    """,
                    {"conv_id": conversation_id, "ttl": _PENDING_TTL_SECONDS},
                )
                row = await cur.fetchone()
                if not row:
                    return None
                params = row[0]
                if isinstance(params, str):
                    params = json.loads(params)
                return params
    except Exception as e:
        logger.warning(f"Intent pending query failed | error={e}")
        return None


async def _write_intent_pattern(
    *,
    original_expression: str,
    confirmed_tool: str,
    user_response: str,
    ask_options: str,
) -> Optional[str]:
    """将意图模式写入 knowledge_nodes"""
    from services.knowledge_service import add_knowledge

    # 工具名到中文映射
    tool_labels = {
        "route_to_chat": "文字对话",
        "route_to_image": "图片生成/编辑",
        "route_to_video": "视频生成",
    }
    tool_label = tool_labels.get(confirmed_tool, confirmed_tool)

    title = f"「{original_expression[:30]}」→ {tool_label}"
    content = (
        f"用户说「{original_expression}」时，"
        f"确认选择了{tool_label}，应路由到 {confirmed_tool}"
    )

    return await add_knowledge(
        category="experience",
        subcategory=confirmed_tool,
        node_type="intent_pattern",
        title=title,
        content=content,
        metadata={
            "original_expression": original_expression[:200],
            "confirmed_tool": confirmed_tool,
            "user_response": user_response[:200],
            "ask_options_summary": ask_options[:500],
        },
        source="user_confirmed",
        confidence=0.8,
        scope="global",
    )
