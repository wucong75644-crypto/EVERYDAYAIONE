"""The planning entry point is a normal Policy-gated chat tool."""
from ..spec import Exposure, ToolAvailability, ToolPolicyRules, ToolSpec


def _planner_schema():
    return {"type":"function","function":{"name":"plan_ecommerce_images",
        "description":("按用户需求调用服务器策划服务，内部串行执行商品卖点、整套视觉规范、逐图完整执行稿三阶段。"
            "用户原话由服务器从本轮真实消息直接读取，不能总结或重写。首次策划传references，必须按用户图片顺序标明role；商品图使用product/商品图，风格参考使用style_reference/风格参考。回答needs_input时传continue_plan_id和image_count（仅用户改了张数时填写），服务器沿用已保存的原图及其顺序。两种调用不能同时传。"
            "风格有要求时保留原文并按要求补充，没要求时依商品定位设计。默认10张，允许1至15。"
            "不要在本工具前自行拆卖点或prompt。ready返回plan_id/revision与逐图item_id；随后按顺序为每项调用generate_image的plan_source。"
            "needs_input时把问题直接问用户；insufficient时解释资料限制；只策划请求不要生成图片。"),
            "parameters":{"type":"object","additionalProperties":False,
        "oneOf":[{"required":["references"]},{"required":["continue_plan_id"]}],"properties":{
            "continue_plan_id":{"type":"string","minLength":36,"maxLength":36},
            "references":{"type":"array","minItems":1,"maxItems":16,"items":{
                "type":"object","additionalProperties":False,"required":["role"],
                "properties":{"resource_ref":{"type":"string"},"file_id":{"type":"string"},
                    "asset_id":{"type":"string"},"message_id":{"type":"string"},
                    "content_index":{"type":"integer","minimum":0},
                    "role":{"type":"string","minLength":1,"maxLength":200,
                        "description":"明确标注product/商品图、detail/商品细节或style_reference/风格参考；至少一张商品图用于确定画布，风格图只影响视觉方向。"}},
                "oneOf":[{"required":[key]} for key in ("resource_ref","file_id","asset_id","message_id")],
                "dependentRequired":{"message_id":["content_index"]}}},
            "source_message_ids":{"type":"array","maxItems":8,"items":{"type":"string"},
                "description":"仅选定确需沿用的历史用户文字消息ID；当前消息由服务端读取。"},
            "image_count":{"type":"integer","minimum":1,"maximum":15},
            "task_type":{"type":"string","enum":["main_images"]}},
        }}}


def build_specs():
    return (ToolSpec(name="plan_ecommerce_images",capability="platform.plan_ecommerce_images",
        schema=_planner_schema(),domain="general",
        availability=ToolAvailability(requires_personal_context=True,
            feature_flags=("ecom_image_planning_enabled","chat_image_async_enabled")),
        risk_level="safe",parallelizable=False,cacheable=False,effects=("unknown",),
        executor_type="legacy",handler_key="plan_ecommerce_images",exposure=Exposure.PUBLIC,
        source="services.tools.definitions.ecommerce_planner.build_specs",definition_kind="explicit",
        catalog_order=28,catalog_groups=("common_tools",),core=True,legacy_plan_visible=False,
        compatibility_notes=(),policy_rules=ToolPolicyRules(operation="analysis",plan_allowed=False,
            execution_modes=("interactive",),action_rule=None,required_permissions=())),)
