"""
社交媒体爬虫工具定义

为 Agent Loop 提供 MediaCrawler 相关的工具定义。
由 agent_tools.py 导入合并。
"""

from typing import Any, Dict, List, Set


# 爬虫工具名集合（INFO 类型：结果回传大脑）
CRAWLER_INFO_TOOLS: Set[str] = {
    "social_crawler",
}

# 爬虫工具 Schema（参数验证）
CRAWLER_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "social_crawler": {
        "required": ["platform", "keywords"],
        "properties": {
            "platform": {"type": "string"},
            "keywords": {"type": "string"},
            "max_results": {"type": "integer"},
            "crawl_type": {"type": "string"},
        },
    },
}


def build_crawler_tools() -> List[Dict[str, Any]]:
    """构建社交媒体爬虫工具定义（1个 INFO 工具）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "social_crawler",
                "description": (
                    "爬取社交媒体平台的搜索结果。适用于：用户想了解某个话题在"
                    "社交平台上的讨论、口碑、推荐、评测等场景。"
                    "支持小红书/抖音/快手/B站/微博/贴吧/知乎。"
                    "结果返回给你，你可以整理分析后回复用户。"
                    "注意：此工具执行较慢（30-120秒），仅在用户明确需要社交平台内容时使用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "platform": {
                            "type": "string",
                            "enum": [
                                "xhs", "dy", "ks",
                                "bili", "wb", "tieba", "zhihu",
                            ],
                            "description": (
                                "目标平台代码："
                                "xhs=小红书, dy=抖音, ks=快手, "
                                "bili=B站, wb=微博, tieba=贴吧, zhihu=知乎。"
                                "根据用户需求或话题特点选择最合适的平台。"
                                "美妆/种草→xhs, 视频/娱乐→dy/bili, "
                                "热点/新闻→wb, 深度讨论→zhihu"
                            ),
                        },
                        "keywords": {
                            "type": "string",
                            "description": (
                                "搜索关键词（多个用逗号分隔）。"
                                "从用户消息中提取核心搜索词，保持简洁精准"
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "最大返回条数（默认10，最大30）",
                        },
                        "crawl_type": {
                            "type": "string",
                            "enum": ["search", "detail"],
                            "description": (
                                "爬取类型：search=搜索（默认），"
                                "detail=详情页"
                            ),
                        },
                    },
                    "required": ["platform", "keywords"],
                },
            },
        },
    ]


# 爬虫路由提示词片段
CRAWLER_ROUTING_PROMPT = (
    "## 社交媒体爬虫规则\n"
    "- 用户想了解社交平台上的内容/口碑/推荐/评测 → social_crawler\n"
    "- 平台选择：美妆种草→xhs, 视频娱乐→dy/bili, 热点新闻→wb, 深度讨论→zhihu\n"
    "- 爬虫结果返回后，用 route_to_chat 总结分析回复用户\n"
    "- 如果爬虫未启用或未安装，直接告知用户\n"
    "- 注意：仅在用户明确需要社交平台内容时才调用，普通问答用 web_search\n\n"
)
