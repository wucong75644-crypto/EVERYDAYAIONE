"""Validate model image arguments against the advertised platform contract.

This deliberately supports the vocabulary used by the image ToolSpec, rather
than importing remote schemas or changing legacy validators for other tools.
It never coerces, drops fields, or changes generation inputs.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re

from .spec import thaw


class ToolArgumentValidationError(ValueError):
    code = "IMAGE_TOOL_ARGUMENTS_INVALID"

    def __init__(self, issues, schema):
        self.issues = issues[:16]
        self.schema = schema
        super().__init__(json.dumps({
            "code": self.code, "accepted": False, "submission_state": "not_accepted",
            "issues": self.issues,
            "parameters": schema,
            "message": "未创建图片任务，未预扣图片积分。按当前合同修正参数；保留提示词原文、参考图顺序和已确认规格。实际资料不明确时询问用户。",
        }, ensure_ascii=False))


def _issues(value, schema, path="$", depth=0):
    if depth > 12:
        return [{"path": path, "reason": "结构层级超过限制"}]
    issues = []
    def add(reason):
        issues.append({"path": path, "reason": reason})
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, Mapping), "array": isinstance(value, (list, tuple)),
        "string": isinstance(value, str), "integer": type(value) is int,
    }
    if expected and not matches.get(expected, False):
        add(f"类型必须为 {expected}")
        return issues
    if "enum" in schema and value not in schema["enum"]:
        add("可选值为 " + json.dumps(schema["enum"], ensure_ascii=False))
    if isinstance(value, Mapping):
        if "oneOf" in schema:
            count = sum(not _issues(value, branch, path, depth + 1) for branch in schema["oneOf"])
            if count != 1:
                add("必须且只能选择一种参数形式")
        properties = schema.get("properties", {})
        for key in schema.get("required", ()):
            if key not in value:
                issues.append({"path": f"{path}.{key}", "reason": "缺少必填字段"})
        for key, required in schema.get("dependentRequired", {}).items():
            if key in value:
                for dependency in required:
                    if dependency not in value:
                        issues.append({"path": path, "reason": f"使用 {key} 时必须填写 {dependency}"})
        for key, child in value.items():
            child_path = f"{path}.{str(key)[:80]}"
            if key not in properties:
                if schema.get("additionalProperties") is False:
                    reason = "未知字段"
                    alias = {"format": "output_format", "size": "resolution"}.get(key)
                    if path == "$" and alias:
                        reason += f"；请使用 {alias}，实际值须符合该字段定义"
                    if path == "$" and key in {"model", "model_name"}:
                        reason += "；生图模型由服务器固定，无需选择"
                    issues.append({"path": child_path, "reason": reason})
            else:
                issues.extend(_issues(child, properties[key], child_path, depth + 1))
    if isinstance(value, (list, tuple)):
        for bound, op in (("maxItems", lambda n: len(value) > n), ("minItems", lambda n: len(value) < n)):
            if bound in schema and op(schema[bound]):
                add(f"{bound}={schema[bound]}")
        for index, child in enumerate(value[:17]):
            issues.extend(_issues(child, schema.get("items", {}), f"{path}[{index}]", depth + 1))
    if isinstance(value, str):
        for bound, op in (("minLength", lambda n: len(value) < n), ("maxLength", lambda n: len(value) > n)):
            if bound in schema and op(schema[bound]):
                add(f"{bound}={schema[bound]}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            add("格式不符合字段 pattern")
    if type(value) is int and "minimum" in schema and value < schema["minimum"]:
        add(f"最小值为 {schema['minimum']}")
    return issues[:16]


def validate_model_image_arguments(spec, arguments):
    schema = spec.to_schema() if spec is not None else None
    parameters = (schema or {}).get("function", {}).get("parameters")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ValueError("IMAGE_TOOL_CONTRACT_UNAVAILABLE")
    issues = _issues(thaw(arguments), parameters)
    if issues:
        raise ToolArgumentValidationError(issues, parameters)


def decode_image_arguments(raw):
    def pairs(items):
        value = dict(items)
        if len(value) != len(items):
            raise ValueError("重复字段")
        return value
    def finite(value):
        if isinstance(value, dict):
            return all(finite(child) for child in value.values())
        if isinstance(value, list):
            return all(finite(child) for child in value)
        return not isinstance(value, float) or math.isfinite(value)
    value = json.loads(raw, object_pairs_hook=pairs) if raw else {}
    if not isinstance(value, dict) or not finite(value):
        raise ValueError("必须是有效 JSON 对象，数值必须有限")
    return value


def parse_model_image_arguments(spec, raw):
    parameters = spec.to_schema()["function"]["parameters"]
    try:
        value = decode_image_arguments(raw)
    except (ValueError, TypeError) as exc:
        raise ToolArgumentValidationError([{"path": "$", "reason": "参数必须是有效 JSON 对象；字段不能重复、数值必须有限"}], parameters) from exc
    return value
