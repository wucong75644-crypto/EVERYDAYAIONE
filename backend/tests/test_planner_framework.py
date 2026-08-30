from services.planner import (
    CapabilityDescriptor,
    CapabilityRegistry,
    PlanCandidate,
    PlanStep,
    PlanValidator,
    validate_runtime_tool,
)


def _candidate(**kwargs):
    values = {
        "target": {"resource_type": "scheduled_task"},
        "input_contract": {"type": "task"},
        "output_contract": {"required": ["result"]},
        "steps": (PlanStep("s1", "查询", ("read_tool",), verification="有结果"),),
        "candidate_tools": ("read_tool",),
        "verification_conditions": ("有结果",),
        "risk_info": {"risk_level": "low"},
    }
    values.update(kwargs)
    return PlanCandidate(**values)


def test_validator_rejects_unknown_tool_and_invalid_parameters():
    registry = CapabilityRegistry([CapabilityDescriptor(
        tool_name="read_tool",
        input_schema={"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
        execution_modes=("scheduled",),
    )])
    validator = PlanValidator(registry)
    result = validator.validate(
        _candidate(candidate_tools=("read_tool", "ghost")),
        parameters={"read_tool": {"query": 123}},
    )
    assert result.valid is False
    assert any("未在 Capability Registry" in error for error in result.errors)
    assert any("必须是字符串" in error for error in result.errors)


def test_runtime_gateway_rechecks_frozen_policy_and_descriptor():
    registry = CapabilityRegistry([CapabilityDescriptor(
        tool_name="read_tool", execution_modes=("scheduled",),
    )])
    validate_runtime_tool(
        "read_tool", {"allowed_tools": ["read_tool"]},
        registry=registry, execution_mode="scheduled",
    )
    try:
        validate_runtime_tool(
            "write_tool", {"allowed_tools": ["write_tool"]},
            registry=registry, execution_mode="scheduled",
        )
    except PermissionError as exc:
        assert "未在 Capability Registry" in str(exc)
    else:
        raise AssertionError("未注册工具不应通过运行时策略")


def test_validator_rejects_parameter_binding_outside_candidate_tools():
    registry = CapabilityRegistry([CapabilityDescriptor(tool_name="read_tool")])
    result = PlanValidator(registry).validate(
        _candidate(), parameters={"other_tool": {"value": "x"}},
    )
    assert result.valid is False
    assert any("不在候选范围内" in error for error in result.errors)
