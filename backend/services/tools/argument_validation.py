"""Validate the schema vocabulary used by native model tools.

Image calls retain their dedicated rejection format and correction policy.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re
from urllib.parse import unquote

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


def _local_schema_reference(root, reference):
    if reference == '#':
        return root
    if not reference.startswith('#/'):
        raise ValueError('仅支持工具合同内的本地引用')
    target = root
    for part in unquote(reference[2:]).split('/'):
        token = part.replace('~1', '/').replace('~0', '~')
        target = target[int(token)] if isinstance(target, (list, tuple)) else target[token]
    if not isinstance(target, (Mapping, bool)):
        raise ValueError('引用目标不是 Schema')
    return target


class _InvalidSchemaReference(ValueError):
    pass


def _issues(value, schema, *, image_hints=True):
    try:
        return _schema_issues(value, schema, schema, "$", 0, image_hints)
    except _InvalidSchemaReference as error:
        return [{"path": str(error), "reason": "工具合同引用无效"}]


def _schema_issues(value, schema, root, path, depth, image_hints):
    if depth > 24:
        return [{"path": path, "reason": "结构层级超过限制"}]
    if isinstance(schema, bool):
        return [] if schema else [{"path": path, "reason": "不允许该值"}]

    def nested(child, definition, child_path=path):
        return _schema_issues(child, definition, root, child_path, depth + 1, image_hints)

    issues = []
    def add(reason):
        issues.append({"path": path, "reason": reason})
    if "$ref" in schema:
        try:
            target = _local_schema_reference(root, schema["$ref"])
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise _InvalidSchemaReference(path) from None
        issues.extend(nested(value, target))
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, Mapping), "array": isinstance(value, (list, tuple)),
        "string": isinstance(value, str), "integer": type(value) is int,
        "boolean": type(value) is bool, "number": type(value) in (int, float), "null": value is None,
    }
    if expected and not any(matches.get(kind, False) for kind in (expected if isinstance(expected, list) else [expected])):
        add(f"类型必须为 {expected}")
        return issues
    if "anyOf" in schema and not any(not nested(value, branch) for branch in schema['anyOf']):
        add('不符合anyOf参数分支')
    if "allOf" in schema:
        for branch in schema['allOf']:
            issues.extend(nested(value, branch))
    if "not" in schema and not nested(value, schema['not']):
        add('命中禁止的参数组合')
    if "const" in schema and value != schema['const']:
        add('值不符合const')
    if "enum" in schema and value not in schema["enum"]:
        add("可选值为 " + json.dumps(schema["enum"], ensure_ascii=False))
    if "oneOf" in schema:
        count = sum(not nested(value, branch) for branch in schema["oneOf"])
        if count != 1:
            add("必须且只能选择一种参数形式")
    if isinstance(value, Mapping):
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
                    if image_hints and path == "$" and alias:
                        reason += f"；请使用 {alias}，实际值须符合该字段定义"
                    if image_hints and path == "$" and key in {"model", "model_name"}:
                        reason += "；生图模型由服务器固定，无需选择"
                    issues.append({"path": child_path, "reason": reason})
            else:
                issues.extend(nested(child, properties[key], child_path))
    if isinstance(value, (list, tuple)):
        for bound, op in (("maxItems", lambda n: len(value) > n), ("minItems", lambda n: len(value) < n)):
            if bound in schema and op(schema[bound]):
                add(f"{bound}={schema[bound]}")
        if schema.get('uniqueItems'):
            keys = [json.dumps(thaw(child), sort_keys=True, allow_nan=False) for child in value]
            if len(set(keys)) != len(keys):
                add('数组元素必须唯一')
        if "items" in schema:
            for index, child in enumerate(value):
                issues.extend(nested(child, schema["items"], f"{path}[{index}]"))
    if isinstance(value, str):
        for bound, op in (("minLength", lambda n: len(value) < n), ("maxLength", lambda n: len(value) > n)):
            if bound in schema and op(schema[bound]):
                add(f"{bound}={schema[bound]}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            add("格式不符合字段 pattern")
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            add('数值必须有限')
        if 'minimum' in schema and value < schema['minimum']:
            add(f"最小值为 {schema['minimum']}")
        if 'maximum' in schema and value > schema['maximum']:
            add(f"最大值为 {schema['maximum']}")
    return issues[:16]


def validate_model_image_arguments(spec, arguments):
    schema = spec.to_schema() if spec is not None else None
    parameters = (schema or {}).get("function", {}).get("parameters")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ValueError("IMAGE_TOOL_CONTRACT_UNAVAILABLE")
    issues = _issues(thaw(arguments), parameters)
    if issues:
        raise ToolArgumentValidationError(issues, parameters)


def decode_tool_arguments(raw):
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



# Existing image callers share the same parser without changing their error contract.
decode_image_arguments = decode_tool_arguments


def parse_model_image_arguments(spec, raw):
    parameters = spec.to_schema()["function"]["parameters"]
    try:
        value = decode_image_arguments(raw)
    except (ValueError, TypeError) as exc:
        raise ToolArgumentValidationError([{"path": "$", "reason": "参数必须是有效 JSON 对象；字段不能重复、数值必须有限"}], parameters) from exc
    return value
