"""
Agent 工具定义

为 Agent Loop 提供千问 Function Calling 工具定义：
- 信息工具（INFO）：结果回传大脑迭代
- 路由工具（ROUTING）：大脑做出路由决策

工具注册表设计：新增能力 = 注册新工具，引擎无需改动。
"""

from typing import Any, Dict, List, Set

from config.code_tools import (
    CODE_INFO_TOOLS,
    CODE_ROUTING_PROMPT,
    CODE_TOOL_SCHEMAS,
    build_code_tools,
)
from config.crawler_tools import (
    CRAWLER_INFO_TOOLS,
    CRAWLER_ROUTING_PROMPT,
    CRAWLER_TOOL_SCHEMAS,
    build_crawler_tools,
)
from config.erp_tools import (
    ERP_ROUTING_PROMPT,
    ERP_SYNC_TOOLS,
    ERP_TOOL_SCHEMAS,
    build_erp_tools,
    build_erp_search_tool,
)
from config.smart_model_config import (
    SMART_CONFIG,
    _get_model_enum,
    _get_model_desc,
)


# ============================================================
# 工具分类（2 类：信息采集 + 路由决策）
# ============================================================

INFO_TOOLS: Set[str] = {
    "web_search", "get_conversation_context", "search_knowledge",
    "erp_api_search", "erp_identify", "model_search",
} | ERP_SYNC_TOOLS | CRAWLER_INFO_TOOLS | CODE_INFO_TOOLS

ROUTING_TOOLS: Set[str] = {
    "route_to_chat", "route_to_image", "route_to_video", "ask_user",
}

ALL_TOOLS: Set[str] = INFO_TOOLS | ROUTING_TOOLS

# 向后兼容别名（test_kuaimai.py 使用 SYNC_TOOLS）
SYNC_TOOLS = INFO_TOOLS


# ============================================================
# 工具 Schema（用于验证，防止幻觉调用）
# ============================================================

TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    # === 信息工具 ===
    "web_search": {
        "required": ["search_query"],
        "properties": {"search_query": {"type": "string"}},
    },
    "get_conversation_context": {
        "required": [],
        "properties": {"limit": {"type": "integer"}},
    },
    "search_knowledge": {
        "required": ["query"],
        "properties": {"query": {"type": "string"}},
    },
    # === 搜索工具 ===
    "erp_api_search": {
        "required": ["query"],
        "properties": {"query": {"type": "string"}},
    },
    "model_search": {
        "required": ["query"],
        "properties": {"query": {"type": "string"}},
    },
    # === 路由工具 ===
    "route_to_chat": {
        "required": ["system_prompt"],
        "properties": {
            "system_prompt": {"type": "string"},
            "model": {"type": "string"},
            "needs_google_search": {"type": "boolean"},
        },
    },
    "route_to_image": {
        "required": ["prompts"],
        "properties": {
            "prompts": {"type": "array"},
            "model": {"type": "string"},
        },
    },
    "route_to_video": {
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string"},
        },
    },
    "ask_user": {
        "required": ["message", "reason"],
        "properties": {
            "message": {"type": "string"},
            "reason": {"type": "string"},
        },
    },
    # === ERP 工具 ===
    **ERP_TOOL_SCHEMAS,
    # === 爬虫工具 ===
    **CRAWLER_TOOL_SCHEMAS,
    # === 代码执行工具 ===
    **CODE_TOOL_SCHEMAS,
}


def validate_tool_call(tool_name: str, arguments: Dict[str, Any]) -> bool:
    """验证工具调用参数（防止幻觉工具名和缺失必填字段）"""
    if tool_name not in ALL_TOOLS:
        return False
    schema = TOOL_SCHEMAS.get(tool_name)
    if not schema:
        return True
    for req_field in schema.get("required", []):
        if req_field not in arguments:
            return False
    return True


# ============================================================
# 工具定义构建
# ============================================================


def build_agent_tools() -> List[Dict[str, Any]]:
    """动态构建 Agent 工具定义（12个工具：8 INFO + 4 ROUTING）"""
    return [
        # === 信息工具（结果回传大脑） ===
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "搜索互联网获取最新信息。适用于：实时新闻、价格行情、"
                    "最新资讯、事实查证等需要联网才能回答的问题。"
                    "结果会返回给你，你可以用搜索结果继续思考或作为路由决策的参考。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "search_query": {
                            "type": "string",
                            "description": "搜索关键词（尽量简洁准确）",
                        },
                    },
                    "required": ["search_query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_conversation_context",
                "description": (
                    "获取当前对话的最近消息记录，包括文字和图片URL。"
                    "适用于：用户说「用刚才的图片」「之前那个」等需要引用历史内容的场景。"
                    "返回最近的对话记录（包含图片URL），你可以用这些信息继续操作。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "获取最近几条消息（默认10，最大20）",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_knowledge",
                "description": (
                    "查询AI知识库获取历史经验。适用于：需要参考之前的模型表现、"
                    "任务成功/失败经验来做决策时使用。一般不需要主动调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "查询关键词",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        # === ERP 数据查询工具（从 erp_tools.py 导入） ===
        *build_erp_tools(),
        # === 社交媒体爬虫工具（从 crawler_tools.py 导入） ===
        *build_crawler_tools(),
        # === 代码执行沙盒工具（从 code_tools.py 导入） ===
        *build_code_tools(),
        # === 搜索工具（按需发现 API/模型文档） ===
        build_erp_search_tool(),
        {
            "type": "function",
            "function": {
                "name": "model_search",
                "description": (
                    "搜索可用的 AI 模型及其能力。"
                    "当你不确定该选哪个模型时调用此工具。"
                    "支持模型名搜索（如「deepseek」）、"
                    "能力搜索（如「code」「reasoning」）、"
                    "场景搜索（如「写代码」「数学题」「看图」）。"
                    "结果会返回给你，帮你选择最合适的模型。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "模型名、能力标签或场景描述",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        # === 路由工具（大脑做出路由决策） ===
        {
            "type": "function",
            "function": {
                "name": "route_to_chat",
                "description": (
                    "普通对话、问答、分析、翻译、写作、代码等文本交互。"
                    "选择回复模型和角色设定。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_prompt": {
                            "type": "string",
                            "description": "适合当前对话的角色设定（一句话）",
                        },
                        "model": {
                            "type": "string",
                            "enum": _get_model_enum("chat"),
                            "description": _get_model_desc("chat"),
                        },
                        "needs_google_search": {
                            "type": "boolean",
                            "description": (
                                "是否需要模型使用联网搜索能力回答"
                                "（仅在用户问题需要实时信息时设为 true）"
                            ),
                        },
                    },
                    "required": ["system_prompt", "model"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "route_to_image",
                "description": (
                    "用户明确要求生成/画/绘制/修改图片时调用。"
                    "在 prompts 中直接写好英文提示词。1 张=单图，2-8 张=批量。"
                    "不适用：纯文字讨论图片话题（如分析风格、评价构图）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "prompt": {
                                        "type": "string",
                                        "description": "优化后的英文图片提示词",
                                    },
                                    "aspect_ratio": {
                                        "type": "string",
                                        "enum": [
                                            "1:1", "9:16", "16:9", "3:4", "4:3",
                                        ],
                                        "description": (
                                            "宽高比。竖版=9:16，横版=16:9，"
                                            "方形=1:1。默认1:1"
                                        ),
                                    },
                                },
                                "required": ["prompt"],
                            },
                            "description": (
                                "图片提示词列表（1张=单图，2-8张=批量）"
                            ),
                            "minItems": 1,
                            "maxItems": 8,
                        },
                        "model": {
                            "type": "string",
                            "enum": _get_model_enum("image"),
                            "description": _get_model_desc("image"),
                        },
                    },
                    "required": ["prompts", "model"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "route_to_video",
                "description": (
                    "用户明确要求生成/制作/创作视频时调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "优化后的视频生成英文提示词",
                        },
                        "model": {
                            "type": "string",
                            "enum": _get_model_enum("video"),
                            "description": _get_model_desc("video"),
                        },
                    },
                    "required": ["prompt", "model"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ask_user",
                "description": (
                    "当你无法确定用户意图或信息不足时使用此工具。三种场景：\n"
                    "1. 意图模糊：不确定用户想要什么类型的输出\n"
                    "2. 信息不足：用户请求不够具体，需要补充关键信息\n"
                    "3. 超出能力范围：当前系统不支持该功能\n\n"
                    "重要：必须给出具体选项引导用户，禁止开放式提问。\n"
                    "示例（用户上传了图片说「帮我处理一下」）：\n"
                    "  ✗ 错误：「你想对这张图片做什么？」\n"
                    "  ✓ 正确：「我注意到你上传了一张图片，你想要：\n"
                    "    1. 编辑这张图片（裁剪/调整/风格变换）\n"
                    "    2. 用这张图片生成视频\n"
                    "    3. 基于这张图片生成新图片\n"
                    "    4. 只是想聊聊这张图片的内容」\n"
                    "使用此工具后循环自动结束。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "要回复给用户的文字内容（追问问题或能力说明）",
                        },
                        "reason": {
                            "type": "string",
                            "enum": ["need_info", "out_of_scope"],
                            "description": (
                                "need_info=信息不足需追问, "
                                "out_of_scope=超出当前能力"
                            ),
                        },
                    },
                    "required": ["message", "reason"],
                },
            },
        },
    ]


# ============================================================
# Agent 系统提示词
# ============================================================


def build_agent_system_prompt() -> str:
    """构建 Agent 系统提示词（纯路由器：分析 → 决策）"""
    from config.smart_model_config import (
        get_image_to_video_model,
    )

    i2v_model = get_image_to_video_model()
    image_edit_models = [
        m["id"] for m in SMART_CONFIG.get("image", {}).get("models", [])
        if m.get("requires_image")
    ]
    edit_hint = " 或 ".join(image_edit_models) or "图片编辑模型"

    return (
        "你是意图路由器。分析用户消息，调用一个路由工具，"
        "并为该工具选择最合适的 model。\n\n"
        "你不直接回答用户问题，不生成最终内容。"
        "你的职责是分析意图、填好工具参数、做出路由决策。"
        "对话记录中的信息可以直接用于填充工具参数。\n\n"
        "路由规则：\n"
        "- route_to_image：用户明确要求生成/画/绘制/修改图片\n"
        "- route_to_video：用户明确要求生成/制作视频\n"
        "- route_to_chat：其他所有对话（包括需要联网搜索的问题）\n"
        "- ask_user：无法判断用户意图时，带选项询问\n\n"
        "route_to_chat 要点：\n"
        "- 用户问题需要实时信息（天气/新闻/股价等）"
        " → needs_google_search=true，并选支持联网搜索的模型\n"
        "- 其他对话 → needs_google_search 不传或 false\n\n"
        "模型选择：\n"
        "- 根据各 model 参数的 description 和能力标签选择最匹配的模型\n"
        "- 能力标签格式：[capabilities | 图片:✓/✗ | 搜索:✓/✗ | 深度思考:✓/✗]\n"
        f"- 用户有图片且要编辑 → {edit_hint}\n"
        f"- 用户有图片且要做视频 → {i2v_model}\n"
        "- 用户发了图片（非编辑/视频） → 必须选 图片:✓ 的模型\n"
        "- 用户需要实时信息 → 优先选 搜索:✓ 的模型\n"
        "- 用户开启了深度思考模式 → 必须选 深度思考:✓ 的模型\n"
        "- 用户无特殊要求 → 优先选 priority 最高的模型\n\n"
        "模型选择示例：\n"
        "- 用户：「帮我写个Python爬虫」→ model=deepseek-v3.2（code能力强）\n"
        "- 用户：「这道数学题怎么解」→ model=deepseek-r1（reasoning/math）\n"
        "- 用户：[图片]+「这张图片里是什么」→ model=qwen3.5-plus（图片:✓）\n"
        "- 用户：「今天黄金价格多少」→ model=gemini-3-flash + needs_google_search=true（搜索:✓）\n"
        "- 用户：「帮我用Claude分析一下」→ model=anthropic/claude-sonnet-4（用户指定Claude）\n"
        "- 用户开启深度思考 → model=deepseek-r1 或 gemini-3-pro（深度思考:✓）\n"
        "- 不确定选哪个模型时 → 先调 model_search 搜索合适的模型\n\n"
        + ERP_ROUTING_PROMPT
        + CRAWLER_ROUTING_PROMPT
        + CODE_ROUTING_PROMPT
        + "重新生成/修改规则：\n"
        "- 用户说「重新生成」「再来一张」「换一个」「改一下」等，"
        "必须从对话记录中找到上一次生成的提示词（标注为 [图片已生成，使用的提示词: ...]），"
        "在原始提示词基础上进行调整，而不是从零开始写新的提示词。\n"
        "- 用户只提了修改要求（如「大小控制在1m以内」「换个风格」）时，"
        "保留原始提示词的核心描述，仅叠加用户的新要求。\n\n"
        "重要：仅当用户明确要求「生成/画/制作」时才调用生成工具。"
        "讨论、分析、解释等一律用 route_to_chat。\n"
        "禁止直接回复用户，禁止调用不存在的工具。\n"
    )


# 模块级常量（启动时初始化）
AGENT_TOOLS = build_agent_tools()
AGENT_SYSTEM_PROMPT = build_agent_system_prompt()
