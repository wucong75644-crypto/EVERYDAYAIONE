"""Real PostgreSQL AR-12 recovery, dependency, and lock-order races."""

from concurrent.futures import ThreadPoolExecutor
import json

import psycopg
import pytest

from tests.test_agent_runtime_action_postgres_external import (
    action_batch,
    database_batch_hash,
    decoded,
    dedicated_database,
    execute,
    seed_running_tool_step,
    terminal,
)

pytestmark = pytest.mark.external


def test_lost_claim_response_is_recovered_without_second_batch() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    terminal(ids, actions, database_batch_hash(ids["step"], actions))
    first = decoded(execute(
        "SELECT claim_ready_agent_actions('lost-worker','lost-response',1,120)",
        worker=True,
    )[0][0])
    recovered = decoded(execute(
        "SELECT get_agent_action_claim_batch('lost-worker','lost-response')",
        worker=True,
    )[0][0])
    replay = decoded(execute(
        "SELECT claim_ready_agent_actions('lost-worker','lost-response',1,120)",
        worker=True,
    )[0][0])
    assert recovered["attempts"] == first["attempts"] == replay["attempts"]
    assert execute(
        "SELECT count(*) FROM agent_action_attempts WHERE claim_request_id=%s",
        ("lost-response",),
    )[0][0] == len(first["attempts"])
    stolen = decoded(execute(
        "SELECT get_agent_action_claim_batch('other-worker','lost-response')",
        worker=True,
    )[0][0])
    assert stolen["outcome"] == "claim_request_conflict"
    run_version = execute(
        "SELECT state_version FROM agent_runs WHERE id=%s", (ids["run"],),
    )[0][0]
    execute(
        "SELECT cancel_agent_run(%s,%s,'claim_readback_cancel')",
        (ids["run"], run_version), worker=True,
    )
    closed = decoded(execute(
        "SELECT get_agent_action_claim_batch('lost-worker','lost-response')",
        worker=True,
    )[0][0])
    target = next(
        item for item in closed["attempts"] if item["run_id"] == str(ids["run"])
    )
    assert target["status"] == "cancelled"
    assert target["ended_at"] is not None


def test_rejected_dependency_fails_before_any_terminal_mutation() -> None:
    ids = seed_running_tool_step()
    rejected = action_batch(ids, blocking=False)[0]
    rejected["policy_decision"] = "rejected"
    queued = action_batch(ids, blocking=True)[0]
    queued.update({
        "index": 1, "stable_tool_call_id": "call-1",
        "provider_call_id": "provider-call-1",
        "dependencies": [rejected["action_id"]],
    })
    actions = [rejected, queued]
    batch_hash = database_batch_hash(ids["step"], actions)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        terminal(ids, actions, batch_hash)
    assert execute(
        "SELECT status FROM agent_model_attempts WHERE id=%s", (ids["attempt"],),
    )[0][0] == "dispatching"
    assert execute(
        "SELECT status FROM agent_model_credit_settlements WHERE model_step_id=%s",
        (ids["step"],),
    )[0][0] == "reserved"
    assert execute(
        "SELECT count(*) FROM agent_actions WHERE model_step_id=%s", (ids["step"],),
    )[0][0] == 0


def test_authorization_dependency_waits_and_cancel_closes_batch() -> None:
    ids = seed_running_tool_step()
    authorization = action_batch(ids, blocking=True)[0]
    authorization["policy_decision"] = "requires_authorization"
    queued = action_batch(ids, blocking=True)[0]
    queued.update({
        "index": 1, "stable_tool_call_id": "call-1",
        "provider_call_id": "provider-call-1",
        "dependencies": [authorization["action_id"]],
    })
    actions = [authorization, queued]
    terminal(ids, actions, database_batch_hash(ids["step"], actions))
    execute(
        "SELECT claim_ready_agent_actions('auth-worker','auth-claim',10,120)",
        worker=True,
    )
    assert execute(
        "SELECT count(*) FROM agent_action_attempts WHERE action_id=%s",
        (queued["action_id"],),
    )[0][0] == 0
    run_version = execute(
        "SELECT state_version FROM agent_runs WHERE id=%s", (ids["run"],),
    )[0][0]
    execute(
        "SELECT cancel_agent_run(%s,%s,'authorization_cancel')",
        (ids["run"], run_version), worker=True,
    )
    assert execute(
        "SELECT array_agg(status ORDER BY action_index) FROM agent_actions "
        "WHERE run_id=%s", (ids["run"],),
    )[0][0] == ["cancelled", "cancelled"]
    assert execute(
        "SELECT status,blocking_action_count FROM agent_runs WHERE id=%s",
        (ids["run"],),
    )[0] == ("cancelled", 0)


def test_tool_terminal_and_cancel_have_one_fenced_winner() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    batch_hash = database_batch_hash(ids["step"], actions)
    with ThreadPoolExecutor(max_workers=2) as pool:
        terminal_future = pool.submit(terminal, ids, actions, batch_hash)
        cancel_future = pool.submit(
            execute, "SELECT cancel_agent_run(%s,0,'terminal_race')",
            (ids["run"],), worker=True,
        )
        outcomes = {
            terminal_future.result()["outcome"],
            decoded(cancel_future.result()[0][0])["outcome"],
        }
    assert outcomes <= {
        "cancelled", "completed", "stale_version", "ownership_lost",
    }
    assert execute(
        "SELECT status,blocking_action_count FROM agent_runs WHERE id=%s",
        (ids["run"],),
    )[0] in (("cancelled", 0), ("waiting_actions", 1))


def test_action_terminal_and_cancel_have_one_fenced_winner() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    terminal(ids, actions, database_batch_hash(ids["step"], actions))
    claim = decoded(execute(
        "SELECT claim_ready_agent_actions('race-worker','action-race',10,120)",
        worker=True,
    )[0][0])
    attempt = next(
        item for item in claim["attempts"] if item["run_id"] == str(ids["run"])
    )
    result = json.dumps({
        "status": "error", "summary": "failed", "data": {},
        "artifact_ids": [], "usage": {}, "cost": {},
        "external_receipt": {}, "error_code": "TOOL_FAILED",
    })
    with ThreadPoolExecutor(max_workers=2) as pool:
        finish_future = pool.submit(
            execute, "SELECT fail_agent_action(%s,%s,0,%s,%s::jsonb)",
            (
                attempt["id"], attempt["execution_token"],
                actions[0]["request_hash"], result,
            ), worker=True,
        )
        cancel_future = pool.submit(
            execute, "SELECT cancel_agent_run(%s,1,'action_race')",
            (ids["run"],), worker=True,
        )
        outcomes = {
            decoded(finish_future.result()[0][0])["outcome"],
            decoded(cancel_future.result()[0][0])["outcome"],
        }
    assert outcomes <= {"failed", "cancelled", "stale_version", "run_cancelled"}
    assert execute(
        "SELECT count(*) FROM agent_action_results WHERE action_id=%s",
        (actions[0]["action_id"],),
    )[0][0] in (0, 1)


def test_still_unknown_and_cancel_have_one_fenced_winner() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    terminal(ids, actions, database_batch_hash(ids["step"], actions))
    claim = decoded(execute(
        "SELECT claim_ready_agent_actions('rec-worker','rec-race',10,120)",
        worker=True,
    )[0][0])
    attempt = next(
        item for item in claim["attempts"] if item["run_id"] == str(ids["run"])
    )
    dispatch = decoded(execute(
        "SELECT mark_agent_action_dispatching(%s,%s,0,%s)",
        (attempt["id"], attempt["execution_token"], actions[0]["request_hash"]),
        worker=True,
    )[0][0])
    unknown = decoded(execute(
        "SELECT record_agent_action_unknown(%s,%s,%s,%s,%s::jsonb)",
        (
            attempt["id"], attempt["execution_token"], dispatch["state_version"],
            actions[0]["request_hash"], '{"kind":"race"}',
        ), worker=True,
    )[0][0])
    reconciliation = decoded(execute(
        "SELECT claim_agent_action_reconciliation(%s,%s,'reconciler',120)",
        (attempt["id"], unknown["state_version"]), worker=True,
    )[0][0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        resolve_future = pool.submit(
            execute, "SELECT resolve_agent_action_reconciliation("
            "%s,%s,%s,%s,'still_unknown',NULL,%s::jsonb)",
            (
                attempt["id"], reconciliation["execution_token"],
                reconciliation["state_version"], actions[0]["request_hash"],
                '{"kind":"still_unproven"}',
            ), worker=True,
        )
        cancel_future = pool.submit(
            execute, "SELECT cancel_agent_run(%s,1,'reconcile_race')",
            (ids["run"],), worker=True,
        )
        outcomes = {
            decoded(resolve_future.result()[0][0])["outcome"],
            decoded(cancel_future.result()[0][0])["outcome"],
        }
    assert outcomes <= {
        "still_unknown", "cancelled", "stale_version", "run_cancelled",
    }
