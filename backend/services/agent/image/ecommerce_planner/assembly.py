"""Deterministic display and Image-tool execution from the same creative draft."""
from __future__ import annotations

from copy import deepcopy
import re
from uuid import uuid4

from .contracts import LABELS, source_id, text_hash
from .designs import DesignValidationError, validate_designs

ASSEMBLY_VERSION = "fixed-frame.v3"
FIDELITY = "商品按参考图真实比例等比例缩放，排版围绕实际商品形状安排，不拉伸、压扁、增厚或改变部件比例。"
NEGATIVE = "排除商品拉伸、压扁、增厚、部件比例改变及透视失真；不得编造商品结构、原印刷、材质性能或附赠销售内容。"
CONTENT_FIELDS = ("scene", "layout", "props_and_decoration", "lighting", "text_layout", "product_preservation", "execution_constraints")


def fixed_canvas_conflict(text, snapshot):
    target = snapshot.get("target_size") or {}
    ratios = re.findall(r"(?:画布(?:比例|尺寸)?|输出比例|尺寸比例)\s*(?:为|采用|使用|设置为|：|:)?\s*(\d+\s*:\s*\d+)", text)
    resolutions = re.findall(r"(?:分辨率|清晰度)(?:参数)?\s*(?:为|采用|使用|设置为|：|:)?\s*(\d+[kK])", text)
    return (any(ratio.replace(" ", "") != target.get("aspect_ratio") for ratio in ratios)
        or any(resolution.upper() != target.get("resolution") for resolution in resolutions))


def _check_fixed_conflicts(draft, snapshot):
    issues = []
    for index, image in enumerate(draft["images"]):
        for key in CONTENT_FIELDS:
            if fixed_canvas_conflict(image[key], snapshot):
                issues.append({"path": ["images", index, key], "code": "PLANNER_FIXED_CANVAS_CONFLICT"})
    if issues:
        raise DesignValidationError(issues)


def assemble_designs(value, snapshot, product, visual_direction=None):
    draft = validate_designs(value, snapshot, product)
    if draft["status"] != "ready":
        return draft
    _check_fixed_conflicts(draft, snapshot)
    refs = snapshot["references"]
    if not refs or len({source_id(ref) for ref in refs}) != len(refs):
        raise ValueError("PLANNER_DUPLICATE_REFERENCE_SOURCE")
    target = snapshot["target_size"]
    detail = snapshot.get("task_type") == "detail_page"
    kind = "详情页内容屏" if detail else "电商主图"
    platform = snapshot.get("platform") or "通用电商平台"
    language = snapshot.get("language") or "跟随用户要求"
    task = f"生成一张{kind}。目标平台：{platform}；新增文字语言：{language}；画布比例：{target['aspect_ratio']}；清晰度参数：{target.get('resolution') or '模型默认'}。"
    shared = ""
    if visual_direction is not None:
        from .format_delivery import visual_sections
        shared = ("\n整套共同边界（第二阶段原文）：\n" + visual_sections(visual_direction)[7]
            + "\n在这些共同原则内执行本张已确定的画面；可选建议与变化范围不表示添加全部元素或重新策划。")
    images = []
    from services.handlers.chat_image_request import validate_single_image_request
    for design in draft["images"]:
        usages = {use["number"]: use["usage"] for use in design["reference_usage"]}
        reference_text = "\n".join(f"输入图片{number}—{source_id(ref)}—{ref['role']}—"
            + usages.get(number, "本张不使用此素材内容，不据此新增商品或信息。")
            for number, ref in enumerate(refs, 1))
        content = [(LABELS[0], task + f"\n本张任务：{design['purpose']}。"),
            (LABELS[1], reference_text), (LABELS[2], design["scene"]),
            (LABELS[3], design["layout"]), (LABELS[4], design["props_and_decoration"]),
            (LABELS[5], design["lighting"]), (LABELS[6], design["text_layout"])]
        if detail:
            link = design["page_link"]
            page = draft["page_plan"]
            adjacent = [boundary["transition"] for boundary in page["boundaries"]
                if boundary["after_position"] in {design["position"] - 1, design["position"]}]
            content.append(("上下边界与整页承接", "\n".join([link["content_task"],
                "前屏关系：" + link["previous"], "后屏关系：" + link["next"], link["edge_treatment"],
                "整页阅读：" + page["reading_strategy"], "视觉节奏：" + page["visual_rhythm"], *adjacent,
                "本屏独立生成；不依赖未传入的相邻生成图，不要求精确像素接缝。关键内容在本屏内成立。"])))
        content.extend([(LABELS[7], design["product_preservation"] + "\n" + FIDELITY),
            (LABELS[8], design["execution_constraints"] + shared + "\n只按本稿已确定的画面要求执行；不得新增文案或重新策划。")])
        positive = "\n\n".join(f"【{label}】\n{body}" for label, body in content)
        negative = NEGATIVE + ("\n" + design["negative_additions"] if design["negative_additions"] else "")
        request = positive + "\n\n负面提示词：" + negative
        validate_single_image_request({"mode": "image_to_image", "prompt": request,
            "aspect_ratio": target["aspect_ratio"], "resolution": target.get("resolution")}, len(refs))
        # Display and persistence reuse exact paragraphs; no second LLM rewrite.
        display = (f"## 方案内容\n\n{design['purpose']}\n\n{design['layout']}\n\n{design['text_layout']}"
            f"\n\n## 视觉设定\n\n{design['scene']}\n\n{design['props_and_decoration']}\n\n{design['lighting']}")
        if detail:
            display += "\n\n## 画布与连续关系\n\n" + content[7][1]
        display += ("\n\n## 参考图使用方式\n\n" + reference_text + "\n\n" + design["product_preservation"]
            + "\n\n## 完整生图提示词\n\n" + positive + "\n\n## 负面提示词\n\n" + negative)
        images.append({"item_id": str(uuid4()), "position": design["position"], "name": design["name"],
            "purpose": design["purpose"], "design": deepcopy(design), "references": deepcopy(refs),
            "aspect_ratio": target["aspect_ratio"], "positive_prompt": positive, "negative_prompt": negative,
            "scheme_markdown": display, "request_text": request, "request_text_sha256": text_hash(request),
            "assembly_version": ASSEMBLY_VERSION})
    return {**draft, "images": images, "design_output": draft, "assembly_version": ASSEMBLY_VERSION,
        "generation_validation": "pending", "page_assembly_validation": "pending" if detail else "not_applicable"}


def assemble_prompts(value, snapshot, product, visual_direction):
    from .format_delivery import FormatError, PROMPTS_SCHEMA, schema_paths, prompt_positions, validate_questions, visual_sections
    if 'questions' in value:
        return validate_questions(value)
    paths = schema_paths(value, PROMPTS_SCHEMA)
    if paths:
        raise FormatError('PLANNER_PROMPTS_FIELDS_INVALID', paths)
    bad = prompt_positions(value, snapshot)
    bad += [i for i, body in enumerate(value['prompts'], 1)
        if isinstance(body, str) and fixed_canvas_conflict(body, snapshot)]
    if bad:
        raise FormatError('PLANNER_PROMPT_POSITIONS_INVALID', [('prompts', i - 1) for i in sorted(set(bad))])
    refs, target = snapshot['references'], snapshot['target_size']
    if len({source_id(ref) for ref in refs}) != len(refs):
        raise ValueError('PLANNER_DUPLICATE_REFERENCE_SOURCE')
    detail = snapshot.get('task_type') == 'detail_page'
    kind = '详情页' if detail else '主图'
    legend = '\n'.join(f"image_{i} = 输入图片{i}—{source_id(ref)}—{ref['role']}"
        for i, ref in enumerate(refs, 1))
    shared = visual_sections(visual_direction)[7]
    images = []
    from services.handlers.chat_image_request import validate_single_image_request
    for position, body in enumerate(value['prompts'], 1):
        name = f'{kind}第{position}张'
        positive = (f"【生成任务与画布】\n生成一张电商{kind}；目标平台：{snapshot.get('platform') or '通用电商平台'}；"
            f"新增文字语言：{snapshot.get('language') or '跟随用户要求'}；画布比例：{target['aspect_ratio']}；"
            f"清晰度参数：{target.get('resolution') or '模型默认'}。\n\n【参考图绑定】\n{legend}\n"
            '严格按上述输入顺序识别正文中的image_N；只使用正文指明的素材内容，不自动加入其余图中的商品、道具或信息。'
            f'\n\n【本张完整设计】\n{body}\n\n【整套共同边界】\n{shared}\n'
            '在这些原则内执行本张已确定画面，可选建议不代表加入全部元素或重新策划。'
            f'\n\n【商品保真与执行】\n{FIDELITY}\n只按本稿执行，不额外添加文案。'
            + ('本屏独立生成，不依赖相邻生成图，不要求精确像素接缝。' if detail else ''))
        request = positive + '\n\n负面提示词：' + NEGATIVE
        try:
            validate_single_image_request({'mode': 'image_to_image', 'prompt': request,
                'aspect_ratio': target['aspect_ratio'], 'resolution': target.get('resolution')}, len(refs))
        except ValueError as error:
            raise FormatError('PLANNER_PROMPT_REQUEST_INVALID', [('prompts', position - 1)]) from error
        images.append({'item_id': str(uuid4()), 'position': position, 'name': name, 'purpose': name,
            'design': {'prompt': body}, 'references': deepcopy(refs), 'aspect_ratio': target['aspect_ratio'],
            'positive_prompt': positive, 'negative_prompt': NEGATIVE, 'request_text': request,
            'request_text_sha256': text_hash(request), 'assembly_version': 'fixed-frame.v4',
            'scheme_markdown': '## 完整生图提示词\n\n' + request})
    return {'status': 'ready', 'questions': [], 'images': images, 'review_records': [],
        'design_output': deepcopy(value), 'assembly_version': 'fixed-frame.v4',
        'generation_validation': 'pending', 'page_assembly_validation': 'pending' if detail else 'not_applicable'}
