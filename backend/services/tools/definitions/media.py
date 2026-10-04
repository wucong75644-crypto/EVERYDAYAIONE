"""media ToolSpec ownership. Business handlers are unchanged."""

from ..spec import Exposure, ToolAvailability, ToolPolicyRules, ToolSpec


def _schema_generate_image_async():
    import json
    from core.config import get_settings
    from services.handlers.chat_image_request import image_capabilities
    limits=get_settings()
    return {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "持久化接受一个独立图片任务，立即返回 submitted、task_id、message_id、排队阶段和服务器预估积分。"
                "接受不表示图片完成；结果随后由独立图片消息展示。每次只生成一张，多张通过多次调用。"
                "明确指定 text_to_image 或 image_to_image；用于分析的图片不自动成为生成参考图。"
                "prompt 是完整最终原文，不再二次改写。只选本次用户指定的原图引用，按用途和顺序传 references。"
                "先看样张时只提交样张。variant_id 是稳定变体身份，不能扩大服务器预算。"
                "遇到素材或提示词版本歧义先询问；失败不自动无限重试。"
                f"本轮上限 {limits.chat_image_max_requests} 张、{limits.chat_image_max_credits} 积分。"
                "当前服务器能力与单张积分：" + json.dumps(image_capabilities(),ensure_ascii=False,separators=(',',':'))
            ),
            "parameters": {
                "type": "object", "additionalProperties": False,
                "required": ["mode", "prompt"],
                "properties": {
                    "mode": {"type":"string", "enum":["text_to_image","image_to_image"]},
                    "prompt": {"type":"string", "minLength":1, "description":"一张图的完整最终提示词原文"},
                    "references": {"type":"array", "maxItems":16, "items": {
                        "type":"object", "additionalProperties":False, "required":["role"],
                        "properties": {
                            "resource_ref":{"type":"string"}, "file_id":{"type":"string"},
                            "asset_id":{"type":"string"}, "message_id":{"type":"string"},
                            "content_index":{"type":"integer","minimum":0},
                            "role":{"type":"string","minLength":1,"maxLength":200},
                        },
                        "oneOf":[{"required":[key]} for key in ("resource_ref","file_id","asset_id","message_id")],
                    }},
                    "model":{"type":"string","description":"可选明确模型；模式和规格必须为该模型实际支持"},
                    "aspect_ratio":{"type":"string"},
                    "resolution":{"type":"string","enum":["1K","2K","4K"]},
                    "output_format":{"type":"string","enum":["png","jpeg","jpg","webp"]},
                    **({"background":{"type":"string","enum":["opaque","transparent"],"description":"仅 Flare 支持；透明输出需保存后验证真实alpha"}} if limits.chat_image_transparent_enabled else {}),
                    "plan_item_id":{"type":"string","minLength":1,"maxLength":200},
                    "variant_id":{"type":"string","minLength":1,"maxLength":200},
                    "source_task_id":{"type":"string"},
                    "source_prompt":{"type":"object","additionalProperties":False,
                        "required":["sha256"],"oneOf":[{"required":["message_id","content_index"]},{"required":["task_id"]}],"properties":{
                            "task_id":{"type":"string"},
                            "message_id":{"type":"string"},"content_index":{"type":"integer","minimum":0},
                            "sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                        }},
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
            name='generate_image', capability='platform.generate_image', schema=_schema_generate_image_async(),
            domain='general', availability=ToolAvailability(requires_personal_context=True, feature_flags=('chat_image_async_enabled',)),
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
            name='generate_video', capability='platform.generate_video', schema=_schema_generate_video(),
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
            name='image_agent', capability='platform.image_agent', schema=_schema_image_agent(),
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
