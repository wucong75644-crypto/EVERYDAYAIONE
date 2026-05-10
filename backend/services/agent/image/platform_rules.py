"""
电商平台规则配置（v2）

各平台的风格光谱 + 硬性规则 + 主图规范。
千问根据 styles + 实际产品自主决定每张图的角色和内容，
这里不写死"第1张是钩子图第2张是卖点图"——那是千问的工作。

设计文档：docs/document/TECH_电商图片Agent_v2.md §4.3
"""

from __future__ import annotations

from typing import Any


PLATFORM_RULES: dict[str, dict[str, Any]] = {
    "taobao": {
        "label": "淘宝/天猫",
        "main_image": {
            "count": 5,
            "default_size": "800x800",
            "aspect_ratio": "1:1",
        },
        "styles": [
            "高饱和、强对比、卖点大字直给——手机缩略图0.5秒抓眼球",
            "年轻品类偏精致生活感，服装强调上身效果和面料质感",
            "食品类暖色调、食欲感、堆满画面比留白好",
            "3C/家电偏理性，参数+品质感+认证背书",
        ],
        "avoid": [
            "莫兰迪色系（在淘宝缩略图中不够突出）",
            "过度留白（信息密度太低，在信息流中被淹没）",
            "纯艺术感（买家看不懂产品是什么）",
        ],
        "hard_rules": [
            "最后一张必须纯白底：纯白背景，产品居中，无文字无水印无装饰（平台强制要求）",
            "2026新规：SKU规格图直接出搜索结果，规格图质量影响搜索曝光",
        ],
        "detail_page": {"count": "8-10", "note": "竖版信息图"},
    },
    "jd": {
        "label": "京东",
        "main_image": {
            "count": 5,
            "default_size": "800x800",
            "aspect_ratio": "1:1",
        },
        "styles": [
            "品质感和专业度要求最高——京东用户偏理性消费",
            "3C/家电：参数硬核展示、工艺细节、认证背书",
            "日用/食品：品质感+生活场景，比淘宝更克制",
            "服装：模特图优先，面料质感+版型展示",
        ],
        "hard_rules": [
            "首图必须纯白底居中（京东强制要求，否则不予展示）",
            "营销面积不超过35%（图片中文字/促销信息占比不能超过35%）",
            "禁止出现其他平台信息、联系方式、外链、二维码",
        ],
        "detail_page": {"count": "8-10", "note": "PC端宽790px"},
    },
    "pdd": {
        "label": "拼多多",
        "main_image": {
            "count": 5,
            "default_size": "800x800",
            "aspect_ratio": "1:1",
        },
        "styles": [
            "传统日用/百货：白底+产品居中+性价比大字直给，简单粗暴",
            "食品/生鲜：鲜艳色彩、堆满画面、食欲感爆棚、价格醒目",
            "年轻/文创/潮玩：网感风格——强对比、有梗、表情包式视觉冲击",
            "美妆/个护：种草风——精致真实、生活化、像小红书博主分享",
            "家居/收纳：场景化展示>棚拍——放在真实环境中让用户代入",
            "核心共性：价格敏感、信息直给、不绕弯子",
        ],
        "hard_rules": [
            "无水印、无边框、无拉伸变形",
            "商品占画面70%以上（商品必须是视觉主体）",
        ],
        "detail_page": {"count": "6-8"},
    },
    "douyin": {
        "label": "抖音",
        "main_image": {
            "count": 3,
            "default_size": "1024x1536",
            "aspect_ratio": "3:4",
        },
        "styles": [
            "竖图3:4——适配手机全屏浏览",
            "强视觉冲击、即时感——在快速滑动的信息流中必须跳出来",
            "素人感>精修感——过度精修反而让用户觉得是广告",
            "使用前后对比效果好",
            "年轻化、情绪化表达",
        ],
        "hard_rules": [],
        "detail_page": {"count": "0", "note": "抖音以视频为主，无传统详情页"},
    },
    "xiaohongshu": {
        "label": "小红书",
        "main_image": {
            "count": 9,
            "default_size": "1024x1024",
            "aspect_ratio": "1:1",
        },
        "styles": [
            "种草氛围——像博主真实分享，不像品牌广告",
            "精致但不过度精修——有生活感和真实感",
            "色调统一和谐——莫兰迪色系/奶茶色系/统一滤镜感",
            "场景化>棚拍——产品在生活中的样子比纯产品图更吸引",
            "第一人称视角效果好——桌面俯拍、手持展示、使用过程",
            "美妆：质地特写、上脸效果、色号对比",
            "家居：房间场景融入、氛围感",
        ],
        "hard_rules": [],
        "detail_page": {"count": "0", "note": "小红书无传统详情页，靠笔记图组"},
    },
    "ali1688": {
        "label": "1688",
        "main_image": {
            "count": 5,
            "default_size": "800x800",
            "aspect_ratio": "1:1",
        },
        "styles": [
            "B端批发视角——买家是经销商/采购，不是终端消费者",
            "信息密集、参数详尽——材质、规格、工艺、起批量都要展示",
            "多角度全方位展示产品——正面/侧面/背面/内部结构",
            "包装和物流方式展示——采购方关心怎么发货",
            "品质管控证据——工厂实拍、认证证书、质检报告",
        ],
        "hard_rules": [
            "强调供货能力和品质管控（B端核心关注点）",
        ],
        "detail_page": {"count": "10-15", "note": "B端详情页偏长，重参数和供货信息"},
    },
}

# tmall 复用淘宝规则
PLATFORM_RULES["tmall"] = PLATFORM_RULES["taobao"]


def get_platform_rules(platform: str) -> dict[str, Any]:
    """获取指定平台的规则配置。未知平台降级到淘宝。"""
    return PLATFORM_RULES.get(platform, PLATFORM_RULES["taobao"])


def format_platform_prompt(platform: str) -> str:
    """将平台规则格式化为 system prompt 注入片段。"""
    rules = get_platform_rules(platform)
    parts = [f"## 目标平台：{rules['label']}"]

    mi = rules["main_image"]
    parts.append(f"- 主图数量：{mi['count']}张")
    parts.append(f"- 默认尺寸：{mi['default_size']}（{mi['aspect_ratio']}）")

    if rules.get("styles"):
        parts.append("\n### 该平台的风格特征（参考，根据实际产品灵活选择）")
        for s in rules["styles"]:
            parts.append(f"- {s}")

    if rules.get("avoid"):
        parts.append("\n### 该平台应避免")
        for a in rules["avoid"]:
            parts.append(f"- {a}")

    if rules.get("hard_rules"):
        parts.append("\n### 硬性规则（违反会被平台驳回，必须遵守）")
        for r in rules["hard_rules"]:
            parts.append(f"- ⚠️ {r}")

    dp = rules.get("detail_page", {})
    dp_count = dp.get("count", "0")
    if dp_count and dp_count != "0":
        parts.append(f"\n### 详情页：{dp_count}张")
        if dp.get("note"):
            parts.append(f"- {dp['note']}")
    else:
        parts.append(f"\n### 详情页：{dp.get('note', '无')}")

    return "\n".join(parts)
