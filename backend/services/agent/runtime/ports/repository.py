"""Agent Runtime 聚合持久化 SPI。"""

from __future__ import annotations

from typing import Mapping, Protocol

from services.agent.runtime.domain import (
    ActionAttempt,
    ActionId,
    ActionResult,
    ActionStatus,
    FencingToken,
    ModelStepId,
    ModelStepStatus,
    RunAttempt,
    RunId,
    RunStatus,
    SessionCommand,
    SessionId,
)


class RuntimeRepositoryPort(Protocol):
    """应用用例依赖的原子 Repository 合同。"""

    async def submit_command(
        self,
        command: SessionCommand,
    ) -> Mapping[str, object]:
        """按命令幂等键提交，重复请求返回既有 receipt。"""

    async def get_session(self, session_id: SessionId) -> Mapping[str, object] | None:
        """读取 Session 协调视图。"""

    async def claim_run(self, run_id: RunId, worker_id: str) -> RunAttempt | None:
        """签发或恢复 Run 执行权。"""

    async def transition_run(
        self,
        run_id: RunId,
        current: RunStatus,
        target: RunStatus,
        state_version: int,
        token: FencingToken | None,
    ) -> Mapping[str, object]:
        """以状态、版本、Scope 和可选 fencing token 原子推进 Run。"""

    async def transition_model_step(
        self,
        model_step_id: ModelStepId,
        current: ModelStepStatus,
        target: ModelStepStatus,
        state_version: int,
        token: FencingToken,
    ) -> Mapping[str, object]:
        """原子推进 ModelStep。"""

    async def claim_action(
        self,
        action_id: ActionId,
        worker_id: str,
    ) -> ActionAttempt | None:
        """仅在重试分类安全时签发 ActionAttempt。"""

    async def transition_action(
        self,
        action_id: ActionId,
        current: ActionStatus,
        target: ActionStatus,
        state_version: int,
        token: FencingToken | None,
        result: ActionResult | None = None,
    ) -> Mapping[str, object]:
        """原子推进 Action；completed 必须提交结果。"""
