from __future__ import annotations

import pytest

from services.agent.sandbox_tool_mixin import SandboxToolMixin


class _Legacy(SandboxToolMixin):
    pass


@pytest.mark.asyncio
async def test_legacy_code_execute_never_runs_code() -> None:
    result = await _Legacy()._code_execute({
        "code": "raise RuntimeError('must not run')",
    })
    assert result.status == "error"
    assert result.error_message == "CODE_EXECUTE_REQUIRES_ACTION_RUNTIME"
    assert result.metadata["retryable"] is False
