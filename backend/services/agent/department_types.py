"""
部门Agent类型定义。

ValidationResult — 部门Agent参数校验的三态结果。

设计文档: docs/document/TECH_多Agent单一职责重构.md §6.1
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ValidationStatus(Enum):
    """参数校验状态"""
    OK = "ok"              # 参数齐全，可执行
    MISSING = "missing"    # 缺少必填参数，返回协商提示
    CONFLICT = "conflict"  # 参数互斥或格式错误


@dataclass(frozen=True)
class ValidationResult:
    """部门Agent参数校验结果。

    三态：
    - ok: 参数齐全，可执行
    - missing: 缺少必填参数，返回需要的参数列表
    - conflict: 参数互斥/格式错误，返回冲突说明
    """
    status: ValidationStatus
    message: str = ""
    missing_params: tuple[str, ...] = ()
    prompt: str = ""  # 引导主Agent转达给用户的友好话术

    @classmethod
    def ok(cls) -> ValidationResult:
        """参数校验通过"""
        return cls(status=ValidationStatus.OK)

    @classmethod
    def missing(
        cls, params: list[str], prompt: str = "",
    ) -> ValidationResult:
        """缺少必填参数"""
        return cls(
            status=ValidationStatus.MISSING,
            message=f"请补充以下信息：{', '.join(params)}",
            missing_params=tuple(params),
            prompt=prompt or f"请补充以下信息：{', '.join(params)}",
        )

    @classmethod
    def conflict(cls, reason: str) -> ValidationResult:
        """参数冲突/格式错误"""
        return cls(
            status=ValidationStatus.CONFLICT,
            message=reason,
        )

    @property
    def is_ok(self) -> bool:
        return self.status == ValidationStatus.OK

    @property
    def is_missing(self) -> bool:
        return self.status == ValidationStatus.MISSING

    @property
    def is_conflict(self) -> bool:
        return self.status == ValidationStatus.CONFLICT
