"""计划校验器：拒绝未注册工具、非法参数和不满足执行模式的计划。"""

from __future__ import annotations

from typing import Any, Mapping

from services.planner.contracts import PlanCandidate, PlanValidationResult
from services.planner.registry import CapabilityRegistry


def required_tools_for_steps(steps: Any) -> set[str]:
    """Compile the completion-gate tool set from required plan steps only."""
    required_tools: set[str] = set()
    for step in steps or ():
        required = step.required if hasattr(step, "required") else bool(step.get("required", True))
        if required:
            tools = step.tools if hasattr(step, "tools") else step.get("tools", ())
            required_tools.update(str(name).strip() for name in tools if str(name).strip())
    return required_tools


class PlanValidator:
    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def validate(
        self,
        candidate: PlanCandidate,
        *,
        parameters: Mapping[str, Mapping[str, Any]] | None = None,
        execution_mode: str = "scheduled",
    ) -> PlanValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        candidate_tools = {str(name).strip() for name in candidate.candidate_tools if str(name).strip()}
        if not candidate_tools and candidate.steps:
            errors.append("计划步骤存在但没有候选工具")

        for tool_name in sorted(candidate_tools):
            descriptor = self.registry.get(tool_name)
            if descriptor is None:
                errors.append(f"工具未在 Capability Registry 注册: {tool_name}")
                continue
            if execution_mode not in descriptor.execution_modes:
                errors.append(f"工具不支持执行模式 {execution_mode}: {tool_name}")
            if execution_mode == "preflight" and not descriptor.supports_readonly_preflight:
                errors.append(f"工具不支持只读预检: {tool_name}")

        required_permissions: set[str] = set()
        risk_levels: set[str] = set()
        for index, step in enumerate(candidate.steps, start=1):
            step_tools = {str(name).strip() for name in step.tools if str(name).strip()}
            if not step_tools <= candidate_tools:
                errors.append(f"第 {index} 步工具超出候选范围")
            for tool_name in step_tools:
                descriptor = self.registry.get(tool_name)
                if descriptor is not None and step.input:
                    errors.extend(_validate_schema(
                        descriptor.input_schema, step.input,
                        prefix=f"第 {index} 步 {tool_name}",
                    ))
                if descriptor is not None:
                    required_permissions.update(descriptor.required_permissions)
                    risk_levels.add(descriptor.risk_level)

        for tool_name, values in (parameters or {}).items():
            if tool_name not in candidate_tools:
                errors.append(f"参数绑定的工具不在候选范围内: {tool_name}")
                continue
            descriptor = self.registry.get(tool_name)
            if descriptor is None:
                errors.append(f"参数绑定的工具未注册: {tool_name}")
                continue
            errors.extend(_validate_schema(descriptor.input_schema, values, prefix=tool_name))

        if not isinstance(candidate.input_contract, Mapping):
            errors.append("输入契约必须是对象")
        if not isinstance(candidate.output_contract, Mapping):
            errors.append("输出契约必须是对象")
        else:
            required_output_tools = candidate.output_contract.get("required_tools")
            if required_output_tools is not None:
                if not isinstance(required_output_tools, list):
                    errors.append("输出契约 required_tools 必须是数组")
                elif not set(map(str, required_output_tools)) <= candidate_tools:
                    errors.append("输出契约要求了不在候选范围内的工具")
            output_schema = candidate.output_contract.get("schema")
            if output_schema is not None and not isinstance(output_schema, Mapping):
                errors.append("输出契约 schema 必须是对象")
        if candidate.steps and not candidate.verification_conditions and not candidate.output_contract:
            warnings.append("计划没有显式验证条件")

        return PlanValidationResult(
            valid=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            tool_policy={
                "version": 1,
                "execution_mode": execution_mode,
                "allowed_tools": sorted(candidate_tools),
                "required_tools": sorted(required_tools_for_steps(candidate.steps)),
                "required_permissions": sorted(required_permissions),
                "risk_levels": sorted(risk_levels),
                "verification_conditions": list(candidate.verification_conditions),
            },
        )


def validate_runtime_tool(
    tool_name: str,
    policy: Mapping[str, Any],
    *,
    registry: CapabilityRegistry,
    execution_mode: str,
) -> None:
    """运行时 Tool Gateway 的最后一道边界；不信任模型/任务中的工具列表。"""
    if tool_name not in {str(name) for name in policy.get("allowed_tools", [])}:
        raise PermissionError(f"运行时工具不在固化策略范围内: {tool_name}")
    descriptor = registry.get(tool_name)
    if descriptor is None:
        raise PermissionError(f"运行时工具未在 Capability Registry 注册: {tool_name}")
    if execution_mode not in descriptor.execution_modes:
        raise PermissionError(f"运行时工具不支持执行模式: {tool_name}")


def _validate_schema(schema: Mapping[str, Any], values: Mapping[str, Any], *, prefix: str) -> list[str]:
    if not schema:
        return []
    errors: list[str] = []
    required = schema.get("required") or []
    for name in required:
        if name not in values:
            errors.append(f"{prefix} 缺少参数: {name}")
    properties = schema.get("properties") or {}
    for name, value in values.items():
        definition = properties.get(name)
        if not isinstance(definition, Mapping):
            continue
        expected = definition.get("type")
        if expected == "string" and not isinstance(value, str):
            errors.append(f"{prefix}.{name} 必须是字符串")
        elif expected == "array" and not isinstance(value, list):
            errors.append(f"{prefix}.{name} 必须是数组")
        elif expected == "object" and not isinstance(value, Mapping):
            errors.append(f"{prefix}.{name} 必须是对象")
        elif expected == "boolean" and not isinstance(value, bool):
            errors.append(f"{prefix}.{name} 必须是布尔值")
        elif expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"{prefix}.{name} 必须是整数")
    return errors
