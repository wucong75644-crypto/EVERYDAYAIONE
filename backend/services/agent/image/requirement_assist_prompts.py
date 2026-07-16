"""电商图 AI 帮写专用 Prompt。"""

from schemas.ecom_requirement import RequirementAssistInput


SYSTEM_PROMPT = """你是专业电商视觉策略师。本次任务只生成三套通用创作简报，不生成逐图提示词，也不触发生图。

事实优先级：产品图片中可确认的事实 > 用户明确文字 > 平台常规 > 参考图视觉特征 > 合理推断。
产品图只用于确认目标产品事实。参考图只用于背景、构图、色彩、光线、质感、文字排版和详情页节奏；禁止复制参考商品、品牌、Logo、原有文字或专属图形。
无法确认的尺寸、材质、功能和规格必须进入 unclear_items，不得编造。用户要求与产品图冲突时必须进入 conflicts，并提供 blocked_claims；冲突内容不得作为卖点、画面指令或模糊暗示。
三套方案共享同一份产品事实，仅在表达策略上区分：selling_point 卖点直达型、scene 场景氛围型、creative 视觉创意型。

只返回合法 JSON，不要代码围栏，不要新增图片 URL。输出必须符合以下结构：
{
  "product_facts": {"product_name": "产品名称", "confirmed_attributes": ["可确认事实"], "unclear_items": ["待确认信息"]},
  "reference_analyses": [{"image_id": "参考图ID", "primary_uses": ["background|composition|color|lighting|texture|typography|rhythm"], "summary": "可借鉴视觉特征", "excluded_elements": ["参考商品", "品牌", "Logo", "原有文字"]}],
  "conflicts": [{"field": "冲突字段", "user_value": "用户值", "confirmed_value": "产品图确认值", "message": "待用户确认，当前不可作为卖点", "blocked_claims": ["必须拦截的具体说法"]}],
  "suggestions": [{"id": "selling_point|scene|creative", "name": "方案名", "style_name": "风格名", "brief_markdown": "完整中文通用创作简报"}]
}
suggestions 必须恰好三项且 ID 各出现一次。"""


def build_context_prompt(data: RequirementAssistInput) -> str:
    """构造设置快照和图片角色说明，不混入 system 指令。"""
    product_lines = [
        f"- 产品图 {index}：id={image.id}，name={image.display_name}"
        for index, image in enumerate(data.product_images, start=1)
    ]
    reference_lines = [
        f"- 参考图 {index}：id={image.id}，name={image.display_name}"
        for index, image in enumerate(data.reference_images, start=1)
    ] or ["- 未提供参考图"]
    return "\n".join([
        "## 任务设置",
        f"内容类型：{data.content_type}",
        f"目标平台：{data.platform}",
        f"目标语言：{data.language}",
        f"尺寸比例：{data.aspect_ratio}",
        f"清晰度：{data.quality}",
        f"后续生成数量：{data.image_count}",
        "", "## 用户需求原文", data.user_requirement or "未提供",
        "", "## 图片角色", *product_lines, *reference_lines,
        "", "请先核对产品事实和冲突，再输出三套 JSON 方案。",
    ])


def build_multimodal_messages(data: RequirementAssistInput) -> list[dict]:
    """构造明确区分产品图与参考图的 DashScope 多模态消息。"""
    content: list[dict] = [{"type": "text", "text": build_context_prompt(data)}]
    for image in data.product_images:
        content.extend([
            {"type": "text", "text": f"产品图 id={image.id}，只用于确认产品事实："},
            {"type": "image_url", "image_url": {"url": image.original_url}},
        ])
    for image in data.reference_images:
        content.extend([
            {"type": "text", "text": f"参考图 id={image.id}，只用于提取视觉特征："},
            {"type": "image_url", "image_url": {"url": image.original_url}},
        ])
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
