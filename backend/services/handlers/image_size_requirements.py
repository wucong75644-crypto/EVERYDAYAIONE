"""Resolve size intent separately from product analysis, before image acceptance."""
from copy import deepcopy
from math import log
import re

from services.handlers.image_dimensions import ratio_for

IMAGE_SIZE_OPTIONS = ('aspect_ratio', 'resolution', 'size_requirement')
IMAGE_DRAFT_OPTIONS = (*IMAGE_SIZE_OPTIONS, 'output_format')

SIZE_GUIDANCE = (
    "图片输出尺寸以平台图片工具规格为准。用户未指定的新创作默认auto、1K，上传参考图不等于要求沿用其比例；"
    "修改图片时继承实际编辑目标的画布，把真实宽高匹配到最接近的支持比例。"
    "明确指定画布时用size_requirement={mode:inherit_reference,reference_index:零基索引}；"
    "inherit_reference是size_requirement.mode，不是aspect_ratio的取值。"
    "参考图可有不同尺寸；画布来源与其他执行参考独立，不能为确定比例删除风格参考。reference_index是执行参考图中的零基索引。"
    "auto是新创作的正常缺省策略，无需额外授权；用户明确尺寸和编辑目标画布优先。只改比例保留已选图的分辨率档，只改分辨率保留比例。"
    "用户给出的比例填aspect_ratio（如1:1），分辨率档填resolution（如1K）。产品比例只描述主体造型。"
    "原图宽高仅是素材事实，不能写成输出像素。仅用户确实要求精确像素时用size_requirement.width/height，"
    "发现不支持或冲突时在提交前说明。用画面正文描述构图，尺寸规格仅在已确定后使用单独参数。"
)


def image_draft_options_schema(native_properties=None):
    """Image-specific authoring schema, separate from historical ContentItem storage."""
    if native_properties is None:
        from services.tools.catalog import build_tool_catalog
        native_properties = build_tool_catalog().require('generate_image').to_schema()['function']['parameters']['properties']
    from services.adapters.kie.configs import IMAGE_MODEL_CONFIGS
    from services.handlers.chat_image_request import default_chat_image_model
    ratios = dict.fromkeys(ratio for mode in ('text_to_image', 'image_to_image')
                          for ratio in IMAGE_MODEL_CONFIGS[default_chat_image_model(mode)]['supported_sizes'])
    fields = {key: deepcopy(native_properties[key]) for key in IMAGE_DRAFT_OPTIONS}
    fields['aspect_ratio'].update(enum=list(ratios), description='输出画布比例；新创作未指定时用auto，编辑继承目标图。参考图片之间无需统一比例。')
    fields['resolution']['description'] = '输出清晰度档位，默认1K。使用已选成图修改时可沿用其已确认档位。'
    fields['size_requirement']['dependentRequired'] = {'width': ['height'], 'height': ['width']}
    return {'type': 'object', 'additionalProperties': False, 'properties': fields, 'description': SIZE_GUIDANCE}


def nearest_reference_ratio(reference, supported):
    width, height = reference.get('width'), reference.get('height')
    if not (type(width) is int and type(height) is int and width > 0 and height > 0):
        try:
            width, height = map(int, (reference.get('aspect_ratio') or '').split(':'))
        except (TypeError, ValueError):
            width = height = 0
    if width <= 0 or height <= 0:
        raise ImageSizeError("IMAGE_DIMENSIONS_UNAVAILABLE", "原图尺寸未读取成功；请指定比例或重新选择图片。")
    ratio = ratio_for(width, height)
    if ratio in supported:
        return ratio
    candidates = [(value, *map(int, value.split(':'))) for value in supported if value != 'auto']
    same_direction = [(value, w, h) for value, w, h in candidates if (width - height) * (w - h) >= 0]
    return min(same_direction or candidates, key=lambda item: abs(log(width / height) - log(item[1] / item[2])))[0]


def normalize_approximate_pixels(intent, config):
    """Map explicitly approximate user pixels to native ratio/K parameters."""
    if not intent.get('approximate_pixels') or 'width' not in intent:
        return intent
    value=dict(intent)
    width,height=value.pop('width'),value.pop('height')
    value.update(requested_width=width,requested_height=height,
        aspect_ratio=value.get('aspect_ratio') or nearest_reference_ratio(
            {'width':width,'height':height},config['supported_sizes']))
    if not value.get('resolution') and config.get('supports_resolution'):
        value['resolution']=min(config['supported_resolutions'],
            key=lambda tier:abs(log(max(width,height))-log(int(tier[:-1])*1024)))
    return value



class ImageSizeError(ValueError):
    def __init__(self, code, guidance):
        super().__init__(code)
        self.code = code
        self.guidance = guidance
















def resolve_size_requirement(args: dict, references: list[dict], *, intent=None, previous=None, action=None) -> tuple[dict, dict]:
    """Return normalized execution specs plus immutable provenance.

    New creations may use auto; edits inherit the selected actual canvas.
    Legacy single-reference tools retain inheritance. User sizes take priority.
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
    intent=normalize_approximate_pixels(intent,config)
    if intent.get('approximate_pixels'):
        request.pop('width',None);request.pop('height',None)
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
    elif previous.get("aspect_ratio") and previous["aspect_ratio"] != "auto" and mode != "inherit_reference":
        mode, ratio, source = previous.get("mode", "explicit"), previous["aspect_ratio"], "task"
    elif mode == 'inherit_reference' or action == 'edit' and references:
        mode, ratio, source = 'inherit_reference', None, 'reference'
    elif mode == "auto" or args.get("aspect_ratio") == "auto" or previous.get('aspect_ratio') == 'auto':
        mode, ratio, source = "auto", "auto", "task" if previous.get('aspect_ratio') == 'auto' else "model"
    elif mode == 'explicit' and args.get('aspect_ratio'):
        mode, ratio, source = 'explicit', args['aspect_ratio'], 'model'
    elif references and (mode == 'inherit_reference' or action == 'edit' or action is None and len(references) == 1):
        mode, ratio, source = "inherit_reference", None, "reference"
    elif args.get('aspect_ratio') and (not references or action in {'generate', 'write', 'write_then_submit_images'}):
        mode, ratio, source = 'explicit', args['aspect_ratio'], 'model'
    else:
        mode, ratio, source = "auto", "auto", "default"
    reference_index = None
    if mode == "inherit_reference" and ratio is None:
        if not references:
            raise ImageSizeError("IMAGE_CANVAS_REFERENCE_REQUIRED", "沿用参考图需要先选定真实原图。")
        reference_index = request.get("reference_index")
        if locator := intent.get('canvas_reference'):
            def matches(ref):
                if 'workspace_path' in locator:
                    return all(ref.get(key)==value for key,value in locator.items())
                candidates = [ref, {'message_id': ref.get('source_message_id'),
                                    'content_index': ref.get('source_content_index')},
                              *ref.get('occurrences', ())]
                return any(str(value.get('message_id')) == locator['message_id']
                           and value.get('content_index') == locator['content_index'] for value in candidates)
            selected = [index for index, ref in enumerate(references) if matches(ref)]
            if len(selected) != 1:
                raise ImageSizeError('IMAGE_CANVAS_REFERENCE_REQUIRED', '请在执行素材中绑定用户明确指定的编辑底图；保留其余风格参考。')
            reference_index = selected[0]
        if reference_index is None:
            ratios = {ref.get("aspect_ratio") for ref in references}
            if None in ratios:
                raise ImageSizeError("IMAGE_DIMENSIONS_UNAVAILABLE", "原图尺寸未读取成功；请指定比例或重新选择图片，不根据目测推断。")
            if len(ratios) > 1:
                raise ImageSizeError("IMAGE_CANVAS_REFERENCE_REQUIRED", "尚未确定要沿用哪张图片的画布，请绑定实际编辑目标；其余参考图继续保留。")
            reference_index = 0
        if type(reference_index) is not int or not 0 <= reference_index < len(references):
            raise ImageSizeError("IMAGE_CANVAS_REFERENCE_REQUIRED", "画布来源索引无效，请从选定参考图中明确选择。")
        ratio = nearest_reference_ratio(references[reference_index], config["supported_sizes"])
    if ratio not in config["supported_sizes"]:
        raise ImageSizeError("IMAGE_ASPECT_RATIO_UNSUPPORTED", f"当前模型不支持{ratio}；可选比例：{', '.join(config['supported_sizes'])}。等待用户选择，不近似转换。")
    resolution = intent.get("resolution") or previous.get("resolution") or args.get("resolution")
    if resolution is None and config.get("supports_resolution"):
        resolution = "1K"
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
        if source_canvas_fact(prompt, match.start()):
            continue
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
    if intent.get('approximate_pixels'):
        target.update(approximate=True,requested_width=intent['requested_width'],
            requested_height=intent['requested_height'],resolution=resolution)
    if source == "task" or target["resolution_source"] == "task":
        target.update({key: previous[key] for key in ("source_message_id", "source_task_id") if previous.get(key)})
    if reference_index is not None:
        ref = references[reference_index]
        target.update(reference_index=reference_index, reference_sha256=ref.get("content_sha256"),
                      original_width=ref.get("width"), original_height=ref.get("height"))
    return args, target






def source_canvas_fact(text, start):
    """Source-image metadata is data, not a competing output requirement."""
    prefix = text[max(0, start - 20):start]
    return bool(re.search(r'(?:原图|参考图)(?:的)?(?:画布)?\s*$', prefix)
                and not re.search(r'(?:保持|沿用|保留|输出).{0,8}$', prefix))
