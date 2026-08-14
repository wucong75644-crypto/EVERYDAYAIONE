from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _rollback
from tests.test_agent_runtime_scheduled_wecom_claim_postgres_external import _owner, _rpc
from tests.test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external import (
    ROLLBACK,
    _fact_state,
    _identity,
    _owner_execute,
    _prepare_params,
    _read_params,
    _seed,
    _setup,
    _start_params,
)


pytestmark = pytest.mark.external


def _attempt_identity(url: str, attempt_id: str) -> object:
    return _owner(
        url,
        "SELECT jsonb_build_object('attempt_id',id,'provider_request_id',provider_request_id,"
        "'idempotency_key',idempotency_key,'provider_revision',provider_revision,"
        "'claim_request_id',claim_request_id,'lease_token',lease_token,"
        "'claim_worker_id',claim_worker_id,'delivery_version',prepared_delivery_state_version,"
        "'item_version',prepared_item_state_version) "
        "FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=%s",
        (attempt_id,),
    )


def _expire(url: str, intent_id: str) -> None:
    _owner(
        url,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE intent_id=%s RETURNING intent_id",
        (intent_id,),
    )


def test_expired_prepared_attempt_recovery_has_one_winner_and_stable_identity(
    database: str,
) -> None:
    _setup(database)
    old_claim, item = _seed(database)
    prepared = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(old_claim, item, _identity()),
    )
    original = _attempt_identity(database, prepared["attempt_id"])
    _expire(database, old_claim["intent_id"])

    before_invalid = _fact_state(database)
    recovery_params = (str(uuid4()), "recovery-validation-worker", 60)
    for index in range(len(recovery_params)):
        invalid = list(recovery_params)
        invalid[index] = None
        with pytest.raises(Exception, match="RECOVERY_INVALID"):
            _rpc(
                database, "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
                tuple(invalid),
            )
    assert _fact_state(database) == before_invalid

    requests = [str(uuid4()) for _ in range(50)]
    with ThreadPoolExecutor(max_workers=50) as pool:
        recovered = list(pool.map(
            lambda pair: _rpc(
                database, "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
                (pair[1], f"recovery-worker-{pair[0]}", 60),
            ),
            enumerate(requests),
        ))
    winners = [row for row in recovered if row["outcome"] == "recovered"]
    assert len(winners) == 1
    assert sum(row["outcome"] == "empty" for row in recovered) == 49
    winner = winners[0]
    replay = _rpc(
        database, "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
        (winner["claim_request_id"], winner["worker_id"], 60),
    )
    assert replay["outcome"] == "readback" and replay["attempt_id"] == prepared["attempt_id"]
    with pytest.raises(Exception, match="RECOVERY_REQUEST_CONFLICT"):
        _rpc(
            database, "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
            (winner["claim_request_id"], "conflicting-worker", 60),
        )
    assert _attempt_identity(database, prepared["attempt_id"]) == original
    assert _rpc(
        database, "read_agent_runtime_scheduled_wecom_dispatch_attempt_v1",
        _read_params(old_claim, prepared),
    )["outcome"] == "fenced"

    _expire(database, winner["intent_id"])
    successor = _rpc(
        database, "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
        (str(uuid4()), "successor-worker", 60),
    )
    assert successor["outcome"] == "recovered"
    old_replay = _rpc(
        database, "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
        (winner["claim_request_id"], winner["worker_id"], 60),
    )
    assert old_replay["outcome"] == "fenced"
    assert old_replay["attempt_id"] == prepared["attempt_id"]
    assert old_replay["lease_token"] == winner["lease_token"]

    start_params = _start_params(database, successor, item, prepared)
    with ThreadPoolExecutor(max_workers=50) as pool:
        started = list(pool.map(
            lambda _: _rpc(
                database, "start_agent_runtime_scheduled_wecom_dispatch_v1", start_params,
            ),
            range(50),
        ))
    assert sum(row["outcome"] == "dispatch_started" for row in started) == 1
    assert sum(row["outcome"] == "readback" for row in started) == 49
    for stale_claim in (old_claim, winner):
        assert _rpc(
            database, "start_agent_runtime_scheduled_wecom_dispatch_v1",
            _start_params(database, stale_claim, item, prepared),
        )["outcome"] == "fenced"

    other_claim, other_item = _seed(database)
    other_prepared = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(other_claim, other_item, _identity()),
    )
    _expire(database, other_claim["intent_id"])
    before_old_replay = _fact_state(database)
    old_replay_with_other_candidate = _rpc(
        database, "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
        (winner["claim_request_id"], winner["worker_id"], 60),
    )
    assert old_replay_with_other_candidate["outcome"] == "fenced"
    assert old_replay_with_other_candidate["attempt_id"] == prepared["attempt_id"]
    assert old_replay_with_other_candidate["attempt_id"] != other_prepared["attempt_id"]
    assert _fact_state(database) == before_old_replay
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts",
    ) == 2
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_prepared_recovery_requests",
    ) == 2
    with pytest.raises(Exception, match="RECOVERY_REQUEST_IMMUTABLE"):
        _owner_execute(
            database,
            "UPDATE agent_runtime_scheduled_wecom_prepared_recovery_requests "
            "SET worker_id='mutated-worker' WHERE request_id=%s",
            (winner["claim_request_id"],),
        )
    with pytest.raises(Exception, match="DISPATCH_ROLLBACK_HAS_FACTS"):
        _rollback(database, ROLLBACK)
