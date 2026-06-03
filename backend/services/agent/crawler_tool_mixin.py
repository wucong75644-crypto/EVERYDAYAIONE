"""社交媒体爬虫工具 Mixin。

依赖宿主类提供：无（独立调用 CrawlerService）。
"""
from __future__ import annotations

from typing import Any, Dict

from loguru import logger


class CrawlerToolMixin:
    """社交媒体爬虫工具 Mixin"""

    async def _social_crawler(self, args: Dict[str, Any]) -> "AgentResult":
        """爬取社交媒体平台搜索结果"""
        from core.config import get_settings
        from services.agent.agent_result import AgentResult
        from services.crawler.service import CrawlerService

        settings = get_settings()
        if not settings.crawler_enabled:
            return AgentResult(
                summary="社交媒体爬虫功能未启用，请在 .env 中设置 CRAWLER_ENABLED=true",
                status="error",
                error_message="Feature disabled: crawler_enabled=false",
                metadata={"retryable": False},
            )

        service = CrawlerService()
        if not service.is_available():
            return AgentResult(
                summary=(
                    "社交媒体爬虫未安装，请运行以下命令安装：\n"
                    "cd backend/external && git clone https://github.com/NanmiCoder/MediaCrawler.git mediacrawler\n"
                    "cd mediacrawler && python3 -m venv venv && source venv/bin/activate\n"
                    "pip install -r requirements.txt && playwright install chromium"
                ),
                status="error",
                error_message="Crawler not installed",
                metadata={"retryable": False},
            )

        platform = args.get("platform", "xhs")
        keywords_str = args.get("keywords", "")
        keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
        if not keywords:
            return AgentResult(
                summary="搜索关键词不能为空",
                status="error",
                error_message="Validation: keywords is required",
                metadata={"retryable": True},
            )

        max_results = min(args.get("max_results", 10), 30)
        crawl_type = args.get("crawl_type", "search")

        logger.info(
            f"ToolExecutor social_crawler | platform={platform} "
            f"| keywords={keywords_str} | max={max_results}"
        )

        result = await service.execute(
            platform=platform,
            keywords=keywords,
            max_notes=max_results,
            crawl_type=crawl_type,
        )

        if result.error:
            return AgentResult(
                summary=f"爬取失败：{result.error}",
                status="error",
                error_message=result.error,
                metadata={"retryable": True},
            )

        return AgentResult(
            summary=service.format_for_brain(result.items),
            status="success",
        )
