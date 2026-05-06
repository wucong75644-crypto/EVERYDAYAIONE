"""
域工具构建函数

按 domain 动态构建工具列表和系统提示词。
供 ERPAgent 内部工具循环使用。
"""

from typing import Any, Dict, List


# ============================================================
# 共用工具
# ============================================================


def _build_ask_user_tool() -> Dict[str, Any]:
    """ask_user 追问工具"""
    return {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                    "主动向用户提问以消除歧义、收集缺失信息或确认操作意图。"
                    "决策原则：猜错一次=用户多等10秒+重新描述需求；"
                    "问一次=用户花3秒选选项。不确定时，选代价小的那个。\n\n"
                    "返回：用户的回复文本。\n\n"
                    "不要用于：已有足够信息的场景（直接执行）；"
                    "纯确认类（'要我继续吗'）— 信息无歧义时直接做。"
                ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": (
                            "追问内容，简洁+带 2-3 个选项引导用户快速选择。"
                            "e.g. '请确认查询范围：1. 全平台 2. 仅淘宝 3. 仅拼多多'"
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "enum": ["need_info", "out_of_scope"],
                        "description": "need_info=信息不足需补充, out_of_scope=超出当前能力范围",
                    },
                },
            },
        },
    }


# ============================================================
# 域工具构建
# ============================================================


BASE_AGENT_PROMPT = (
    "你是工具编排引擎。根据用户需求调用工具采集数据，"
    "采集完毕后用结构化格式（列表/表格）呈现数据，再加一句总结结论。\n"
    "禁止直接回答用户问题，必须通过工具获取数据后再总结。\n"
    "对话记录中的信息可以直接用于填充工具参数。\n\n"
    "## 工具调用规则\n"
    "- 根据工具描述自行判断最合适的工具\n"
    "- 可以多次调用同一工具（不同参数）采集多维数据"
    "（如分别查今天和昨天的数据用于对比）\n"
    "- 可以组合多个工具完成复杂需求\n\n"
    "## 大数据处理规则\n"
    "- 当工具返回 <persisted-output> 标签或 staging 文件引用时，说明数据量过大已存入文件\n"
    "- 用 data_query 查询文件内容：先不传 sql 了解文件结构，再用 SQL 提取所需数据\n"
    "- 禁止直接使用 Preview 中的数据回答用户，Preview 仅供了解数据结构\n"
    "- 多 Sheet Excel：探索模式会返回 Sheet 概览，结构相同时用 sheet=\"*\" 合并读取\n"
    "- 需要复杂计算时，用返回的 [完整数据] 路径在 code_execute 中处理\n\n"
    "## 退出规则\n"
    "- 数据采集完毕 → 直接用文字总结结论回复用户（不需要调 route_to_chat）\n"
    "- 关键参数有歧义（多个合理值）→ 调 ask_user，列出可选条件让用户选择\n"
    "- route_to_chat 仅在需要指定特殊角色时使用，普通场景直接输出文字即可\n\n"
)


def _build_phase2_route_to_chat_tool() -> Dict[str, Any]:
    """出口工具 — 只有 system_prompt，无 model 选择"""
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
                        "description": "适合当前回复的角色设定（一句话）",
                    },
                },
                "required": ["system_prompt"],
            },
        },
    }


def build_domain_tools(domain: str) -> List[Dict[str, Any]]:
    """按 domain 动态构建工具列表

    供 ERPAgent 内部工具循环使用。
    """
    from config.code_tools import build_code_tools
    from config.crawler_tools import build_crawler_tools
    from config.erp_tools import (
        build_erp_search_tool, build_erp_tools, build_fetch_all_pages_tool,
    )
    from config.file_tools import build_file_tools

    # data_query 工具定义（erp/computer 域共用）
    def _get_data_query_tool() -> List[Dict[str, Any]]:
        from config.chat_tools import get_chat_tools
        return [
            t for t in get_chat_tools(org_id=None)
            if t["function"]["name"] == "data_query"
        ]

    builders: Dict[str, Any] = {
        "erp": lambda: [
            *build_erp_tools(),
            build_erp_search_tool(),
            build_fetch_all_pages_tool(),
            *build_code_tools(),
            *_get_data_query_tool(),
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
            *_get_data_query_tool(),
            _build_phase2_route_to_chat_tool(),
            _build_ask_user_tool(),
        ],
    }
    builder = builders.get(domain)
    return builder() if builder else []


def build_domain_prompt(domain: str) -> str:
    """按 domain 动态构建系统提示词

    供 ERPAgent 内部工具循环使用。
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
