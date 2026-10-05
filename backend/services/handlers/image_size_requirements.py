"""Resolve size intent separately from product analysis, before image acceptance."""
from copy import deepcopy
import re

from services.handlers.image_dimensions import ratio_for

RATIO = re.compile(r"(?<![\d:：])([+-]?\d{1,3})\s*[:：]\s*([+-]?\d{1,3})(?![\d:：])")
PIXELS = re.compile(r"(?<!\d)([+-]?\d+)\s*[xX×＊*]\s*([+-]?\d+)(?!\d)")
RESOLUTION = re.compile(r"(?<![\d])(\d+)\s*[kK](?![A-Za-z0-9_])")
SIZE_GUIDANCE = ("图片尺寸是独立生成要求：服务器 canvas 中的宽高是整张原图画布，不是产品形状。"
    "禁止根据笔记本、包装盒等主体的形状推导画布比例。提示词中的产品比例只描述主体造型，"
    "不能作为输出尺寸要求或填入aspect_ratio；输出画布规格单独使用服务器目标尺寸。"
    "当前用户明确规格优先，其次沿用已确定规格；"
    "只改比例保留分辨率，只改分辨率保留比例。参考图任务要求保持比例时，按服务器 canvas 事实生成方案和提示词；"
    "未能读取尺寸时不能目测冒充原图比例。多参考图需要明确画布来源。"
    "尺寸含义不明、像素与比例冲突、接口不支持精确像素时，先解释并等待用户选择，不能偷偷近似或扣图片积分。"
    "自动模式仅在用户允许且没有固定要求时使用。方案、最终提示词与工具参数必须使用同一目标规格。")


class ImageSizeError(ValueError):
    def __init__(self, code, guidance):
        super().__init__(code)
        self.guidance = guidance


def user_size_intent(text: str) -> dict:
    """Read literal dimensions only from user text, never attachments or assistant plans.

    Natural-language intent remains the model's job; literal conflicts cannot be
    bypassed by a model-supplied mode or approximate resolution.
    """
    text = re.sub(r"```[\s\S]*?```", "", text or "")
    text = re.sub(r"(\d{1,3})\s*比\s*(\d{1,3})", r"\1:\2", text)
    numerals = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
    def number(value):
        if "十" in value and len(value) > 1:
            before, after = value.split("十")
            return numerals.get(before, 1) * 10 + numerals.get(after, 0)
        return numerals.get(value, 0)
    text = re.sub(r"([一二三四五六七八九]?十[一二三四五六七八九]?|[一二三四五六七八九])比([一二三四五六七八九]?十[一二三四五六七八九]?|[一二三四五六七八九])",
                  lambda match: f"{number(match[1])}:{number(match[2])}", text)
    # Descriptions of an object's shape or the source file do not specify the
    # output canvas. Only retain their dimensions when explicitly used as output.
    segments = re.split(r"[，。；;\n]", text)
    text = "，".join(segment for segment in segments if not (
        re.search(r"(?:产品|主体|笔记本|包装盒).{0,8}(?:比例|尺寸)|(?:原图|参考图).{0,8}(?:是|为|尺寸)", segment)
        and not re.search(r"生成|输出|画布|改成|改为|沿用|保持", segment)))
    # '原来1:1，改成3:4' is a replacement, not two simultaneous requirements.
    changed = re.split(r"改成|改为|调整为|换成", text)
    if len(changed) > 1:
        text = changed[-1]
    if any(int(w) <= 0 or int(h) <= 0 for w, h in RATIO.findall(text)):
        raise ImageSizeError("IMAGE_SIZE_INVALID", "比例宽高必须为正整数。")
    ratios = {ratio_for(int(w), int(h)) for w, h in RATIO.findall(text) if int(w) and int(h)}
    pixels = {(int(w), int(h)) for w, h in PIXELS.findall(text)}
    pixels.update((int(w), int(h)) for w, h in re.findall(r"宽(?:度)?\s*[:：]?\s*(\d+)\s*(?:像素|px)?\s*[,，、]?\s*高(?:度)?\s*[:：]?\s*(\d+)", text, re.I))
    resolutions = {f"{k}K" for k in RESOLUTION.findall(text)}
    if len(ratios) > 1 or len(pixels) > 1 or len(resolutions) > 1:
        raise ImageSizeError("IMAGE_SIZE_AMBIGUOUS", "本次有多个尺寸要求，请明确这张图片使用哪一个；不自动选择。")
    intent = {}
    if pixels:
        width, height = pixels.pop()
        if width < 1 or height < 1:
            raise ImageSizeError("IMAGE_SIZE_INVALID", "像素宽高必须是正整数。")
        intent.update(width=width, height=height)
        if ratios and next(iter(ratios)) != ratio_for(width, height):
            raise ImageSizeError("IMAGE_SIZE_CONFLICT", "精确像素与宽高比冲突，请明确目标规格。")
    if ratios:
        intent["aspect_ratio"] = ratios.pop()
    if resolutions:
        intent["resolution"] = resolutions.pop()
    if re.search(r"(?:正方形|方图|方形画布)", text) and "aspect_ratio" not in intent:
        intent["aspect_ratio"] = "1:1"
    if re.search(r"(?:比例|尺寸).{0,5}(?:你决定|你来决定|自动|自行|自由)|(?:自动|自行|自由|你决定|你来决定).{0,5}(?:比例|尺寸)", text):
        if ratios or "aspect_ratio" in intent or pixels or "width" in intent:
            raise ImageSizeError("IMAGE_SIZE_CONFLICT", "自动比例与固定尺寸同时出现，请明确本次使用哪一种。")
        intent["mode"] = "auto"
    elif re.search(r"(?:保持|沿用|保留|不改).{0,8}(?:原图|参考图)|(?:原图|参考图).{0,8}(?:比例|尺寸)", text):
        intent["mode"] = "inherit_reference"
    elif re.search(r"(?:保持|沿用|保留|不改).{0,8}比例", text):
        intent["keep_aspect_ratio"] = True
    elif re.search(r"(?<!\d)\d{3,5}\s*(?:尺寸|像素|px)(?!\w)", text, re.I) and not pixels and "width" not in intent:
        raise ImageSizeError("IMAGE_SIZE_AMBIGUOUS", "请明确是正方形像素尺寸，还是长边尺寸。")
    if re.search(r"(?<!\d)\d{3,5}[pP](?![A-Za-z0-9_])", text):
        raise ImageSizeError("IMAGE_EXACT_SIZE_UNSUPPORTED", "当前图片接口不保证指定像素高度，请选择支持的比例与分辨率档位，不自动近似。")
    if re.search(r"(?:长边|短边)\s*[：:]?\s*\d+", text):
        raise ImageSizeError("IMAGE_EXACT_SIZE_UNSUPPORTED", "当前接口不保证精确长边或短边像素，请选择支持的比例与分辨率档位，不自动换算。")
    if re.search(r"(?:尺寸\s*[：:]?\s*\d{3,5}|(?:做|生成)\s*\d{3,5}\s*$)", text) and "width" not in intent:
        raise ImageSizeError("IMAGE_SIZE_AMBIGUOUS", "请明确是正方形像素尺寸，还是长边尺寸。")
    if re.search(r"竖(?:版|图|向(?:画布|封面|海报|图片))|纵向画布", text):
        intent["orientation"] = "portrait"
    elif re.search(r"横(?:版|图|向(?:画布|封面|海报|图片))|宽屏", text):
        intent["orientation"] = "landscape"
    return intent


def resolve_size_requirement(args: dict, references: list[dict], *, intent=None, previous=None) -> tuple[dict, dict]:
    """Return normalized execution specs plus immutable provenance.

    Missing mode on legacy tools does not grant a model permission to change a
    reference canvas. Explicit user ratios override inheritance.
    """
    from services.adapters.kie.configs import IMAGE_MODEL_CONFIGS
    from services.handlers.chat_image_request import default_chat_image_model
    intent = intent or {}
    previous = previous or {}
    args = deepcopy(args)
    request = args.pop("size_requirement", {})
    if not isinstance(request, dict) or set(request) - {"mode", "reference_index", "width", "height"}:
        raise ImageSizeError("IMAGE_SIZE_INVALID", "请使用尺寸模式及可选画布参考索引或完整精确像素宽高。")
    mode = request.get("mode")
    if mode is not None and mode not in {"explicit", "inherit_reference", "auto"}:
        raise ImageSizeError("IMAGE_SIZE_INVALID", "尺寸模式必须为explicit、inherit_reference或auto。")
    model = args.get("model") or default_chat_image_model(args.get("mode"))
    config = IMAGE_MODEL_CONFIGS.get(model)
    if not config:
        raise ValueError("IMAGE_MODEL_UNAVAILABLE")
    if any(key in request or key in intent for key in ("width", "height")):
        width, height = intent.get("width", request.get("width")), intent.get("height", request.get("height"))
        if type(width) is not int or type(height) is not int or width < 1 or height < 1:
            raise ImageSizeError("IMAGE_SIZE_INVALID", "精确尺寸必须同时提供正整数宽高。")
        ratio = intent.get("aspect_ratio") if "width" in intent else args.get("aspect_ratio")
        if ratio and ratio != ratio_for(width, height):
            raise ImageSizeError("IMAGE_SIZE_CONFLICT", "精确像素与比例冲突，请先明确目标规格。")
        # Current KIE adapters expose ratios/K tiers, not guaranteed arbitrary pixels.
        raise ImageSizeError("IMAGE_EXACT_SIZE_UNSUPPORTED", "当前接口不能保证精确像素输出；请告知用户可用比例与1K/2K/4K，并等待选择，不自动近似。")
    if intent.get("keep_aspect_ratio") and not previous.get("aspect_ratio") and not references:
        raise ImageSizeError("IMAGE_SIZE_INTENT_REQUIRED", "没有已确定的画布比例或选定参考图，请明确希望保持哪一份规格。")
    if "aspect_ratio" in intent:
        mode, ratio, source = "explicit", intent["aspect_ratio"], "user"
    elif intent.get("mode") == "auto":
        mode, ratio, source = "auto", "auto", "user"
    elif intent.get("mode") == "inherit_reference":
        mode, ratio, source = "inherit_reference", None, "user"
    elif intent.get("orientation"):
        mode, ratio, source = "explicit", args.get("aspect_ratio"), "user_interpreted"
        if not ratio or ratio == "auto":
            raise ImageSizeError("IMAGE_SIZE_AMBIGUOUS", "用户指定了画布方向，请确定对应支持比例；不能使用auto绕过明确方向。")
        try:
            w, h = map(int, ratio.split(":"))
        except (ValueError, TypeError):
            raise ImageSizeError("IMAGE_SIZE_INVALID", "画布比例格式无效。")
        if (intent["orientation"] == "portrait" and w >= h) or (intent["orientation"] == "landscape" and w <= h):
            raise ImageSizeError("IMAGE_SIZE_CONFLICT", "工具比例与用户指定的横竖方向冲突，请纠正参数。")
    elif previous.get("aspect_ratio"):
        mode, ratio, source = previous.get("mode", "explicit"), previous["aspect_ratio"], "task"
    elif mode == "auto":
        if references:
            raise ImageSizeError("IMAGE_SIZE_INTENT_REQUIRED", "参考图默认保留画布比例；仅在用户允许自动选择时使用auto，不要用auto绕过原图事实。")
        ratio, source = "auto", "model"
    elif references and mode != "explicit":
        mode, ratio, source = "inherit_reference", None, "reference"
    else:
        mode, ratio, source = "explicit", args.get("aspect_ratio", "1:1"), "model"
        # The model may interpret '竖版海报' as an explicit target; it cannot
        # override a selected canvas merely by adding explicit to the tool call.
        if references and not intent.get("orientation"):
            mode, ratio, source = "inherit_reference", None, "reference"
    reference_index = None
    if mode == "inherit_reference" and ratio is None:
        if not references:
            raise ImageSizeError("IMAGE_CANVAS_REFERENCE_REQUIRED", "沿用参考图需要先选定真实原图。")
        reference_index = request.get("reference_index")
        if reference_index is None:
            ratios = {ref.get("aspect_ratio") for ref in references}
            if None in ratios:
                raise ImageSizeError("IMAGE_DIMENSIONS_UNAVAILABLE", "原图尺寸未读取成功；请指定比例或重新选择图片，不根据目测推断。")
            if len(ratios) > 1:
                raise ImageSizeError("IMAGE_CANVAS_REFERENCE_REQUIRED", "多张参考图画布比例不同，请明确哪张作为画布来源；风格参考不自动决定尺寸。")
            reference_index = 0
        if type(reference_index) is not int or not 0 <= reference_index < len(references):
            raise ImageSizeError("IMAGE_CANVAS_REFERENCE_REQUIRED", "画布来源索引无效，请从选定参考图中明确选择。")
        ratio = references[reference_index].get("aspect_ratio")
        if not ratio:
            raise ImageSizeError("IMAGE_DIMENSIONS_UNAVAILABLE", "原图尺寸读取失败，请指定比例或重新选择图片。")
    if ratio not in config["supported_sizes"]:
        raise ImageSizeError("IMAGE_ASPECT_RATIO_UNSUPPORTED", f"当前模型不支持{ratio}；可选比例：{', '.join(config['supported_sizes'])}。等待用户选择，不近似转换。")
    resolution = intent.get("resolution") or previous.get("resolution") or args.get("resolution")
    if resolution is not None and resolution not in config.get("supported_resolutions", ()):
        raise ImageSizeError("IMAGE_RESOLUTION_UNSUPPORTED", f"当前模型支持分辨率：{', '.join(config.get('supported_resolutions', ()))}。请等待用户选择，不自动降级。")
    args.update(aspect_ratio=ratio)
    if resolution is not None:
        args["resolution"] = resolution
    # Only declarations about canvas/output count; a product shape may differ.
    prompt = args.get("prompt", "")
    declarations = []
    pattern = r"(?:宽高比|画布比例|输出比例|aspect[_ ]ratio|比例)\s*[:：为是约]?\s*(\d+\s*[:：]\s*\d+)"
    for match in re.finditer(pattern, prompt, re.I):
        prefix = prompt[max(0, match.start() - 8):match.start()]
        if re.search(r"(?:产品|主体|笔记本|包装盒).{0,4}$", prefix) and not re.search(r"画布|输出", match.group(0)):
            continue
        declarations.append(match.group(1))
    if ratio != "auto" and any(ratio_for(*map(int, re.split(r"[:：]", value))) != ratio for value in declarations):
        raise ImageSizeError("IMAGE_SIZE_PROMPT_CONFLICT", f"服务器已确定输出比例{ratio}；请在系统内纠正方案与提示词尺寸声明，不要求用户重复确认，不提交矛盾参数。")
    declared_resolutions = re.findall(r"(?:分辨率|resolution|清晰度)\s*[:：=为]?\s*(\d+[kK])", prompt, re.I)
    effective_resolution = resolution or ("1K" if config.get("supports_resolution") else None)
    if any(value.upper() != effective_resolution for value in declared_resolutions):
        raise ImageSizeError("IMAGE_SIZE_PROMPT_CONFLICT", f"服务器已确定分辨率{effective_resolution}；请在系统内纠正提示词声明，不重复询问已明确的用户要求。")
    target = {"mode": mode, "aspect_ratio": ratio, "source": source,
              "resolution_source": "user" if intent.get("resolution") else "task" if previous.get("resolution") else "model"}
    if source == "task" or target["resolution_source"] == "task":
        target.update({key: previous[key] for key in ("source_message_id", "source_task_id") if previous.get(key)})
    if reference_index is not None:
        ref = references[reference_index]
        target.update(reference_index=reference_index, reference_sha256=ref.get("content_sha256"),
                      original_width=ref.get("width"), original_height=ref.get("height"))
    return args, target


def size_preflight_notice(text: str, sources: list[dict]) -> str:
    """Give the planning model the same size resolver used at acceptance.

    No task, provider IO or debit. Unselected, mixed canvases are not collapsed.
    Historical fixed specs remain in history when this message changes one axis.
    """
    import json
    try:
        intent = user_size_intent(text)
        if not intent and not sources:
            return ""
        available = [source for source in sources if source.get("available")]
        if not available:
            if not intent:
                return ""
            # A resolution-only update depends on the existing task canvas.
            if set(intent) <= {"resolution", "keep_aspect_ratio"}:
                return "服务器尺寸意图：" + json.dumps(intent, ensure_ascii=False) + "；保留当前任务已确定的比例。"
            _, target = resolve_size_requirement({"mode":"text_to_image","prompt":"size preflight"}, [], intent=intent)
        else:
            refs = [{**source.get("canvas", {}), "role":"reference"} for source in available]
            _, target = resolve_size_requirement({"mode":"image_to_image","prompt":"size preflight"}, refs, intent=intent)
        target["resolution"] = intent.get("resolution") or "沿用当前任务分辨率；无既定规格时使用模型默认分辨率"
        return "服务器目标尺寸预检（方案与提示词须一致，正式执行时重新校验选定引用）：" + json.dumps(target, ensure_ascii=False)
    except ImageSizeError as error:
        return "服务器尺寸预检未通过：" + error.guidance + " 未创建图片任务或预扣图片积分。"


def is_size_instruction(text: str) -> bool:
    """Do not turn historic size questions/examples into persistent task rules."""
    if re.search(r"如果|例如|为什么|是什么意思|解释|区别", text):
        return False
    return bool(re.search(r"生成|输出|改成|改为|调整|保持|沿用|统一|设成|设为|设置|提高|比例.{0,5}(?:你决定|自动)", text)
                or re.fullmatch(r"[\d\s:：×xXkK、，,。.]+", text.strip()))
