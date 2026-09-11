"""media ToolSpec ownership. Business handlers are unchanged."""

from ..spec import Exposure, ToolAvailability, ToolPolicyRules, ToolSpec


def _schema_generate_image():
    return {
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
    }


def _schema_generate_video():
    return {
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
    }


def _schema_image_agent():
    return {
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
    }


def build_specs():
    return (
        ToolSpec(
            name='generate_image', schema=_schema_generate_image(),
            domain='general', availability=ToolAvailability(),
            risk_level='confirm', parallelizable=False, cacheable=False,
            effects=('unknown',), executor_type="legacy", handler_key='generate_image',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.media.build_specs", definition_kind="explicit",
            catalog_order=29, catalog_groups=('common_tools',), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            policy_rules=ToolPolicyRules(
                operation='generation',
                plan_allowed=False,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='generate_video', schema=_schema_generate_video(),
            domain='general', availability=ToolAvailability(),
            risk_level='confirm', parallelizable=False, cacheable=False,
            effects=('unknown',), executor_type="legacy", handler_key='generate_video',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.media.build_specs", definition_kind="explicit",
            catalog_order=30, catalog_groups=('common_tools',), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            policy_rules=ToolPolicyRules(
                operation='generation',
                plan_allowed=False,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='image_agent', schema=_schema_image_agent(),
            domain='general', availability=ToolAvailability(),
            risk_level='confirm', parallelizable=False, cacheable=False,
            effects=('unknown',), executor_type="legacy", handler_key='image_agent',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.media.build_specs", definition_kind="explicit",
            catalog_order=31, catalog_groups=('common_tools',), core=True, legacy_plan_visible=False,
            compatibility_notes=(),
            policy_rules=ToolPolicyRules(
                operation='generation',
                plan_allowed=False,
                execution_modes=('interactive', 'scheduled'),
                action_rule=None,
                required_permissions=(),
            ),
        ),
    )
