from __future__ import annotations

import hashlib
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

LABELS = (
    "生成任务与画布", "参考图身份与用途", "背景、桌面与配色", "主体与辅助视图布局",
    "道具与装饰", "光影与构图配合", "文字内容与排版", "商品保真与比例", "执行约束",
)
VISUAL_SECTIONS = ("视觉定位", "背景与场景体系", "色彩配套", "字体与文案视觉体系",
    "场景道具配套", "画面装饰体系", "照明与空间表现", "统一规则与变化范围")
SCHEME_SECTIONS = {
    "方案内容": ("核心主题", "主体元素", "动作或状态", "造型特点", "辅助元素", "背景或填充元素", "文字内容", "文字风格"),
    "视觉设定": ("色彩方案", "视觉媒介", "材质纹理", "构图方式", "主光与补光", "背景光影", "商品光影", "色彩与曝光", "整体氛围"),
    "参考图使用方式": ("保留内容", "替换内容", "尺寸与比例", "输出要求"),
}
SCHEME_HEADINGS = (*SCHEME_SECTIONS, "完整生图提示词", "负面提示词")
SELECTORS = ("resource_ref", "file_id", "asset_id", "message_id", "content_index")


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_id(reference):
    selectors = [key for key in SELECTORS[:3] if key in reference]
    if "message_id" in reference:
        selectors.append("message_id")
    if len(selectors) != 1:
        raise ValueError("PLANNER_REFERENCE_ID_INVALID")
    if selectors[0] == "message_id":
        if type(reference.get("content_index")) is not int or reference["content_index"] < 0:
            raise ValueError("PLANNER_REFERENCE_ID_INVALID")
        return f'{reference["message_id"]}:{reference["content_index"]}'
    return reference[selectors[0]]


def locator(reference):
    return {key: reference[key] for key in SELECTORS if key in reference}


def parse_json(text):
    # Only a complete object is executable; markdown/partial objects trigger repair.
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("PLANNER_JSON_OBJECT_REQUIRED")
    return result


def validate_schema(value, schema, root=None):
    """Validate the vocabulary of the supplied, immutable stage-one schema."""
    root = root or schema
    if "$ref" in schema:
        return validate_schema(value, root["$defs"][schema["$ref"].rsplit("/", 1)[1]], root)
    for branch in schema.get("allOf", []):
        validate_schema(value, branch, root)
    if "if" in schema:
        try:
            validate_schema(value, schema["if"], root)
        except ValueError:
            branch = schema.get("else", {})
        else:
            branch = schema.get("then", {})
        validate_schema(value, branch, root)
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                validate_schema(value, branch, root)
                break
            except ValueError:
                pass
        else:
            raise ValueError("PLANNER_SCHEMA_ANYOF")
    types = {"object": dict, "array": list, "string": str, "null": type(None)}
    if "type" in schema and type(value) is not types[schema["type"]]:
        raise ValueError("PLANNER_SCHEMA_TYPE")
    if "const" in schema and value != schema["const"]:
        raise ValueError("PLANNER_SCHEMA_CONST")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("PLANNER_SCHEMA_ENUM")
    if isinstance(value, dict):
        if set(schema.get("required", [])) - value.keys():
            raise ValueError("PLANNER_SCHEMA_REQUIRED")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise ValueError("PLANNER_SCHEMA_EXTRA")
        for key, child in value.items():
            if key in properties:
                validate_schema(child, properties[key], root)
    if isinstance(value, (str, list)):
        minimum = schema.get("minLength", schema.get("minItems", 0))
        maximum = schema.get("maxLength", schema.get("maxItems", float("inf")))
        if not minimum <= len(value) <= maximum:
            raise ValueError("PLANNER_SCHEMA_LENGTH")
    if isinstance(value, list):
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise ValueError("PLANNER_SCHEMA_DUPLICATE")
        for item in value:
            validate_schema(item, schema.get("items", {}), root)
        if "contains" in schema:
            matches = 0
            for item in value:
                try:
                    validate_schema(item, schema["contains"], root)
                    matches += 1
                except ValueError:
                    pass
            if not schema.get("minContains", 1) <= matches <= schema.get("maxContains", float("inf")):
                raise ValueError("PLANNER_SCHEMA_CONTAINS")


def validate_product(value, schema, snapshot):
    validate_schema(value, schema)
    ids = {}
    for group in ("facts", "selling_points", "questions", "gaps"):
        ids[group] = {item["id"] for item in value[group]}
        if len(ids[group]) != len(value[group]):
            raise ValueError("PLANNER_DUPLICATE_FACT_ID")
    allowed_sources = {source_id(ref) for ref in snapshot["references"]}
    allowed_sources.update(f'{message["message_id"]}:{part["content_index"]}'
        for message in snapshot["messages"] for part in message["parts"])
    for fact in value["facts"]:
        if any(source["source_id"] not in allowed_sources for source in fact["sources"]):
            raise ValueError("PLANNER_UNKNOWN_FACT_SOURCE")
    usable = {item["id"] for item in value["facts"] if item["status"] == "usable"}
    if not set(value["product"]["fact_ids"]) <= ids["facts"]:
        raise ValueError("PLANNER_UNKNOWN_FACT_ID")
    for point in value["selling_points"]:
        if not set(point["fact_ids"]) <= usable:
            raise ValueError("PLANNER_UNSUPPORTED_SELLING_POINT")
    for question in value["questions"]:
        if not set(question["gap_ids"]) <= ids["gaps"]:
            raise ValueError("PLANNER_UNKNOWN_GAP_ID")
    for gap in value["gaps"]:
        if not set(gap["affected_selling_point_ids"]) <= ids["selling_points"]:
            raise ValueError("PLANNER_UNKNOWN_SELLING_POINT_ID")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PlannedImage(StrictModel):
    position: int = Field(ge=1, le=15)
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1)
    scheme_markdown: str = Field(min_length=1)
    references: list[dict] = Field(min_length=1, max_length=16)
    positive_prompt: str = Field(min_length=1)
    negative_prompt: str = Field(min_length=1)
    aspect_ratio: str


class ImageDraft(PlannedImage):
    scheme_markdown: str = Field(min_length=1, description="完整填写方案内容、视觉设定、参考图使用方式三节；正向与负面提示词仅在独立字段中各输出一次，程序补齐展示稿。")


class ReviewRecord(StrictModel):
    object: str = Field(min_length=1)
    method: str = Field(pattern="^self_check$")
    checks: list[str] = Field(min_length=1)
    conclusion: str = Field(pattern="^(pass|blocked|repaired)$")
    evidence: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    attribution: str = Field(min_length=1)
    repair: str = Field(min_length=1)
    recheck_scope: str = Field(min_length=1)


class ImagesOutput(StrictModel):
    """Model-facing JSON Schema for the existing stage-three wire contract."""
    status: Literal["ready", "needs_input"]
    questions: list[str]
    images: list[ImageDraft]
    review_records: list[ReviewRecord]


class ImagesRepairOutput(StrictModel):
    images: list[ImageDraft]
    review_records: list[ReviewRecord]


class ImageValidationError(ValueError):
    def __init__(self, position, error):
        self.position = position
        super().__init__(f"{error}（第{position}张）")


def validate_images(value, snapshot, *, assemble_display=False):
    if set(value) != {"status", "questions", "images", "review_records"}:
        raise ValueError("PLANNER_OUTPUT_FIELDS_INVALID")
    if (not isinstance(value["questions"], list) or not isinstance(value["images"], list)
            or not isinstance(value["review_records"], list)):
        raise ValueError("PLANNER_OUTPUT_TYPES_INVALID")
    if value["status"] == "needs_input":
        if value["images"] or not 1 <= len(value["questions"]) <= 3:
            raise ValueError("PLANNER_QUESTIONS_INVALID")
        if any(not isinstance(q, str) or not q.strip() for q in value["questions"]):
            raise ValueError("PLANNER_QUESTIONS_INVALID")
        return value
    if value["status"] != "ready" or value["questions"] or len(value["images"]) != snapshot["image_count"]:
        raise ValueError("PLANNER_IMAGE_COUNT_INVALID")
    allowed = {source_id(ref): ref for ref in snapshot["references"]}
    if len(allowed) != len(snapshot["references"]):
        raise ValueError("PLANNER_DUPLICATE_REFERENCE_SOURCE")
    images = []
    for position, raw in enumerate(value["images"], 1):
        try:
            images.append(_validate_image(raw, position, allowed, snapshot, assemble_display))
        except ValueError as error:
            raise ImageValidationError(position, error) from error
    records = [ReviewRecord.model_validate(record).model_dump() for record in value["review_records"]]
    if not records or any(record["conclusion"] == "blocked" for record in records):
        raise ValueError("PLANNER_REVIEW_BLOCKED")
    return {**value, "images": images, "review_records": records}


def _validate_image(raw, position, allowed, snapshot, assemble_display):
    image = PlannedImage.model_validate(raw).model_dump()
    if image["position"] != position:
        raise ValueError("PLANNER_IMAGE_ORDER_INVALID")
    if image["aspect_ratio"] != snapshot["target_size"]["aspect_ratio"]:
        raise ValueError("PLANNER_TARGET_SIZE_CONFLICT")
    offsets = [image["positive_prompt"].find(f"【{label}】") for label in LABELS]
    if (any(offset < 0 for offset in offsets) or offsets != sorted(offsets)
            or not image["positive_prompt"].lstrip().startswith(f"【{LABELS[0]}】")
            or any(image["positive_prompt"].count(f"【{label}】") != 1 for label in LABELS)):
        raise ValueError("PLANNER_PROMPT_COLUMNS_INVALID")
    for ordinal, ref in enumerate(image["references"], 1):
        key = source_id(ref)
        if key not in allowed or ref != allowed[key]:
            raise ValueError("PLANNER_REFERENCE_CHANGED")
        required = f"输入图片{ordinal}—{key}"
        if required not in image["positive_prompt"]:
            raise ValueError(f"PLANNER_REFERENCE_ORDER_TEXT_MISMATCH: 第{position}张参考图栏目须逐字包含“{required}”，编号按本张references顺序。")
    headings = re.findall(r"(?m)^##\s+(.+?)\s*$", image["scheme_markdown"])
    if headings != list(SCHEME_HEADINGS) and not (assemble_display and headings == list(SCHEME_SECTIONS)):
        raise ValueError("PLANNER_SCHEME_INCOMPLETE")
    bodies = {}
    for index, title in enumerate(headings):
        start = image["scheme_markdown"].find(f"## {title}") + len(f"## {title}")
        end = image["scheme_markdown"].find(f"## {headings[index + 1]}", start) if index + 1 < len(headings) else len(image["scheme_markdown"])
        bodies[title] = image["scheme_markdown"][start:end].strip()
    for section, fields in SCHEME_SECTIONS.items():
        if not bodies[section] or any(f"**{field}**" not in bodies[section] for field in fields):
            raise ValueError("PLANNER_SCHEME_FIELDS_INCOMPLETE")
    positive_prompt = image["positive_prompt"].strip()
    negative_prompt = image["negative_prompt"].strip()
    if headings == list(SCHEME_SECTIONS):
        image["scheme_markdown"] = (image["scheme_markdown"].rstrip()
            + "\n\n## 完整生图提示词\n\n" + positive_prompt
            + "\n\n## 负面提示词\n\n" + negative_prompt)
    elif bodies["完整生图提示词"] != positive_prompt or bodies["负面提示词"] != negative_prompt:
        raise ValueError("PLANNER_SCHEME_PROMPT_MISMATCH")
    required_ratio = "商品按参考图真实比例等比例缩放，排版围绕实际商品形状安排，不拉伸、压扁、增厚或改变部件比例。"
    if required_ratio not in positive_prompt:
        raise ValueError("PLANNER_PRODUCT_RATIO_RULE_MISSING")
    if any(term not in negative_prompt for term in ("拉伸", "压扁", "增厚", "部件比例", "透视失真")):
        raise ValueError("PLANNER_NEGATIVE_PRODUCT_FIDELITY_RULE_MISSING")
    image["positive_prompt"] = positive_prompt
    image["negative_prompt"] = negative_prompt
    request = positive_prompt + "\n\n负面提示词：" + negative_prompt
    from services.handlers.chat_image_request import validate_single_image_request
    validate_single_image_request({"mode": "image_to_image", "prompt": request,
        "aspect_ratio": image["aspect_ratio"], "resolution": snapshot["target_size"]["resolution"]}, len(image["references"]))
    return {**image, "item_id": str(uuid4()), "request_text": request, "request_text_sha256": text_hash(request)}
