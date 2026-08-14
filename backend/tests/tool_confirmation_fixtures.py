"""Fixtures shared by legacy chat tests exercising V3 confirmation."""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture(autouse=True)
def mock_v3_confirmation():
    from services.tool_confirmation.types import (
        ConfirmationBinding, ConfirmationDecision, ConfirmationOutcome,
        ConfirmationRequest,
    )

    async def create(**kwargs):
        binding = ConfirmationBinding(
            kwargs["task_id"], kwargs["tool_call_id"], kwargs["tool_name"],
            "hash", kwargs["user_id"], kwargs.get("org_id") or "",
        )
        return ConfirmationRequest(
            "confirmation", "waiter", binding,
            {"description": "已脱敏操作"}, kwargs["safety_level"],
        )

    with patch("services.tool_confirmation.tool_confirmation_service") as service:
        service.create = AsyncMock(side_effect=create)
        service.reject_unavailable = AsyncMock()
        service.await_and_claim = AsyncMock(return_value=ConfirmationDecision(
            ConfirmationOutcome.APPROVED, "EXECUTION_CLAIMED",
        ))
        yield service
