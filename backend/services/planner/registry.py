"""Planner capability projection of ToolSpec; no runtime authorization authority."""

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
        """Registered names derive from Spec; preserve custom offline planner API."""
        from services.tools.catalog import build_tool_catalog
        catalog = build_tool_catalog()
        descriptors = []
        for name in sorted({str(name) for name in names if str(name).strip()}):
            spec = catalog.get(name)
            descriptors.append(cls._from_spec(spec) if spec is not None else CapabilityDescriptor(
                tool_name=name, input_schema={}, read_attributes=("query",), risk_level="low",
            ))
        return cls(descriptors)

    @staticmethod
    def _from_spec(spec) -> CapabilityDescriptor:
        dangerous = spec.risk_level == "dangerous"
        schema = spec.to_schema() or {}
        return CapabilityDescriptor(
            tool_name=spec.name,
            input_schema=(schema.get("function") or {}).get("parameters") or {},
            output_schema={},
            # Preserve capability.v1 risk/read/write labels; these labels do not
            # classify actual effects or grant unattended writes.
            read_attributes=("business_data",) if not dangerous else (),
            write_attributes=("business_state",) if dangerous else (),
            risk_level="high" if dangerous else "low",
            required_permissions=spec.policy_rules.required_permissions,
            execution_modes=spec.policy_rules.execution_modes,
            supports_readonly_preflight="preflight" in spec.policy_rules.execution_modes,
        )

    @classmethod
    def from_specs(cls, specs: Iterable) -> "CapabilityRegistry":
        return cls(cls._from_spec(spec) for spec in specs)

    @classmethod
    def from_tool_schemas(cls, tools: Iterable[Mapping[str, Any]]) -> "CapabilityRegistry":
        from services.tools.catalog import build_tool_catalog
        catalog = build_tool_catalog()
        descriptors = []
        for tool in tools:
            function = tool.get("function") or {}
            name = str(function.get("name") or "").strip()
            if not name:
                continue
            spec = catalog.get(name)
            if spec is not None:
                descriptors.append(cls._from_spec(spec))
                continue
            # Existing generic Planner clients may supply their own schemas.
            # Such descriptors cannot register/authorize a runtime tool.
            descriptors.append(CapabilityDescriptor(
                tool_name=name,
                input_schema=function.get("parameters") or {},
                output_schema={},
                read_attributes=("business_data",),
                write_attributes=(),
                risk_level="low",
                execution_modes=("interactive", "scheduled", "preflight"),
                supports_readonly_preflight=True,
            ))
        return cls(descriptors)

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {name: descriptor.as_dict() for name, descriptor in sorted(self._descriptors.items())}
