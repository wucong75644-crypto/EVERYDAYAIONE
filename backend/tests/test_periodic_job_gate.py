"""Worker 周期任务租约客户端合同。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.periodic_job_gate import (
    claim_periodic_job,
    finish_periodic_job,
    renew_periodic_job,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["busy", "completed"])
async def test_claim_accepts_non_owner_outcomes(outcome: str) -> None:
    db = MagicMock()
    with patch(
        "services.periodic_job_gate._execute_rpc",
        new=AsyncMock(return_value={"outcome": outcome}),
    ):
        claim = await claim_periodic_job(db, "model_scoring")

    assert claim.outcome == outcome
    assert claim.lease_token is None
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_claim_requires_token_for_owner() -> None:
    db = MagicMock()
    with patch(
        "services.periodic_job_gate._execute_rpc",
        new=AsyncMock(return_value={"outcome": "claimed"}),
    ):
        with pytest.raises(
            RuntimeError,
            match="PERIODIC_JOB_CLAIM_TOKEN_MISSING",
        ):
            await claim_periodic_job(db, "wecom_dup_monitor")


@pytest.mark.asyncio
async def test_finish_requires_finished_outcome() -> None:
    db = MagicMock()
    with patch(
        "services.periodic_job_gate._execute_rpc",
        new=AsyncMock(return_value={"outcome": "lease_lost"}),
    ):
        with pytest.raises(RuntimeError, match="PERIODIC_JOB_FINISH_INVALID"):
            await finish_periodic_job(
                db,
                "model_scoring",
                "token",
                succeeded=False,
            )


@pytest.mark.asyncio
async def test_renew_requires_renewed_outcome() -> None:
    with patch(
        "services.periodic_job_gate._execute_rpc",
        new=AsyncMock(return_value={"outcome": "renewed"}),
    ) as execute:
        await renew_periodic_job(
            MagicMock(),
            "model_scoring",
            "token",
        )

    assert execute.await_args.args[1] == "worker_renew_periodic_job"
