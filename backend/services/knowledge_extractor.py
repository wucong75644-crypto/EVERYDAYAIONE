"""
知识提取器

从高可信事件（智能重试成功、任务失败）中提取可复用知识。
降级链：qwen-turbo → qwen-plus → 跳过。
"""

import json
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from core.config import settings
from services.dashscope_client import DashScopeClient
from services.knowledge_service import add_knowledge, get_node_by_metadata
from services.graph_service import graph_service

EXTRACTION_PROMPT = """你是 AI 系统的知识管理员。根据以下任务执行结果，提取可复用的系统知识。

任务信息：
- 类型：{task_type}
- 模型：{model_id}
- 状态：{status}
- 错误信息：{error_message}
- 重试信息：{retry_info}

请以 JSON 数组格式返回知识条目（0-3 条，没有有价值的知识则返回空数组 []）：
[
  {{
    "category": "model|tool|experience",
    "subcategory": "chat|image_generation|video_generation",
    "title": "简短标题（≤50字）",
    "content": "详细描述（≤200字）",
    "related_entities": ["model_id_1", "model_id_2"],
    "relations": [
      {{"from": "model_id_1", "to": "model_id_2", "type": "better_than|struggles_with|good_at"}}
    ],
    "confidence": 0.5
  }}
]

提取规则：
1. 只提取关于模型能力、工具特性、参数效果的系统知识
2. 不提取用户个人信息或对话内容
3. 失败经验必须包含具体错误原因，不要笼统的"失败了"
4. 重试成功必须记录两个模型的对比结论
5. 置信度：重试对比=0.9，明确错误=0.8，推测性结论=0.5
6. 只返回 JSON 数组，不要其他文字"""

# 模块级 HTTP 客户端
_ds_client = DashScopeClient("kb_extraction_timeout")


def _build_prompt(
    task_type: str,
    model_id: str,
    status: str,
    error_message: Optional[str] = None,
    retry_info: Optional[str] = None,
) -> str:
    """构建提取 prompt"""
    return EXTRACTION_PROMPT.format(
        task_type=task_type,
        model_id=model_id,
        status=status,
        error_message=error_message or "无",
        retry_info=retry_info or "无重试",
    )


def _parse_extraction(text: str) -> List[Dict[str, Any]]:
    """解析 LLM 返回的 JSON 知识条目"""
    text = text.strip()
    # 去除 markdown 代码块包裹
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        return []
    except json.JSONDecodeError:
        # 尝试提取 JSON 数组部分
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        logger.warning(f"Knowledge extraction JSON parse failed | text={text[:100]}")
        return []


async def _call_extraction_model(
    model: str, prompt: str
) -> Optional[List[Dict[str, Any]]]:
    """调用单个模型做知识提取"""
    client = await _ds_client.get()

    try:
        response = await client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 1000,
                "enable_thinking": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        items = _parse_extraction(content)
        logger.info(
            f"Knowledge extraction done | model={model} | items={len(items)}"
        )
        return items
    except httpx.TimeoutException:
        logger.warning(f"Knowledge extraction timeout | model={model}")
        return None
    except Exception as e:
        logger.warning(f"Knowledge extraction failed | model={model} | error={type(e).__name__}: {e or 'no detail'}")
        return None


async def extract_and_save(
    *,
    task_type: str,
    model_id: str,
    status: str,
    error_message: Optional[str] = None,
    retry_from_model: Optional[str] = None,
) -> int:
    """
    从任务事件中提取并保存知识（fire-and-forget 入口）

    Returns:
        保存的知识条目数
    """
    if not settings.kb_enabled or not settings.dashscope_api_key:
        return 0

    retry_info = None
    if retry_from_model:
        retry_info = f"从 {retry_from_model} 失败后切换到 {model_id} 成功"

    prompt = _build_prompt(task_type, model_id, status, error_message, retry_info)

    # 降级链：turbo → plus → 跳过
    items = await _call_extraction_model(settings.kb_extraction_model, prompt)
    if items is None:
        items = await _call_extraction_model(
            settings.kb_extraction_fallback_model, prompt
        )
    if items is None or not items:
        return 0

    saved = 0
    for item in items:
        # 验证必须字段
        if not all(k in item for k in ("category", "title", "content")):
            continue
        if item["category"] not in ("model", "tool", "experience"):
            continue

        # 保存知识节点
        node_id = await add_knowledge(
            category=item["category"],
            subcategory=item.get("subcategory"),
            node_type=_infer_node_type(item),
            title=item["title"][:100],
            content=item["content"][:1000],
            metadata={"source_model": model_id, "task_type": task_type},
            source="auto",
            confidence=item.get("confidence", 0.5),
        )
        if not node_id:
            continue
        saved += 1

        # 构建关系边
        for relation in item.get("relations", []):
            await _save_relation(node_id, relation)

    logger.info(
        f"Knowledge extracted | task={task_type} model={model_id} | saved={saved}"
    )
    return saved


def _infer_node_type(item: Dict[str, Any]) -> str:
    """从知识条目推断 node_type"""
    category = item.get("category", "")
    if category == "model":
        return "capability"
    if category == "tool":
        return "parameter"
    return "pattern"


async def _save_relation(node_id: str, relation: Dict[str, Any]) -> None:
    """保存一条关系边"""
    from_entity = relation.get("from", "")
    to_entity = relation.get("to", "")
    rel_type = relation.get("type", "related_to")

    if not from_entity or not to_entity:
        return

    valid_types = {
        "good_at", "struggles_with", "better_than",
        "requires", "produces", "related_to",
    }
    if rel_type not in valid_types:
        rel_type = "related_to"

    # 查找实体节点（按 model_id metadata 匹配）
    from_node = await get_node_by_metadata("model_id", from_entity)
    to_node = await get_node_by_metadata("model_id", to_entity)

    if from_node and to_node:
        await graph_service.add_edge(
            source_id=str(from_node["id"]),
            target_id=str(to_node["id"]),
            relation_type=rel_type,
        )
    elif from_node:
        # to_entity 没有对应节点，用当前新建的节点
        await graph_service.add_edge(
            source_id=str(from_node["id"]),
            target_id=node_id,
            relation_type=rel_type,
        )


async def close() -> None:
    """关闭 HTTP 客户端"""
    await _ds_client.close()
