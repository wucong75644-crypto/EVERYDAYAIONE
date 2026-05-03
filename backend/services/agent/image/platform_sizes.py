"""
电商平台图片尺寸规范

各平台主图/竖图/详情页的标准尺寸（像素），
供 ImageAgent 裁切和 enhance API 提示词使用。
设计文档：docs/document/TECH_电商图片Agent.md §13.9
"""

from __future__ import annotations

from typing import Any

# 平台 → 尺寸规范
PLATFORM_SIZES: dict[str, dict[str, Any]] = {
    "taobao": {
        "main": [(800, 800), (750, 1000)],
        "detail_width": 750,
        "detail_max_height": 1200,
    },
    "tmall": {
        "main": [(800, 800), (750, 1000)],
        "detail_width": 750,
        "detail_max_height": 1300,
    },
    "jd": {
        "main": [(800, 800), (750, 1000)],
        "detail_width": 750,
        "detail_max_height": None,
    },
    "pdd": {
        "main": [(480, 480)],
        "detail_width": 750,
        "detail_max_height": 1500,
    },
    "douyin": {
        "main": [(800, 800), (750, 1000)],
        "detail_width": 750,
        "detail_max_height": None,
    },
    "xiaohongshu": {
        "main": [(800, 800), (750, 1000)],
        "detail_width": 750,
        "detail_max_height": None,
    },
}


def get_default_main_size(platform: str) -> tuple[int, int]:
    """获取平台默认主图尺寸（第一个）。"""
    sizes = PLATFORM_SIZES.get(platform, PLATFORM_SIZES["taobao"])
    return sizes["main"][0]
