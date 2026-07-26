"""Agent Runtime 领域合同错误。"""

from __future__ import annotations


class DomainContractError(ValueError):
    """领域合同被违反。"""


class InvalidTransitionError(DomainContractError):
    """状态转移不在闭合状态图中。"""


class ScopeMismatchError(DomainContractError):
    """父子 Runtime Scope 不一致。"""


class FencingTokenMismatchError(DomainContractError):
    """提交者不持有当前 fencing token。"""


class LeaseExpiredError(DomainContractError):
    """提交者持有的 lease 已过期。"""


class IdempotencyConflictError(DomainContractError):
    """同一幂等键被用于不同的逻辑请求。"""


class InvalidRecoveryError(DomainContractError):
    """Action 恢复方式会造成不安全的重复执行。"""
