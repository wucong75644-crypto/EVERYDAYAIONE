"""Evidence and readiness checks are deterministic, never publishing approval."""

import re
from typing import Literal
from datetime import date
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from uuid import UUID


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: UUID
    quote: str = Field(min_length=1, max_length=1000)


class Topic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=1000)
    angle: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    unknowns: list[str] = Field(default_factory=list, max_length=20)
    decision: Literal["adopt", "adjust", "defer"]
    planned_date: date | None = None
    format: str = Field(max_length=100)
    required_materials: list[str] = Field(max_length=20)


class PlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[Topic] = Field(min_length=1, max_length=20)
    analysis: str = Field(max_length=15000)
    missing_inputs: list[str] = Field(max_length=30)


class DraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10000)
    tags: list[str] = Field(max_length=10)
    evidence: list[Evidence] = Field(min_length=1, max_length=30)
    claims: list[str] = Field(max_length=30)
    assumptions: list[str] = Field(max_length=20)
    required_materials: list[str] = Field(max_length=20)
    missing_inputs: list[str] = Field(max_length=30)


class CheckOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hard_errors: list[str] = Field(max_length=30)
    suggestions: list[str] = Field(max_length=30)
    unknowns: list[str] = Field(max_length=30)
    source_risk: str = Field(max_length=4000)


def verify_evidence(output, sources):
    by_id = {str(s["id"]): s for s in sources}
    groups = (
        [t["evidence"] for t in output["topics"]]
        if "topics" in output
        else [output.get("evidence", [])]
    )
    for group in groups:
        for evidence in group:
            source = by_id.get(str(evidence["snapshot_id"]))
            if not source or evidence["quote"] not in source["body"]:
                raise ValueError("EVIDENCE_QUOTE_NOT_SUPPORTED")


def check_draft(data, sources):
    ids = {str(s["id"]) for s in sources}
    errors = []
    if not data.get("title", "").strip():
        errors.append("缺少标题")
    if len(data.get("title", "")) > 20:
        errors.append("标题超过当前适配器20字限制")
    if not data.get("body", "").strip():
        errors.append("缺少正文")
    if any(not t.strip() or len(t) > 50 for t in data.get("tags", [])):
        errors.append("标签需非空且不超过50字")
    evidence = data.get("evidence_ids", []) or [
        e["snapshot_id"] for e in data.get("evidence", [])
    ]
    if not evidence:
        errors.append("缺少来源证据")
    if any(str(e) not in ids for e in evidence):
        errors.append("来源不存在或无权访问")
    return {
        "status": "needs_media",
        "ready_to_publish": False,
        "hard_errors": errors,
        "missing_inputs": ["待补图片素材与素材权属说明"],
        "unknowns": ["未进行平台审核", "引用不等于事实真实性或素材授权已核验"],
        "scope": "仅文字草稿检查；本阶段不创建发布授权",
    }


def validate_model_output(schema, text):
    """Keep strict validation while exposing only safe error categories."""
    from services.agent.agent_reach.contracts import ReachError

    cleaned = text.strip()
    # Accept only an entire JSON code fence, never extract a convenient object
    # from surrounding prose or repair values/claims supplied by the model.
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", cleaned, flags=re.S)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        return schema.model_validate_json(cleaned).model_dump(mode="json")
    except ValidationError as error:
        # Never persist input values, arbitrary extra keys, URLs or parser context.
        kinds = sorted({item["type"] for item in error.errors(
            include_url=False, include_context=False, include_input=False
        )})[:8]
        raise ReachError(
            "MODEL_OUTPUT_INVALID",
            "模型输出不符合约定：" + ",".join(kinds) + "；未生成成果，请勿重复提交。",
        ) from None
