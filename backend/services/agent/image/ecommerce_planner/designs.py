"""Creative-only v3 delivery, exhaustive validation and field-scoped repair."""
from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import Field, ValidationError

from .contracts import ReviewRecord, StrictModel
from .inputs import is_product

DESIGN_VERSION = "ecom-design.v3"


class ReferenceUsage(StrictModel):
    number: int = Field(ge=1, le=16)
    usage: str = Field(min_length=1, description="该图具体提供的视图或风格及使用边界；编号沿用输入，不排序。")


class PageLink(StrictModel):
    content_task: str = Field(min_length=1)
    previous: str = Field(min_length=1)
    next: str = Field(min_length=1)
    edge_treatment: str = Field(min_length=1, description="本屏边缘完整表达或有目的的特写；默认不跨屏补画。")


class ImageDesign(StrictModel):
    position: int = Field(ge=1, le=15)
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1)
    fact_ids: list[str] = Field(min_length=1)
    reference_usage: list[ReferenceUsage] = Field(min_length=1, max_length=16)
    scene: str = Field(min_length=1, description="具体背景、承托面、空间层次、配色和媒介；不改商品固有色。")
    layout: str = Field(min_length=1, description="主体状态、位置、尺度、来源视图、辅助视图、裁切与前后层级。")
    props_and_decoration: str = Field(min_length=1, description="已确定道具与装饰的位置、尺度、用途、叠压；没有则明确无。")
    lighting: str = Field(min_length=1, description="光源、方向、覆盖、遮挡及由此产生的高光、暗部、投影与曝光。")
    text_layout: str = Field(min_length=1, description="每处新增文案的准确原文、换行、次数、字形、颜色、尺度、位置和对应对象；没有则明确无新增文字。")
    product_preservation: str = Field(min_length=1, description="当前商品的具体形态、配色、图案、原印刷、部件比例及允许修改范围。")
    execution_constraints: str = Field(min_length=1, description="本张具体执行边界、关键可读内容及当前事实的声明限制。")
    negative_additions: str = Field(description="本张额外负面约束；通用防变形规则由程序添加，无补充填空字符串。")
    page_link: PageLink | None = Field(description="主图为null；详情屏填写内容任务、前后关系和边缘处理。")


class Coverage(StrictModel):
    question: str = Field(min_length=1)
    fact_ids: list[str] = Field(min_length=1)
    positions: list[int] = Field(min_length=1)


class Boundary(StrictModel):
    after_position: int = Field(ge=1, le=14)
    transition: str = Field(min_length=1, description="相邻两屏的内容、底色、留白与阅读节奏；不强制精准接缝。")


class PagePlan(StrictModel):
    reading_strategy: str = Field(min_length=1)
    visual_rhythm: str = Field(min_length=1)
    coverage: list[Coverage] = Field(min_length=1)
    boundaries: list[Boundary]
    generation_path: Literal["independent_screens"]


class DesignsOutput(StrictModel):
    schema_version: Literal["ecom-design.v3"]
    status: Literal["ready", "needs_input"]
    questions: list[str]
    page_plan: PagePlan | None
    images: list[ImageDesign]
    review_records: list[ReviewRecord]


class FieldPatch(StrictModel):
    path: list[str | int] = Field(min_length=1)
    value: object
    op: Literal["replace", "remove"] = "replace"


class DesignRepair(StrictModel):
    patches: list[FieldPatch]
    review_records: list[ReviewRecord] = Field(min_length=1)


class DesignValidationError(ValueError):
    def __init__(self, issues):
        self.issues = []
        for issue in sorted(issues, key=lambda issue: len(issue["path"])):
            if any(issue["path"][:len(old["path"])] == old["path"] for old in self.issues):
                continue
            self.issues.append(issue)
        super().__init__("PLANNER_DESIGN_INVALID: " + "; ".join(
            f"{'.'.join(map(str, issue['path']))}: {issue['code']}" for issue in issues))


def validate_designs(value, snapshot, product):
    issues = []
    def problem(path, code):
        issues.append({"path": list(path), "code": code})
    try:
        draft = DesignsOutput.model_validate(value).model_dump()
    except ValidationError as error:
        # Accumulate every schema defect before asking the model to repair.
        for item in error.errors():
            problem(item["loc"], item["type"])
        raise DesignValidationError(issues) from error
    count = snapshot["image_count"]
    usable = {fact["id"] for fact in product.get("facts", []) if fact["status"] == "usable"}
    detail = snapshot.get("task_type") == "detail_page"
    if draft["status"] == "needs_input":
        if draft["images"] or not 1 <= len(draft["questions"]) <= 3 or any(not q.strip() for q in draft["questions"]):
            problem(("questions",), "PLANNER_QUESTIONS_INVALID")
        if draft["images"]:
            problem(("images",), "PLANNER_QUESTIONS_INVALID")
        if issues:
            raise DesignValidationError(issues)
        return draft
    if draft["questions"]:
        problem(("questions",), "PLANNER_READY_QUESTIONS_INVALID")
    if len(draft["images"]) != count:
        problem(("images",), "PLANNER_IMAGE_COUNT_INVALID")
    for index, image in enumerate(draft["images"]):
        prefix = ("images", index)
        for key in ("name", "purpose", "scene", "layout", "props_and_decoration", "lighting", "text_layout", "product_preservation", "execution_constraints"):
            if not image[key].strip():
                problem((*prefix, key), "PLANNER_DESIGN_FIELD_EMPTY")
        if image["position"] != index + 1:
            problem((*prefix, "position"), "PLANNER_IMAGE_ORDER_INVALID")
        if not set(image["fact_ids"]) <= usable:
            problem((*prefix, "fact_ids"), "PLANNER_UNSUPPORTED_IMAGE_FACT")
        usages = image["reference_usage"]
        numbers = [use["number"] for use in usages]
        refs = snapshot["references"]
        if any(not use["usage"].strip() for use in usages):
            problem((*prefix, "reference_usage"), "PLANNER_DESIGN_FIELD_EMPTY")
        if len(numbers) != len(set(numbers)) or any(n > len(refs) for n in numbers):
            problem((*prefix, "reference_usage"), "PLANNER_UNKNOWN_REFERENCE_NUMBER")
        elif not any(is_product(refs[n - 1]["role"]) for n in numbers):
            problem((*prefix, "reference_usage"), "PLANNER_PRODUCT_REFERENCE_REQUIRED")
        if detail != (image["page_link"] is not None):
            problem((*prefix, "page_link"), "PLANNER_PAGE_LINK_INVALID")
    if detail:
        page = draft["page_plan"]
        if page is None:
            problem(("page_plan",), "PLANNER_PAGE_PLAN_REQUIRED")
        else:
            if [b["after_position"] for b in page["boundaries"]] != list(range(1, count)):
                problem(("page_plan", "boundaries"), "PLANNER_PAGE_BOUNDARIES_INVALID")
            covered = set()
            for index, coverage in enumerate(page["coverage"]):
                positions = coverage["positions"]
                if (len(positions) != len(set(positions)) or any(not 1 <= n <= count for n in positions)
                        or not set(coverage["fact_ids"]) <= usable):
                    problem(("page_plan", "coverage", index), "PLANNER_PAGE_COVERAGE_INVALID")
                for n in positions:
                    if 1 <= n <= len(draft["images"]) and not set(coverage["fact_ids"]) <= set(draft["images"][n - 1]["fact_ids"]):
                        problem(("page_plan", "coverage", index), "PLANNER_PAGE_COVERAGE_FACT_MISMATCH")
                covered.update(positions)
            if covered != set(range(1, count + 1)):
                problem(("page_plan", "coverage"), "PLANNER_PAGE_COVERAGE_INCOMPLETE")
    elif draft["page_plan"] is not None:
        problem(("page_plan",), "PLANNER_MAIN_PAGE_PLAN_INVALID")
    if not draft["review_records"] or any(r["conclusion"] == "blocked" for r in draft["review_records"]):
        problem(("review_records",), "PLANNER_REVIEW_BLOCKED")
    if issues:
        raise DesignValidationError(issues)
    return draft


def repair_context(previous, issues):
    """Expose only faulty fields/images; keep the full authoritative draft server-side."""
    targets = []
    for issue in issues:
        path = issue["path"]
        if path in [target["path"] for target in targets]:
            continue
        cursor = previous
        try:
            for key in path:
                cursor = cursor[key]
        except (KeyError, IndexError, TypeError):
            cursor = None
        context = None
        if len(path) >= 2 and path[0] == "images" and isinstance(path[1], int):
            try:
                context = previous["images"][path[1]]
            except (KeyError, IndexError, TypeError):
                pass
        targets.append({"path": path, "code": issue["code"], "current_value": cursor, "affected_image": context})
    return targets


def apply_repair(previous, value, issues):
    patch = DesignRepair.model_validate(value).model_dump()
    allowed = {tuple(issue["path"]) for issue in issues if issue["path"] != ["review_records"]}
    actual = [tuple(item["path"]) for item in patch["patches"]]
    # Every targeted defect is repaired together. Neither siblings nor whole
    # unaffected images may be overwritten by a wider replacement.
    if set(actual) != allowed or len(actual) != len(set(actual)):
        raise ValueError("PLANNER_REPAIR_SCOPE_CHANGED")
    updated = deepcopy(previous)
    for item in patch["patches"]:
        cursor = updated
        try:
            for key in item["path"][:-1]:
                cursor = cursor[key]
            if item["op"] == "remove":
                # Removal is only meaningful for explicitly rejected extra keys.
                matching = [issue for issue in issues if issue["path"] == item["path"]]
                if not matching or matching[0]["code"] != "extra_forbidden" or not isinstance(cursor, dict):
                    raise ValueError("PLANNER_REPAIR_SCOPE_CHANGED")
                del cursor[item["path"][-1]]
            else:
                cursor[item["path"][-1]] = item["value"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("PLANNER_REPAIR_SCOPE_CHANGED") from error
    updated["review_records"] = patch["review_records"]
    return updated
