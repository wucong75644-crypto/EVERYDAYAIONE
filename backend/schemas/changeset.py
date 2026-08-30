"""ChangeSet 对外 DTO；前端只依赖这些字段，不读取业务表快照来推断状态。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


ChangeSetStatusLiteral = Literal[
    "draft", "resolving", "proposed", "validating", "preflighting",
    "awaiting_approval", "committing", "applied", "cancelled", "rejected",
    "failed", "expired", "conflicted",
]


class ChangeCheckDTO(BaseModel):
    id: str
    change_set_id: str
    check_type: str
    check_key: str
    input: Dict[str, Any] = Field(default_factory=dict)
    result: Dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "running", "passed", "failed", "skipped"]
    actor_id: Optional[str] = None
    actor_type: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime


class ChangeEventDTO(BaseModel):
    id: str
    change_set_id: str
    sequence: int
    event_type: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    actor_id: Optional[str] = None
    actor_type: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ChangeSetDTO(BaseModel):
    id: str
    org_id: str
    resource_type: str
    resource_id: str
    operation: str
    base_revision: str
    base_snapshot: Dict[str, Any] = Field(default_factory=dict)
    proposed_snapshot: Dict[str, Any] = Field(default_factory=dict)
    patch: List[Dict[str, Any]] = Field(default_factory=list)
    diff: Dict[str, Any] = Field(default_factory=dict)
    risk_level: Literal["low", "medium", "high", "critical"]
    policy_snapshot: Dict[str, Any] = Field(default_factory=dict)
    plan_snapshot: Optional[Dict[str, Any]] = None
    tool_policy_snapshot: Optional[Dict[str, Any]] = None
    check_summary: Optional[Dict[str, Any]] = None
    status: ChangeSetStatusLiteral
    idempotency_key: str
    expires_at: datetime
    created_by: str
    created_by_type: str
    updated_by: Optional[str] = None
    updated_by_type: Optional[str] = None
    audit_subject: Dict[str, Any] = Field(default_factory=dict)
    recovery_of_id: Optional[str] = None
    committed_revision: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    conflict: Optional[Dict[str, Any]] = None
    revision: int
    created_at: datetime
    updated_at: datetime
    checks: List[ChangeCheckDTO] = Field(default_factory=list)


class ChangeSetTimelineDTO(BaseModel):
    change_set_id: str
    events: List[ChangeEventDTO] = Field(default_factory=list)


class CancelChangeSetRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class RecoverChangeSetRequest(BaseModel):
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=200)
