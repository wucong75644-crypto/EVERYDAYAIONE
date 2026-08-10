"""Disposable PostgreSQL parity for Scheduled WeCom App receipts."""

from psycopg.types.json import Jsonb
import pytest

from services.agent.runtime.application.scheduled_wecom_receipt import (
    scheduled_wecom_receipt_hash,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    DispatchOutcome,
    ProviderDispatchIdentity,
    ReceiptMetadata,
    ReceiptType,
)
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external import (
    _owner,
    _setup,
)


pytestmark = pytest.mark.external
IDENTITY = ProviderDispatchIdentity(
    provider_request_id="scheduled-wecom-app:" + "a" * 64,
    idempotency_key="b" * 64,
    provider_revision=4,
)


@pytest.mark.parametrize(
    ("outcome", "code", "metadata"),
    (
        (
            DispatchOutcome.ACCEPTED,
            "acknowledged",
            ReceiptMetadata(provider_message_id="msg-001", wecom_errcode=0),
        ),
        (
            DispatchOutcome.REJECTED,
            "provider_rejected",
            ReceiptMetadata(wecom_errcode=40013),
        ),
        (
            DispatchOutcome.REJECTED,
            "provider_partial_rejected",
            ReceiptMetadata(wecom_errcode=0),
        ),
    ),
)
def test_python_app_receipt_hash_matches_sql_canonical_contract(
    database: str,
    outcome: DispatchOutcome,
    code: str,
    metadata: ReceiptMetadata,
) -> None:
    _setup(database)
    metadata_json = {
        key: value for key, value in {
            "provider_message_id": metadata.provider_message_id,
            "wecom_errcode": metadata.wecom_errcode,
        }.items() if value is not None
    }
    sql_hash = _owner(
        database,
        "SELECT _agent_runtime_scheduled_wecom_receipt_hash(%s,%s,%s,%s::jsonb,%s,%s,%s)",
        (
            outcome.value,
            ReceiptType.WECOM_APP.value,
            code,
            Jsonb(metadata_json),
            IDENTITY.provider_request_id,
            IDENTITY.idempotency_key,
            IDENTITY.provider_revision,
        ),
    )

    assert scheduled_wecom_receipt_hash(
        dispatch_outcome=outcome,
        receipt_type=ReceiptType.WECOM_APP,
        receipt_code=code,
        metadata=metadata,
        identity=IDENTITY,
    ) == sql_hash
