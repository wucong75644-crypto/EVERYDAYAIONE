import pytest

from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.scheduled_wecom_delivery import (
    PostgresScheduledWecomDeliveryRepository,
)
from tests.test_agent_runtime_scheduled_wecom_repository import (
    ORG,
    RECONCILE_REQUEST,
    _Database,
    _reconcile,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("claim", "renew", "read"))
async def test_reconcile_org_is_canonical_and_cross_tenant_drift_fails_closed(
    operation: str,
) -> None:
    response = _reconcile("renewed" if operation == "renew" else "readback")
    response["org_id"] = ORG.upper()
    responses = {
        "claim_agent_runtime_scheduled_wecom_reconcile_v1": _reconcile(),
        "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1": response,
        "read_agent_runtime_scheduled_wecom_reconcile_v1": response,
    }
    repository = PostgresScheduledWecomDeliveryRepository(_Database(responses))
    if operation == "claim":
        responses["claim_agent_runtime_scheduled_wecom_reconcile_v1"] = response
        claim = await repository.claim_reconcile(
            request_id=RECONCILE_REQUEST, worker_id="reconciler",
        )
        assert claim is not None and claim.org_id == ORG
        return

    claim = await repository.claim_reconcile(
        request_id=RECONCILE_REQUEST, worker_id="reconciler",
    )
    assert claim is not None
    response["org_id"] = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    with pytest.raises(PersistenceContractError, match="reconcile_identity_changed"):
        if operation == "renew":
            await repository.renew_reconcile(claim)
        else:
            await repository.read_reconcile(claim)


@pytest.mark.asyncio
@pytest.mark.parametrize("org_id", (None, "not-a-uuid", ""))
async def test_reconcile_org_malformed_fails_closed(org_id: object) -> None:
    response = _reconcile()
    response["org_id"] = org_id
    repository = PostgresScheduledWecomDeliveryRepository(_Database({
        "claim_agent_runtime_scheduled_wecom_reconcile_v1": response,
    }))
    with pytest.raises(
        PersistenceContractError,
        match="SCHEDULED_WECOM_RPC_CONTRACT_INVALID:org_id",
    ):
        await repository.claim_reconcile(
            request_id=RECONCILE_REQUEST, worker_id="reconciler",
        )
