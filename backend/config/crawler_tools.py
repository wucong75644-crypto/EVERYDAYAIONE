"""Compatibility projections; tool definitions are owned by ToolSpec. Prompt selection stays here."""

from typing import Any, Dict, List, Set
from services.tools.catalog import definition_registry, group_schemas, validation_schemas


CRAWLER_INFO_TOOLS: Set[str] = {s.name for s in definition_registry().specs() if 'crawler_tools' in s.catalog_groups}


CRAWLER_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = validation_schemas('crawler_tools')


def build_crawler_tools() -> List[Dict[str, Any]]:
    return group_schemas('crawler_tools')


CRAWLER_ROUTING_PROMPT = (
    "## 社交媒体爬虫规则\n"
    "- 用户想了解社交平台上的内容/口碑/推荐/评测 → social_crawler\n"
    "- 平台选择：美妆种草→xhs, 视频娱乐→dy/bili, 热点新闻→wb, 深度讨论→zhihu\n"
    "- 爬虫结果返回后，用 route_to_chat 总结分析回复用户\n"
    "- 如果爬虫未启用或未安装，直接告知用户\n"
    "- 注意：仅在用户明确需要社交平台内容时才调用，普通问答用 web_search\n\n"
)
