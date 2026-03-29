"""
Phase 1/Phase 2 工具定义（v2 意图优先架构）

Phase 1：轻量意图分类（6 个路由工具，~2000 tokens）
Phase 2：按 domain 动态加载（ERP/crawler 工具 + 精简出口）

与 agent_tools.py（v1 全量工具）独立，通过灰度开关切换。
"""

from typing import Any, Dict, List


# ============================================================
# Phase 1：轻量意图分类
# ============================================================


PHASE1_SYSTEM_PROMPT = (
    "你是意图路由器。分析用户消息，调用一个路由工具。\n"
    "- route_chat: 普通对话/问答/写作/代码/分析/翻译\n"
    "- route_erp: 查询ERP数据（订单/库存/商品/售后/采购/物流/仓储/店铺/销量）\n"
    "- route_crawler: 搜索社交平台内容（小红书/抖音/B站/微博/知乎）\n"
    "- route_computer: 操作文件（读取/写入/搜索/分析文件，编写代码处理数据）\n"
    "- route_image: 要求生成/画/绘制/编辑图片（包括口语：搞张图/来个图/出个图）\n"
    "- route_video: 要求生成/制作视频\n"
    "- ask_user: 完全无法判断意图时才追问\n\n"
    "## ERP 判定规则（宽进严出，宁可进ERP让Phase2处理）\n"
    "含以下关键词 → route_erp：\n"
    "订单/库存/发货/退货/退款/采购/物流/快递/销量/销售额/成交/"
    "多少单/多少件/待发货/待审核/盘点/调拨/出库/入库/供应商/"
    "采购单/售后/工单/补发/换货/仓库/店铺\n"
    "含电商平台名(淘宝/天猫/京东/拼多多/抖店/小红书店铺/1688)"
    "且涉及订单/销量/发货/退款 → route_erp（不是 crawler）\n"
    "含「我的订单」「查个单」「丁单」（错字）→ route_erp\n\n"
    "## 文件操作判定规则\n"
    "含以下关键词 → route_computer：\n"
    "读取文件/打开文件/查看文件/文件内容/写入文件/保存文件/创建文件/"
    "搜索文件/查找文件/文件列表/目录/文件夹/"
    "分析数据/处理CSV/处理Excel/整理文件/文件大小/文件信息\n"
    "用户上传了文件或提到 workspace 中的文件 → route_computer\n\n"
    "## 其他规则\n"
    "普通搜索(天气/新闻)用 route_chat + needs_search=true，"
    "社交平台搜索(口碑/推荐/评测)用 route_crawler。\n"
    "用户说「重新生成」「再来一张」等，"
    "查看历史记录中的生成类型，调对应的 route_image/route_video。\n"
    "信息不足但能推断大方向时，优先路由到对应域，不要轻易 ask_user。"
)

PHASE1_TOOL_TO_DOMAIN: Dict[str, str] = {
    "route_chat": "chat",
    "route_erp": "erp",
    "route_crawler": "crawler",
    "route_computer": "computer",
    "route_image": "image",
    "route_video": "video",
    "ask_user": "ask_user",
}


def _build_ask_user_tool() -> Dict[str, Any]:
    """Phase 1/2 共用的 ask_user 工具"""
    return {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "无法判断意图或信息不足时追问",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "追问内容（带选项引导用户）",
                    },
                    "reason": {
                        "type": "string",
                        "enum": ["need_info", "out_of_scope"],
                        "description": (
                            "need_info=信息不足, "
                            "out_of_scope=超出能力"
                        ),
                    },
                },
            },
        },
    }


def build_phase1_tools() -> List[Dict[str, Any]]:
    """Phase 1 轻量意图分类工具（6 个路由工具，~2000 tokens）

    与 v1 AGENT_TOOLS 的区别：
    - 无 ERP/crawler/code 具体工具（Phase 2 按需加载）
    - 无 model enum（由 model_selector 规则选模型）
    - 无 web_search/search_knowledge 等信息工具
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "route_chat",
                "description": (
                    "普通对话/问答/写作/代码/分析/翻译等文本交互"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_prompt": {
                            "type": "string",
                            "description": "角色设定(一句话)",
                        },
                        "brand_hint": {
                            "type": "string",
                            "description": (
                                "用户指定的模型品牌"
                                "(如claude/gpt/deepseek)"
                            ),
                        },
                        "needs_code": {
                            "type": "boolean",
                            "description": "用户需要写代码或技术问题",
                        },
                        "needs_reasoning": {
                            "type": "boolean",
                            "description": "需要深度推理/数学/逻辑",
                        },
                        "needs_search": {
                            "type": "boolean",
                            "description": "需要搜索实时信息",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "route_erp",
                "description": (
                    "查询ERP数据：订单/库存/商品/售后/"
                    "采购/物流/仓储/销量/店铺/平台对比"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_prompt": {
                            "type": "string",
                            "description": "角色设定",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "route_crawler",
                "description": (
                    "搜索社交平台内容/口碑/推荐/评测"
                    "(小红书/抖音/B站/微博/知乎)"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "platform_hint": {
                            "type": "string",
                            "description": (
                                "目标平台(xhs/dy/bili/wb/zhihu)"
                            ),
                        },
                        "keywords": {
                            "type": "string",
                            "description": "搜索关键词",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "route_computer",
                "description": (
                    "用户要求操作本地文件：读取/写入/搜索/分析文件，"
                    "处理数据（CSV/Excel/JSON），整理目录等"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_prompt": {
                            "type": "string",
                            "description": "角色设定",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "route_image",
                "description": (
                    "用户要求生成/画/绘制/编辑图片"
                    "（包括口语：搞张图/来个图/出个图/给我个图）"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompts": {
                            "type": "array",
                            "description": "英文图片提示词列表",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "description": (
                                "宽高比(1:1/9:16/16:9/3:4/4:3)"
                            ),
                        },
                        "needs_edit": {
                            "type": "boolean",
                            "description": "用户要编辑已有图片",
                        },
                        "needs_hd": {
                            "type": "boolean",
                            "description": "用户要求高清/4K",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "route_video",
                "description": "用户明确要求生成/制作视频",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "英文视频提示词",
                        },
                        "needs_pro": {
                            "type": "boolean",
                            "description": "用户要求专业级/电影级",
                        },
                    },
                },
            },
        },
        _build_ask_user_tool(),
    ]


# ============================================================
# Phase 2：按 domain 动态加载
# ============================================================


BASE_AGENT_PROMPT = (
    "你是工具编排引擎。根据用户需求调用工具采集数据，"
    "采集完毕后调 route_to_chat 汇总回复用户。\n"
    "你不直接回答用户问题，必须通过工具获取数据后再汇总。\n"
    "对话记录中的信息可以直接用于填充工具参数。\n\n"
    "## 退出规则（严格遵守）\n"
    "- 数据采集完毕 → 调 route_to_chat\n"
    "- 信息不足无法查询 → 调 ask_user（不要用纯文本回复）\n"
    "- 禁止不调工具直接回复文本，必须通过工具退出循环\n\n"
)


def _build_phase2_route_to_chat_tool() -> Dict[str, Any]:
    """Phase 2 出口工具 — 只有 system_prompt，无 model 选择"""
    return {
        "type": "function",
        "function": {
            "name": "route_to_chat",
            "description": "数据采集完毕，汇总回复用户。",
            "parameters": {
                "type": "object",
                "properties": {
                    "system_prompt": {
                        "type": "string",
                        "description": (
                            "适合当前回复的角色设定（一句话）"
                        ),
                    },
                },
                "required": ["system_prompt"],
            },
        },
    }


def build_domain_tools(domain: str) -> List[Dict[str, Any]]:
    """按 domain 动态构建 Phase 2 工具列表

    仅 erp/crawler 需要 Phase 2 工具循环。
    chat/image/video 在 Phase 1 已完成路由，返回空列表。
    """
    from config.code_tools import build_code_tools
    from config.crawler_tools import build_crawler_tools
    from config.erp_tools import build_erp_search_tool, build_erp_tools
    from config.file_tools import build_file_tools

    builders: Dict[str, Any] = {
        "erp": lambda: [
            *build_erp_tools(),
            build_erp_search_tool(),
            *build_code_tools(),
            _build_phase2_route_to_chat_tool(),
            _build_ask_user_tool(),
        ],
        "crawler": lambda: [
            *build_crawler_tools(),
            _build_phase2_route_to_chat_tool(),
            _build_ask_user_tool(),
        ],
        "computer": lambda: [
            *build_file_tools(),
            *build_code_tools(),
            _build_phase2_route_to_chat_tool(),
            _build_ask_user_tool(),
        ],
    }
    builder = builders.get(domain)
    return builder() if builder else []


def build_domain_prompt(domain: str) -> str:
    """按 domain 动态构建 Phase 2 系统提示词

    仅 erp/crawler 有 Phase 2 提示词（含领域路由规则）。
    chat/image/video 返回空字符串。
    """
    from config.code_tools import CODE_ROUTING_PROMPT
    from config.crawler_tools import CRAWLER_ROUTING_PROMPT
    from config.erp_tools import ERP_ROUTING_PROMPT
    from config.file_tools import FILE_ROUTING_PROMPT

    prompts: Dict[str, Any] = {
        "erp": lambda: (
            BASE_AGENT_PROMPT
            + ERP_ROUTING_PROMPT
            + CODE_ROUTING_PROMPT
        ),
        "crawler": lambda: (
            BASE_AGENT_PROMPT + CRAWLER_ROUTING_PROMPT
        ),
        "computer": lambda: (
            BASE_AGENT_PROMPT
            + FILE_ROUTING_PROMPT
            + CODE_ROUTING_PROMPT
        ),
    }
    builder = prompts.get(domain)
    return builder() if builder else ""


# ============================================================
# 模块级常量
# ============================================================

PHASE1_TOOLS = build_phase1_tools()
