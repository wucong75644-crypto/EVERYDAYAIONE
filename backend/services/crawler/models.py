"""爬虫数据模型

统一各平台爬取结果为标准化格式，供 Agent 大脑消费。
"""

from dataclasses import dataclass, field
from typing import List, Optional


# MediaCrawler 支持的平台代码
SUPPORTED_PLATFORMS = {"xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu"}

# 平台中文名映射
PLATFORM_NAMES = {
    "xhs": "小红书",
    "dy": "抖音",
    "ks": "快手",
    "bili": "B站",
    "wb": "微博",
    "tieba": "贴吧",
    "zhihu": "知乎",
}


@dataclass
class CrawlItem:
    """单条爬取结果（跨平台统一字段）"""

    platform: str
    title: str
    content: str = ""
    author: str = ""
    url: str = ""
    liked_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    collected_count: int = 0
    publish_time: str = ""
    images: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    source_keyword: str = ""


@dataclass
class CrawlResult:
    """爬取任务结果"""

    platform: str
    items: List[CrawlItem] = field(default_factory=list)
    total_found: int = 0
    elapsed_seconds: float = 0.0
    error: Optional[str] = None
