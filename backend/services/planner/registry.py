"""Capability Registry：工具能力的系统事实来源。"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from services.planner.contracts import CapabilityDescriptor


class CapabilityRegistry:
    def __init__(self, descriptors: Iterable[CapabilityDescriptor] = ()) -> None:
        self._descriptors: dict[str, CapabilityDescriptor] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: CapabilityDescriptor) -> None:
        if not descriptor.tool_name.strip():
            raise ValueError("能力描述缺少工具名")
        if descriptor.tool_name in self._descriptors:
            raise ValueError(f"能力重复注册: {descriptor.tool_name}")
        self._descriptors[descriptor.tool_name] = descriptor

    def get(self, tool_name: str) -> CapabilityDescriptor | None:
        return self._descriptors.get(tool_name)

    def require(self, tool_name: str) -> CapabilityDescriptor:
        descriptor = self.get(tool_name)
        if descriptor is None:
            raise KeyError(f"能力未注册: {tool_name}")
        return descriptor

    def names(self) -> set[str]:
        return set(self._descriptors)

    @classmethod
    def from_names(cls, names: Iterable[str]) -> "CapabilityRegistry":
        """为兼容已有计划测试构造保守描述；生产入口优先使用 from_tool_schemas。"""
        return cls(
            CapabilityDescriptor(
                tool_name=str(name),
                input_schema={},
                read_attributes=("query",),
                risk_level="low",
            )
            for name in sorted({str(name) for name in names if str(name).strip()})
        )

    @classmethod
    def from_tool_schemas(cls, tools: Iterable[Mapping[str, Any]]) -> "CapabilityRegistry":
        from config.chat_tools import SafetyLevel, get_safety_level

        descriptors = []
        for tool in tools:
            function = tool.get("function") or {}
            name = str(function.get("name") or "").strip()
            if not name:
                continue
            safety = get_safety_level(name)
            dangerous = safety is SafetyLevel.DANGEROUS
            descriptors.append(CapabilityDescriptor(
                tool_name=name,
                input_schema=function.get("parameters") or {},
                output_schema={},
                read_attributes=("business_data",) if not dangerous else (),
                write_attributes=("business_state",) if dangerous else (),
                risk_level="high" if dangerous else "low",
                execution_modes=("interactive", "scheduled") if dangerous else (
                    "interactive", "scheduled", "preflight",
                ),
                supports_readonly_preflight=not dangerous,
            ))
        return cls(descriptors)

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {name: descriptor.as_dict() for name, descriptor in sorted(self._descriptors.items())}
