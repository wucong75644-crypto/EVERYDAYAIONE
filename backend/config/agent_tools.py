"""
Agent 工具定义

为 Agent Loop 提供千问 Function Calling 工具定义：
- 同步工具（结果回传大脑迭代）
- 异步工具（fire-and-forget 任务）
- 终端工具（结束循环）

工具注册表设计：新增能力 = 注册新工具，引擎无需改动。
"""

from typing import Any, Dict, List, Set

from config.smart_model_config import (
    SMART_CONFIG,
    _get_model_enum,
    _get_model_desc,
)


# ============================================================
# 工具分类
# ============================================================

SYNC_TOOLS: Set[str] = {"web_search", "get_conversation_context", "search_knowledge"}
ASYNC_TOOLS: Set[str] = {"generate_image", "generate_video", "batch_generate_image"}
TERMINAL_TOOLS: Set[str] = {"text_chat", "ask_user", "finish"}
ALL_TOOLS: Set[str] = SYNC_TOOLS | ASYNC_TOOLS | TERMINAL_TOOLS


# ============================================================
# 工具 Schema（用于验证，防止幻觉调用）
# ============================================================

TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
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
    "generate_image": {
        "required": ["prompt", "model"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string"},
            "aspect_ratio": {"type": "string"},
        },
    },
    "generate_video": {
        "required": ["prompt", "model"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string"},
        },
    },
    "batch_generate_image": {
        "required": ["prompts", "model"],
        "properties": {
            "prompts": {"type": "array"},
            "model": {"type": "string"},
        },
    },
    "text_chat": {
        "required": ["system_prompt", "model"],
        "properties": {
            "system_prompt": {"type": "string"},
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
    "finish": {
        "required": [],
        "properties": {"summary": {"type": "string"}},
    },
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
    """从 smart_models.json 动态构建 Agent 工具定义（9个工具）"""
    return [
        # === 同步工具 ===
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "搜索互联网获取最新信息。适用于：实时新闻、价格行情、"
                    "最新资讯、事实查证等需要联网才能回答的问题。"
                    "结果会返回给你，你可以用搜索结果继续思考或作为生成图片/视频的参考。"
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
        # === 异步工具 ===
        {
            "type": "function",
            "function": {
                "name": "generate_image",
                "description": (
                    "生成单张图片。用户明确要求生成/画/绘制/创作/修改图片时调用。"
                    "注意：讨论图片风格、分析设计等不需要调用此工具。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "优化后的图片生成英文提示词",
                        },
                        "model": {
                            "type": "string",
                            "enum": _get_model_enum("image"),
                            "description": _get_model_desc("image"),
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["1:1", "9:16", "16:9", "3:4", "4:3"],
                            "description": (
                                "图片宽高比。竖版/海报=9:16，横版/风景=16:9，方形=1:1。"
                                "未明确要求时默认1:1"
                            ),
                        },
                    },
                    "required": ["prompt", "model"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "generate_video",
                "description": "生成视频。用户明确要求生成/制作/创作视频时调用。",
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
                "name": "batch_generate_image",
                "description": (
                    "批量生成多张不同的图片（2-8张）。"
                    "适用于：用户要求「画5张不同的」「多个角度」「一组系列图」等场景。"
                    "每张图使用不同的提示词，实现多样化输出。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "prompt": {"type": "string"},
                                    "aspect_ratio": {"type": "string"},
                                },
                                "required": ["prompt"],
                            },
                            "description": "每张图片的提示词和可选比例（2-8个）",
                            "minItems": 2,
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
        # === 终端工具 ===
        {
            "type": "function",
            "function": {
                "name": "text_chat",
                "description": (
                    "普通对话、问答、分析、翻译、写作、代码等文本交互时调用。"
                    "这是最终输出工具，调用后将直接回复用户。"
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
                    },
                    "required": ["system_prompt", "model"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ask_user",
                "description": (
                    "当你无法完成任务时使用此工具。两种场景：\n"
                    "1. 信息不足需要追问：用户请求不够具体，需要补充关键信息才能继续\n"
                    "2. 超出能力范围：当前系统不支持该功能，需要诚实说明\n"
                    "使用此工具后会直接回复用户，不再继续执行其他工具。"
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
                            "description": "need_info=信息不足需追问, out_of_scope=超出当前能力",
                        },
                    },
                    "required": ["message", "reason"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": (
                    "当你已经安排了异步任务（生图/生视频），且不需要额外文字回复时调用。"
                    "例如：用户说「画一只猫」→ 你已调用 generate_image → 调用 finish 结束。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "可选的简短说明（不会展示给用户）",
                        },
                    },
                },
            },
        },
    ]


# ============================================================
# Agent 系统提示词
# ============================================================


def build_agent_system_prompt() -> str:
    """构建 Agent 系统提示词（指导多步思考和工具使用）"""
    from config.smart_model_config import (
        DEFAULT_IMAGE_MODEL,
        get_image_to_video_model,
    )

    i2v_model = get_image_to_video_model()
    image_edit_models = [
        m["id"] for m in SMART_CONFIG.get("image", {}).get("models", [])
        if m.get("requires_image")
    ]
    edit_hint = " 或 ".join(image_edit_models) or "图片编辑模型"

    return (
        "你是智能AI助手的大脑。分析用户消息，决定使用哪些工具来完成任务。\n"
        "你可以链式调用多个工具：先搜索信息，再用搜索结果生成图片等。\n\n"
        "## 工具使用规则\n"
        "1. 每次回复必须调用至少一个工具\n"
        "2. 同步工具（web_search/get_conversation_context/search_knowledge）"
        "的结果会返回给你，你可以继续思考\n"
        "3. 异步工具（generate_image/generate_video/batch_generate_image）"
        "是后台任务，调用后用 finish 结束\n"
        "4. 终端工具（text_chat/ask_user/finish）调用后循环结束\n\n"
        "## 模型选择要点\n"
        "- 根据 model 参数的 description 选择最匹配的模型\n"
        f"- 用户有图片且要编辑 → {edit_hint}\n"
        f"- 用户有图片且要做视频 → {i2v_model}\n"
        "- 日常生成图片 → 默认模型即可\n"
        "- 根据各模型 description 自动匹配\n\n"
        "## 何时使用 ask_user\n"
        "- 用户请求模糊（如「帮我做个海报」但没说产品/尺寸/风格）→ ask_user(reason=\"need_info\")\n"
        "- 用户请求超出当前工具能力（如查库存、发邮件）→ ask_user(reason=\"out_of_scope\")\n"
        "- 你不确定用户想要什么类型的输出 → ask_user(reason=\"need_info\")\n\n"
        "## 何时不要使用 ask_user\n"
        "- 你有足够信息完成任务 → 直接用对应工具\n"
        "- 只是缺少非核心细节（如图片尺寸）→ 用合理默认值\n\n"
        "## 重要\n"
        "- 仅当用户明确要求「生成/画/制作」时才调用生成工具\n"
        "- 讨论、分析、解释等一律用 text_chat\n"
        "- 批量生图时每张图写不同的提示词，实现多样化\n"
    )


# 模块级常量（启动时初始化）
AGENT_TOOLS = build_agent_tools()
AGENT_SYSTEM_PROMPT = build_agent_system_prompt()
