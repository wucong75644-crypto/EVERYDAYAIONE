"""
通用工具定义（非 ERP 直接查询）

为 Agent Loop 提供 erp_agent/erp_analyze/search/media/scheduled_task 等
顶层工具的 OpenAI function calling schema。
由 chat_tools.py 导入合并。
"""

from typing import Any, Dict, List


def _build_erp_agent_description() -> str:
    """从 ERPAgent.build_tool_description() 自动生成描述（运行时调用）。"""
    from services.agent.erp_agent import ERPAgent
    return ERPAgent.build_tool_description()


def build_common_tools() -> List[Dict[str, Any]]:
    """构建通用工具（非 ERP 直接查询）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "erp_agent",
                "description": _build_erp_agent_description(),
                "parameters": {
                    "type": "object",
                    "required": ["task"],
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": (
                                "用户本次查询的完整描述。写法：复述用户原话，只做两处替换——"
                                "时间词→具体日期（如'今天'→'2026-05-03 00:00~15:30'），"
                                "指代词→具体名称。其他一字不动，不要添加额外说明。"
                                "e.g. '2026-05-02 00:00~23:59 淘宝退货按店铺统计'；"
                                "'导出2026-04-28~2026-05-03的订单明细'"
                            ),
                        },
                        "conversation_context": {
                            "type": "string",
                            "description": (
                                "追问时传上轮的查询条件（时间范围/平台/对象/筛选条件），"
                                "让专家理解上文。不传结果数字，不传你的推测。首轮不传。"
                                "e.g. '上轮查了2026-05-02淘宝退货，按店铺分组'"
                            ),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "erp_analyze",
                "description": (
                    "ERP 查询任务拆解——只分析不执行，不查数据库不调 API，毫秒级返回。"
                    "将复杂查询拆解为多步计划（数据域、参数、步骤依赖）。"
                    "仅计划模式下使用：分析后展示方案，等用户确认后再执行，不要分析完直接调 erp_agent。"
                    "不要用于：参数已明确的查询 → 直接调 erp_agent；非 ERP 分析 → code_execute。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["task"],
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": (
                                "用户的完整查询原文，不要拆分或改写。"
                                "e.g. '对比上月和本月各平台退货率，找出退货率上升最多的平台'"
                            ),
                        },
                        "conversation_context": {
                            "type": "string",
                            "description": (
                                "对话背景补充（可选）。追问时传上轮的查询条件，首轮不传"
                            ),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "erp_api_search",
                "description": (
                    "搜索 ERP API 文档的语义搜索工具。"
                    "不确定用哪个工具、action 或参数格式时先调此工具，返回结果可直接用于下一步调用。"
                    "支持关键词（如'退货'）和精确查询（如'erp_trade_query:order_list'）。"
                    "不要用于：查询实际数据 → erp_agent；搜索知识库 → search_knowledge。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "搜索关键词或 tool:action 精确查询。"
                                "e.g. '退货'、'库存盘点'、'erp_trade_query:order_list'"
                            ),
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
                    "搜索企业知识库，查找业务规则、操作流程、SOP、历史经验、"
                    "培训文档等非数据类信息。基于语义相似度检索，返回最相关的文档片段。\n\n"
                    "返回：匹配的文档片段列表（含来源和相关度），无匹配时返回空列表。\n\n"
                    "不要用于：查询业务数据（订单/库存/销售额）→ erp_agent；"
                    "查询实时信息（天气/新闻）→ web_search；"
                    "查看具体文件内容 → file_read。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "搜索关键词或自然语言问题。"
                                "e.g. '退货流程'、'新员工入职操作指南'、'淘宝发货超时规则'"
                            ),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "搜索互联网获取实时公开信息：天气、新闻、行业资讯、"
                    "政策法规、技术文档、公司公开信息等。\n\n"
                    "返回：基于 Google Search 的搜索结果摘要（含来源URL引用），"
                    "回答中会标注信息来源。无结果时返回空。\n\n"
                    "不要用于：查询企业内部业务数据（订单/库存）→ erp_agent；"
                    "查询企业知识库 → search_knowledge；"
                    "爬取社交平台内容（小红书/抖音帖子）→ social_crawler。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "搜索关键词，简洁精准。"
                                "e.g. '杭州今天天气'、'2026年跨境电商政策变化'、'快递停发地区最新通知'"
                            ),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "generate_image",
                "description": (
                    "通用图片生成工具：根据文字描述生成图片（文生图），或基于参考图片生成新图片（图生图）。"
                    "适用于插画、概念图、logo、创意图、头像等非电商场景。"
                    "电商商品图请用 image_agent。\n\n"
                    "两种模式：\n"
                    "- 纯文字 → 文生图（只传 prompt）\n"
                    "- 有参考图 → 图生图（prompt + image_urls，用户上传图片时必传 image_urls）\n\n"
                    "返回：成功 → 图片 URL，前端自动展示。"
                    "失败 → 错误信息，可修改 prompt 后重试。\n\n"
                    "不要用于：电商商品图（白底主图、场景图）→ image_agent；"
                    "视频生成 → generate_video。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "图片描述，英文效果更好。描述主体、风格、构图、色调等。"
                                "e.g. 'A cozy coffee shop interior, warm lighting, watercolor style'；"
                                "'极简风格logo，一只抽象的猫，黑白配色'"
                            ),
                        },
                        "image_urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "参考图片 URL 列表。用户上传了图片并要求画图/改图时必传。"
                                "图生图模式下，生成结果会参考这些图片的风格和内容"
                            ),
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["1:1", "3:4", "4:3", "9:16", "16:9"],
                            "description": (
                                "画面比例。默认 1:1。"
                                "e.g. 头像/logo→1:1, 手机壁纸→9:16, 横幅→16:9"
                            ),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "generate_video",
                "description": (
                    "根据文字描述生成短视频。调用后异步生成，返回 task_id，"
                    "视频完成后自动推送给用户。生成通常需要 1-3 分钟。\n\n"
                    "返回：task_id + 预计等待时间。视频完成后自动展示。\n\n"
                    "不要用于：图片生成 → generate_image / image_agent；"
                    "视频编辑/剪辑 → 不支持。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "视频内容描述，包含场景、动作、风格等。"
                                "e.g. '一只橘猫在阳光下的窗台上伸懒腰，慢动作，温暖色调'"
                            ),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "image_agent",
                "description": (
                    "生成单张电商商品图片（白底主图、场景氛围图、详情页卖点图、SKU 展示图），"
                    "输出符合目标平台规范。每次 1 张，前端自动展示。\n\n"
                    "Guidelines:\n"
                    "- 有 image_task_meta 时按 images[i].description 逐项调用，每张完成后简短确认即可。\n"
                    "- 生成后不要描述图片内容，不要问后续问题，不要提及下载。\n"
                    "- 生成失败时前端自动显示重试按钮，不需要道歉或额外处理。\n"
                    "- image_urls 和 style 由系统自动注入，不需要传这两个参数。\n"
                    "- 非电商画图（插画/logo/创意图）→ 用 generate_image，不要用此工具。\n"
                    "- 局部修图（抠图/换背景）→ 不支持。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["task"],
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": (
                                "单张图片的完整描述。格式：图片类型 尺寸：主体+背景+光线+构图。"
                                "e.g. '白底主图 800×800：运动鞋居中，纯白背景，柔光箱45度布光，自然底部投影'；"
                                "'场景图 750×950：咖啡杯置于原木桌面，背景虚化书房，暖色侧逆光'"
                            ),
                        },
                        "platform": {
                            "type": "string",
                            "enum": ["taobao", "tmall", "jd", "pdd", "douyin", "xiaohongshu"],
                            "description": (
                                "目标电商平台，决定输出尺寸裁切规范。默认 taobao。"
                                "e.g. taobao=800×800主图, jd=800×800, pdd=750×352轮播"
                            ),
                        },
                    },
                },
            },
        },
        # data_query 已合并到 file_read（TECH_file_read统一工具.md）
        {
            "type": "function",
            "function": {
                "name": "manage_scheduled_task",
                "description": (
                    "管理定时任务（自动执行重复性工作，如每日推送报表、定期数据同步）。\n\n"
                    "Actions:\n"
                    "- create: 传 description 自然语言描述任务和频率，返回预填表单供用户确认后创建。\n"
                    "- list: 查看当前任务列表。\n"
                    "- update: 传 task_name + description 描述变更，返回表单供确认。\n"
                    "- pause/resume/delete: 传 task_name（模糊匹配）或 task_id 定位任务。\n\n"
                    "任务不存在时建议用 list 查看现有任务。"
                    "不要用于：一次性数据查询 → erp_agent；手动触发执行 → 不支持。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["action"],
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "list", "update", "pause", "resume", "delete"],
                            "description": "操作类型。create 需配合 description，其余需配合 task_name 或 task_id",
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "create/update 时传：自然语言描述任务内容和频率。"
                                "e.g. '每天早上9点推送销售日报'、'每周一上午10点生成库存周报'"
                            ),
                        },
                        "task_name": {
                            "type": "string",
                            "description": (
                                "任务名称，用于 update/pause/resume/delete。"
                                "e.g. '销售日报推送'"
                            ),
                        },
                        "task_id": {
                            "type": "string",
                            "description": "任务 ID，可传完整 UUID 或前 8 位短 ID",
                        },
                    },
                },
            },
        },
    ]
