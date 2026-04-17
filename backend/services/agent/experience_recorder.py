"""
Agent 经验记录器（独立类）。

从 ERPAgent 提取，支持多 Agent 共用。
记录路由经验（成功路径）和失败记忆（失败教训）到知识库。

设计文档: docs/document/TECH_多Agent单一职责重构.md §7.1（D16）
"""
from __future__ import annotations

import re
from typing import Any, List, Optional

from loguru import logger


# 业务域白名单：从 tool_name 推断 subcategory
_BUSINESS_DOMAINS = frozenset({
    "stock", "order", "product", "purchase",
    "aftersale", "warehouse", "info", "general",
})

# 同义归一
_DOMAIN_NORMALIZE = {
    "aftersales": "aftersale",
    "trade": "order",
    "inventory": "stock",
    "basic": "info",
}

_TOOL_PATTERN = re.compile(
    r"^(?:local_|erp_)([a-z]+?)(?:_query|_identify|_stats|_flow)?$",
)

# per-node_type 独立配额
ROUTING_PATTERN_MAX = 400
FAILURE_PATTERN_MAX = 200


def infer_business_domain(tools_called: List[str]) -> str:
    """从 tools_called 列表推断业务域，作为知识节点的 subcategory。

    命名规范：local_*_query / erp_*_query / local_*_identify / local_*_stats
    无匹配返回 'general'。
    """
    for tool in tools_called:
        m = _TOOL_PATTERN.match(tool)
        if not m:
            continue
        domain = _DOMAIN_NORMALIZE.get(m.group(1), m.group(1))
        if domain in _BUSINESS_DOMAINS:
            return domain
    return "general"


class ExperienceRecorder:
    """Agent 经验记录器。

    支持多 Agent 共用（通过 writer 参数区分来源）。
    """

    def __init__(self, org_id: str, writer: str = "erp_agent"):
        self.org_id = org_id
        self.writer = writer

    async def record(
        self,
        record_type: str,
        query: str,
        tools_called: List[str],
        detail: str,
        budget: Optional[Any] = None,
        confidence: float = 0.5,
    ) -> None:
        """记录路由经验或失败记忆到知识库。

        Args:
            record_type: "routing" (成功) 或 "failure" (失败)
            query: 用户原始查询
            tools_called: 调用过的工具列表
            detail: 详情（成功=轮次信息，失败=原因）
            budget: ExecutionBudget（取 elapsed）
            confidence: 初始置信度
        """
        try:
            from services.knowledge_service import add_knowledge

            if record_type == "routing":
                node_type = "routing_pattern"
                max_count = ROUTING_PATTERN_MAX
                prefix = "查询路由"
            elif record_type == "failure":
                node_type = "failure_pattern"
                max_count = FAILURE_PATTERN_MAX
                prefix = "查询失败"
            else:
                logger.error(
                    f"ExperienceRecorder: unknown record_type={record_type!r}",
                )
                return

            elapsed = f"{budget.elapsed:.1f}s" if budget else "N/A"
            unique_tools = list(dict.fromkeys(tools_called))
            domain = infer_business_domain(unique_tools)

            await add_knowledge(
                category="experience",
                subcategory=domain,
                node_type=node_type,
                title=f"{prefix}：{query[:30]}",
                content=(
                    f"查询：{query}\n"
                    f"路径：{' → '.join(unique_tools)}\n"
                    f"{detail}\n耗时：{elapsed}"
                ),
                metadata={
                    "writer": self.writer,
                    "record_type": record_type,
                    "tools": unique_tools,
                },
                source="auto",
                confidence=confidence,
                scope="org",
                org_id=self.org_id,
                max_per_node_type=max_count,
            )
        except ValueError as e:
            logger.error(
                f"ExperienceRecorder {record_type} schema violation | "
                f"error={e}",
            )
        except Exception as e:
            logger.debug(
                f"ExperienceRecorder {record_type} save failed | error={e}",
            )
