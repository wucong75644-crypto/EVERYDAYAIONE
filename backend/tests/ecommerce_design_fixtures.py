"""Professional content fixtures for the v3 protocol; no remote model calls."""
from copy import deepcopy
from uuid import uuid4

from services.agent.image.ecommerce_planner.contracts import VISUAL_SECTIONS


def design_fixture(count=1, task_type="main_images", ref_count=1):
    refs = [{"asset_id": str(uuid4()), "role": "product" if i < 2 else "style_reference"} for i in range(ref_count)]
    refs = [{**ref, "source_id": ref["asset_id"]} for ref in refs]
    raw = '这个是存钱本\n要求：发财的感觉；保留原文“只进不出·聚财守业”\n不要改商品颜色。'
    messages = [{"message_id": str(uuid4()), "parts": [{"content_index": 0, "text": raw}]}]
    snapshot = {"image_count": count, "task_type": task_type, "platform": "淘宝", "language": "中文（简体）",
        "references": refs, "messages": messages, "target_size": {"aspect_ratio": "3:4", "resolution": "2K"}}
    product = {"schema_version": "product-selling-points.v3", "status": "ready",
        "product": {"name": "存钱本", "category": "储蓄本", "use": "存钱", "sale_scope": None, "identity_status": "clear", "fact_ids": ["f1"]},
        "facts": [{"id": "f1", "statement": "商品有红色封面及金色龙纹。", "status": "usable", "variant_ids": [],
            "sources": [{"source_ref": "image_1", "kind": "image_observation", "locator": "封面正面"}]}],
        "selling_points": [{"id": "s1", "title": "红金聚财风", "priority": "primary", "buyer_need": "吉祥外观", "buyer_need_basis": "provided",
            "scenario": "商品展示", "feature": "红色封面与金色龙纹", "mechanism": None, "benefit": "呈现吉祥视觉主题", "benefit_basis": "direct",
            "fact_ids": ["f1"], "variant_ids": [], "claim_boundary": ["只表达视觉寓意，不承诺财富效果"]}],
        "questions": [], "gaps": []}
    visual = "\n\n".join(f"## {index}. {name}\n红金聚财的新中式视觉；商品固有配色、原印刷和比例保留。" for index, name in enumerate(VISUAL_SECTIONS, 1))
    review = {"object": "整组", "method": "self_check", "checks": ["原图比例与事实边界", "当前用途与内容任务"],
        "conclusion": "pass", "evidence": "image_1红色封面及上游f1，用户指定发财视觉风格。", "impact": "无明确冲突",
        "attribution": "无", "repair": "无", "recheck_scope": "规划自检，未生成图片"}
    image = {"position": 1, "name": "红金聚财", "purpose": "展示商品原有红金图案",
        "fact_ids": ["f1"], "reference_usage": [{"number": 1, "usage": "使用完整封面视图锁定外形与印刷，背景可替换。"}],
        "scene": "深酒红低纹理背景与暖棕木桌，暖米金留白衬托真实红色商品。",
        "layout": "商品原始正面视图置于中央偏下，等比例缩放，封面边缘与搭扣清楚；不新增未知背面。",
        "props_and_decoration": "少量金色祥云线置于背景右上，呼应封面且不遮挡商品；无新增实体道具。",
        "lighting": "左上大面积柔光覆盖商品与桌面，右侧自然暗部、底部接触阴影；金色高光保留细节，红色不过曝。",
        "text_layout": "主标题“红金聚财”分为一行，出现一次，稳重书写字形、暖金色，置于上方留白居中；商品原印刷保留，不添加其他文字。",
        "product_preservation": "保留原图红色封面、金色龙纹、祥云、搭扣与原印刷；只允许改背景、外部文字、位置和照明。",
        "execution_constraints": "商品优先，龙纹和搭扣完整可辨；只表达吉祥视觉寓意，不宣称实际招财，不虚构烫金、耐用或可换内芯。",
        "negative_additions": "避免标题压住龙纹、过曝丢失封面细节或出现未知商品背面。", "page_link": None}
    detail = task_type == "detail_page"
    images = []
    for position in range(1, count + 1):
        draft = {**deepcopy(image), "position": position, "name": f"红金聚财{position}", "purpose": f"展示任务{position}：封面图案的商品识别与视觉寓意"}
        if detail:
            draft["page_link"] = {"content_task": f"本屏解释封面可见元素{position}", "previous": "开篇" if position == 1 else "承接前屏整体识别",
                "next": "收束" if position == count else "后屏解释可见细节", "edge_treatment": "关键图案与文字在本屏内完整，非关键背景自然延伸，不跨屏补画商品。"}
        images.append(draft)
    page = {"reading_strategy": "先识别商品，再解释可见依据，最后收束已知选择信息。", "visual_rhythm": "红金配色与书写字形贯穿，各屏留白支持连续阅读。",
        "coverage": [{"question": "封面有什么视觉特点", "fact_ids": ["f1"], "positions": list(range(1, count + 1))}],
        "boundaries": [{"after_position": i, "transition": "延续红金视觉与留白，下一屏从整体转为解释细节；不要求像素对齐。"} for i in range(1, count)],
        "generation_path": "independent_screens"} if detail else None
    output = {"schema_version": "ecom-design.v3", "status": "ready", "questions": [], "page_plan": page, "images": images, "review_records": [review]}
    return output, {"input_snapshot": snapshot, "product_selling_points": product, "visual_direction": visual}, refs
