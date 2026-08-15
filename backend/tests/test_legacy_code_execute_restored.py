from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.agent.agent_result import AgentResult
from services.agent.sandbox_tool_mixin import SandboxToolMixin


class _Legacy(SandboxToolMixin):
    def __init__(self) -> None:
        self.user_id = "user"
        self.workspace_user_id = "user"
        self.org_id = None
        self.conversation_id = "legacy-contract"
        self.db = MagicMock()


@pytest.mark.asyncio
async def test_legacy_code_execute_routes_through_sandbox_executor() -> None:
    legacy = _Legacy()
    executor = MagicMock()
    executor.execute = AsyncMock(
        return_value=AgentResult(summary="sandbox result", status="success"),
    )
    settings = MagicMock(
        sandbox_enabled=True,
        sandbox_timeout=120,
        sandbox_max_result_chars=8000,
    )
    code = "raise RuntimeError('must not run in the actor process')"

    with (
        patch("core.config.get_settings", return_value=settings),
        patch(
            "services.sandbox.functions.build_sandbox_executor",
            return_value=executor,
        ) as build_executor,
        patch(
            "services.sandbox.kernel_manager.get_kernel_manager",
            return_value="actor-kernel",
        ),
        patch.object(legacy, "_get_staging_dir", return_value="/tmp/staging"),
        patch.object(legacy, "_register_files_from_output"),
        patch.object(legacy, "_record_sandbox_metric"),
    ):
        result = await legacy._code_execute({"code": code})

    assert result.status == "success"
    executor.execute.assert_awaited_once_with(code, "")
    assert build_executor.call_args.kwargs["kernel_manager"] == "actor-kernel"
