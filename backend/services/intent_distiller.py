"""
意图模式提炼

定时任务（每日一次）：
1. 聚合 knowledge_nodes 中 source='user_confirmed' 的意图模式
2. 按工具名分组，每组 ≥5 条时调千问归纳通用规则
3. 写入 source='distilled' 知识节点，全用户共享

由 BackgroundTaskWorker 调用，fire-and-forget。
"""

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from core.config import settings
from services.dashscope_client import DashScopeClient
from services.knowledge_config import get_pg_connection, is_kb_available
from services.knowledge_service import add_knowledge

# 最少模式数量（不够则不提炼）
_MIN_PATTERNS_PER_TOOL = 5

# 聚合窗口（天）
_AGGREGATION_WINDOW_DAYS = 30

# 千问客户端（延迟初始化）
_ds_client = DashScopeClient("intent_distill_timeout", default_timeout=10.0)

_DISTILL_SYSTEM_PROMPT = """你是一个意图分析专家。
给定一组"用户表达 → 确认意图"的记录，归纳出通用规则。

输出要求（JSON 格式）：
{
  "rule": "一句话描述规则（如：当用户使用修改类动词+图片类名词时，意图是编辑图片）",
  "keywords": ["关键词1", "关键词2", ...],
  "confidence": 0.0-1.0（基于模式一致性，越一致越高）
}

注意：
- 只提炼高一致性的模式，忽略噪声
- keywords 应覆盖用户表达的常见变体
- 只输出 JSON，不要额外解释"""


async def distill_intent_patterns(org_id: str | None = None) -> None:
    """
    主入口：聚合 → 分组 → 提炼 → 写入知识库

    由 BackgroundTaskWorker 按 org 迭代调用。

    Args:
        org_id: 企业 ID（None=散客数据）
    """
    if not is_kb_available():
        return

    patterns = await _aggregate_user_patterns(org_id=org_id)
    if not patterns:
        logger.debug("Intent distillation skipped | no user_confirmed patterns")
        return

    # 按 subcategory（工具名）分组
    by_tool: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in patterns:
        tool = p.get("subcategory", "unknown")
        by_tool[tool].append(p)

    distilled_count = 0
    for tool_name, tool_patterns in by_tool.items():
        if len(tool_patterns) < _MIN_PATTERNS_PER_TOOL:
            continue

        result = await _distill_for_tool(tool_name, tool_patterns)
        if result:
            await _write_distilled_rule(tool_name, result, len(tool_patterns), org_id=org_id)
            distilled_count += 1

    logger.info(
        f"Intent distillation completed | groups={len(by_tool)} | "
        f"distilled={distilled_count}"
    )


async def _aggregate_user_patterns(
    org_id: str | None = None,
) -> List[Dict[str, Any]]:
    """查询最近 30 天的 user_confirmed 意图模式（按 org 隔离）"""
    try:
        conn_ctx = await get_pg_connection()
        if conn_ctx is None:
            return []

        org_filter = (
            "AND org_id = %(org_id)s" if org_id
            else "AND org_id IS NULL"
        )

        async with conn_ctx as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT subcategory, title, content, metadata
                    FROM knowledge_nodes
                    WHERE source = 'user_confirmed'
                      AND node_type = 'intent_pattern'
                      AND is_deleted = FALSE
                      AND created_at > NOW() - make_interval(days => %(days)s)
                      {org_filter}
                    ORDER BY created_at DESC
                    LIMIT 500;
                    """,
                    {"days": _AGGREGATION_WINDOW_DAYS, "org_id": org_id},
                )
                rows = await cur.fetchall()
                columns = [desc.name for desc in cur.description]
                return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        logger.warning(f"Intent pattern aggregation failed | error={e}")
        return []


async def _distill_for_tool(
    tool_name: str, patterns: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """调千问分析一组模式，提炼规则"""
    # 构建用户消息
    lines = []
    for i, p in enumerate(patterns[:30], 1):  # 最多 30 条
        meta = p.get("metadata", {})
        if isinstance(meta, str):
            meta = json.loads(meta)
        expr = meta.get("original_expression", p.get("title", ""))
        lines.append(f"{i}. \"{expr}\" → {tool_name}")

    user_prompt = (
        f"以下是用户确认的意图记录（共{len(patterns)}条，展示前{len(lines)}条）：\n\n"
        + "\n".join(lines)
        + f"\n\n所有记录都指向工具：{tool_name}\n请归纳通用规则。"
    )

    # 降级链：qwen-turbo → qwen-plus → 跳过
    models = [
        getattr(settings, "intent_distill_model", "qwen-turbo"),
        getattr(settings, "intent_distill_fallback_model", "qwen-plus"),
    ]

    for model in models:
        result = await _call_distill_model(model, user_prompt)
        if result:
            return result

    logger.warning(f"Intent distillation failed for {tool_name} | all models failed")
    return None


async def _call_distill_model(
    model: str, user_prompt: str,
) -> Optional[Dict[str, Any]]:
    """调用单个模型做提炼"""
    client = await _ds_client.get()

    try:
        response = await client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _DISTILL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 500,
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]

        return _parse_distill_response(content)

    except httpx.TimeoutException:
        logger.warning(f"Intent distill timeout | model={model}")
        return None
    except Exception as e:
        logger.warning(f"Intent distill failed | model={model} | error={e}")
        return None


def _parse_distill_response(text: str) -> Optional[Dict[str, Any]]:
    """解析千问的 JSON 响应"""
    text = text.strip()
    # 去掉可能的 markdown code block
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
        if "rule" in result and "keywords" in result:
            return result
        return None
    except (json.JSONDecodeError, TypeError):
        return None


async def _write_distilled_rule(
    tool_name: str,
    result: Dict[str, Any],
    sample_count: int,
    org_id: str | None = None,
) -> Optional[str]:
    """将提炼规则写入知识库"""
    # 工具名到中文映射
    tool_labels = {
        "route_to_chat": "文字对话",
        "route_to_image": "图片生成/编辑",
        "route_to_video": "视频生成",
    }
    tool_label = tool_labels.get(tool_name, tool_name)

    rule = result.get("rule", "")
    keywords = result.get("keywords", [])
    confidence = min(result.get("confidence", 0.85), 0.95)

    # 根据样本量调整 confidence
    if sample_count >= 20:
        confidence = min(confidence + 0.05, 0.95)

    return await add_knowledge(
        category="experience",
        subcategory=tool_name,
        node_type="distilled_rule",
        title=f"[提炼] {tool_label}意图规则",
        content=rule,
        metadata={
            "based_on_count": sample_count,
            "keywords": keywords,
            "tool_name": tool_name,
        },
        source="distilled",
        confidence=confidence,
        scope="global",
        org_id=org_id,
    )
