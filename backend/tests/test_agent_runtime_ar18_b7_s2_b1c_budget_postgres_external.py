from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import _rpc
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import (
    USER, _scheduled_command_run,
)
from tests.test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_postgres_external import (
    _bound_run, _prepare, _request_rpc,
)


pytestmark = pytest.mark.external
MIGRATION = "227_34_agent_runtime_scheduled_run_credit_budget.sql"
ROLLBACK = "rollback/227_34_agent_runtime_scheduled_run_credit_budget_rollback.sql"


def _setup(url: str) -> None:
    _prepare(url)
    for name in (
        "227_32_agent_runtime_scheduled_finalization_apply.sql",
        "227_33_agent_runtime_scheduled_finalization_context.sql",
        MIGRATION,
    ):
        _apply(url, name)
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE users SET credits=1000 WHERE id=%s", (USER,))
        conn.commit()


def _step(url: str, run_id: str, number: int) -> dict[str, object]:
    step_id = str(uuid4())
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        row = conn.execute(
            "SELECT session_id,org_id,user_id FROM agent_runs WHERE id=%s", (run_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,step_number,"
            "status,model_id,provider,model_revision,prompt_revision,tool_catalog_revision) "
            "VALUES(%s,%s,%s,%s,%s,%s,'running','test-model','test-provider','v1','v1','v1')",
            (step_id, run_id, row[0], row[1], row[2], number),
        )
        version = conn.execute(
            "SELECT state_version FROM agent_model_steps WHERE id=%s", (step_id,),
        ).fetchone()[0]
        conn.commit()
    return {"id": step_id, "version": version}


def _prepare_step(url: str, facts: dict, step: dict, reserve: int, key: str) -> dict:
    return _rpc(url, "prepare_model_attempt", (
        step["id"], facts["token"], step["version"], "budget-worker", "a" * 64,
        key, "test-provider", {}, reserve, 90,
    ))


def _budget(url: str, run_id: str) -> tuple:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT max_credits,reserved_credits,settled_credits,"
            "pending_adjustment_credits,adjusted_credits FROM "
            "agent_runtime_scheduled_run_credit_budgets WHERE runtime_run_id=%s",
            (run_id,),
        ).fetchone()


def _start(url: str, facts: dict, attempt: dict) -> dict:
    return _rpc(url, "start_model_attempt_dispatch", (
        attempt["attempt_id"], facts["token"], attempt["state_version"], "a" * 64,
    ))


def _complete(url: str, facts: dict, step: dict, attempt: dict, actual: int) -> dict:
    started = _start(url, facts, attempt)
    return _rpc(url, "complete_model_attempt_without_actions", (
        attempt["attempt_id"], facts["token"], started["state_version"], step["version"],
        "a" * 64, {}, "b" * 64, "final", None,
        {"input_tokens": 2, "output_tokens": 3}, actual,
    ))


def _cancel_unknown(url: str, facts: dict, step: dict, attempt: dict) -> None:
    started = _start(url, facts, attempt)
    assert _rpc(url, "record_model_attempt_unknown", (
        attempt["attempt_id"], facts["token"], started["state_version"], "a" * 64,
        "request_started", "reconcile_only", {"reason": "response_lost"},
    ))["outcome"] == "unknown"
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_runtime_sessions SET scope_kind='user',scope_id=%s WHERE id=("
                     "SELECT session_id FROM agent_runs WHERE id=%s)", (USER, facts["run_id"]))
        conn.commit()
    assert _request_rpc(url, "test_b1a_cancel_agent_run", (
        facts["run_id"], facts["version"], "runtime_cancel",
    ))["outcome"] == "cancelled"


def test_ordinary_run_delegates_without_budget_fact(database: str) -> None:
    _setup(database)
    scheduled = _bound_run(database)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_run_credit_budgets",
        ).fetchone()[0] == 0
    _, run_id = _scheduled_command_run(
        database, scheduled["task_id"], scheduled["scheduled_run_id"], run_kind="user",
    )
    token = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_runs SET status='running',execution_token=%s,"
                     "lease_expires_at=clock_timestamp()+interval '5 minutes' WHERE id=%s",
                     (token, run_id))
        conn.commit()
    facts = {"run_id": run_id, "token": token}
    result = _prepare_step(database, facts, _step(database, run_id, 1), 3, "ordinary")
    assert result["outcome"] == "prepared"
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_run_credit_budgets WHERE runtime_run_id=%s",
            (run_id,),
        ).fetchone()[0] == 0


def test_ordinary_late_actual_keeps_legacy_full_charge(database: str) -> None:
    _setup(database)
    scheduled = _bound_run(database)
    _, run_id = _scheduled_command_run(
        database, scheduled["task_id"], scheduled["scheduled_run_id"], run_kind="user",
    )
    token = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_runs SET status='running',execution_token=%s,"
                     "lease_expires_at=clock_timestamp()+interval '5 minutes' WHERE id=%s",
                     (token, run_id))
        version = conn.execute(
            "SELECT state_version FROM agent_runs WHERE id=%s", (run_id,),
        ).fetchone()[0]
        conn.commit()
    facts = {"run_id": run_id, "token": token, "version": version}
    step = _step(database, run_id, 1)
    attempt = _prepare_step(database, facts, step, 20, "ordinary-late")
    _cancel_unknown(database, facts, step, attempt)
    with psycopg.connect(database) as conn:
        before = conn.execute("SELECT credits FROM users WHERE id=%s", (USER,)).fetchone()[0]
    result = _rpc(database, "record_late_model_receipt", (
        attempt["attempt_id"], "ordinary-provider", {}, "d" * 64,
        {"input_tokens": 100}, "completed", {"readback": True}, 100,
    ))
    assert result["outcome"] == "recorded"
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT credits FROM users WHERE id=%s", (USER,)).fetchone()[0] == before - 100
        assert conn.execute(
            "SELECT adjusted_credits FROM agent_model_credit_settlements WHERE model_step_id=%s",
            (step["id"],),
        ).fetchone()[0] == 100
        assert conn.execute("SELECT count(*) FROM agent_runtime_scheduled_credit_overages").fetchone()[0] == 0


def test_multi_step_settle_release_and_replay_preserve_cap(database: str) -> None:
    _setup(database)
    facts = _bound_run(database)
    first = _step(database, facts["run_id"], 1)
    one = _prepare_step(database, facts, first, 6, "step-1")
    assert _prepare_step(database, facts, first, 6, "step-1")["outcome"] == "already_prepared"
    assert _complete(database, facts, first, one, 4)["outcome"] == "completed"
    second = _step(database, facts["run_id"], 2)
    two = _prepare_step(database, facts, second, 6, "step-2")
    assert two["outcome"] == "prepared"
    with psycopg.connect(database) as conn:
        attempt_version = conn.execute(
            "SELECT state_version FROM agent_model_attempts WHERE id=%s", (two["attempt_id"],),
        ).fetchone()[0]
    failed = _rpc(database, "fail_model_attempt_and_step", (
        two["attempt_id"], facts["token"], attempt_version, second["version"],
        "a" * 64, "safe_failure", "forbidden",
    ))
    assert failed["outcome"] == "failed"
    assert _budget(database, facts["run_id"]) == (10, 0, 4, 0, 0)


def test_frozen_budget_ignores_later_task_max_credits(database: str) -> None:
    _setup(database)
    facts = _bound_run(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE scheduled_tasks SET max_credits=99 WHERE id=%s", (facts["task_id"],))
        conn.commit()
    step = _step(database, facts["run_id"], 1)
    assert _prepare_step(database, facts, step, 10, "frozen-old")["outcome"] == "prepared"
    assert _budget(database, facts["run_id"]) == (10, 10, 0, 0, 0)
    assert _prepare_step(
        database, facts, _step(database, facts["run_id"], 2), 1, "frozen-exhausted",
    )["outcome"] == "budget_exhausted"

    new_facts = _bound_run(database)
    new_step = _step(database, new_facts["run_id"], 1)
    assert _prepare_step(database, new_facts, new_step, 10, "frozen-new")["outcome"] == "prepared"
    assert _budget(database, new_facts["run_id"]) == (10, 10, 0, 0, 0)


def test_concurrent_steps_and_same_step_replay_are_atomic(database: str) -> None:
    _setup(database)
    facts = _bound_run(database)
    steps = [_step(database, facts["run_id"], index + 1) for index in range(50)]
    with ThreadPoolExecutor(max_workers=25) as pool:
        outcomes = list(pool.map(
            lambda item: _prepare_step(database, facts, item[1], 1, f"parallel-{item[0]}")["outcome"],
            enumerate(steps),
        ))
    assert outcomes.count("prepared") == 10
    assert outcomes.count("budget_exhausted") == 40
    assert _budget(database, facts["run_id"]) == (10, 10, 0, 0, 0)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_model_attempts WHERE run_id=%s", (facts["run_id"],),
        ).fetchone()[0] == 10

    other = _bound_run(database)
    step = _step(database, other["run_id"], 1)
    with ThreadPoolExecutor(max_workers=20) as pool:
        replay = list(pool.map(
            lambda _: _prepare_step(database, other, step, 2, "same-step")["outcome"], range(20),
        ))
    assert replay.count("prepared") == 1
    assert replay.count("already_prepared") == 19
    assert _budget(database, other["run_id"]) == (10, 2, 0, 0, 0)


def test_late_overage_charges_only_remaining_budget_and_replays_once(database: str) -> None:
    _setup(database)
    facts = _bound_run(database)
    first = _step(database, facts["run_id"], 1)
    assert _complete(database, facts, first,
                     _prepare_step(database, facts, first, 7, "settled"), 7)["outcome"] == "completed"
    second = _step(database, facts["run_id"], 2)
    attempt = _prepare_step(database, facts, second, 3, "late")
    started = _start(database, facts, attempt)
    unknown = _rpc(database, "record_model_attempt_unknown", (
        attempt["attempt_id"], facts["token"], started["state_version"], "a" * 64,
        "request_started", "reconcile_only", {"reason": "response_lost"},
    ))
    assert unknown["outcome"] == "unknown"
    assert _budget(database, facts["run_id"]) == (10, 3, 7, 0, 0)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_runtime_sessions SET scope_kind='user',scope_id=%s WHERE id=("
                     "SELECT session_id FROM agent_runs WHERE id=%s)", (USER, facts["run_id"]))
        conn.commit()
    assert _request_rpc(database, "test_b1a_cancel_agent_run", (
        facts["run_id"], facts["version"], "runtime_cancel",
    ))["outcome"] == "cancelled"
    assert _budget(database, facts["run_id"]) == (10, 0, 7, 0, 0)
    before = None
    with psycopg.connect(database) as conn:
        before = conn.execute("SELECT credits FROM users WHERE id=%s", (USER,)).fetchone()[0]
    late = _rpc(database, "record_late_model_receipt", (
        attempt["attempt_id"], "provider-late", {}, "c" * 64,
        {"input_tokens": 5}, "completed", {"readback": True}, 4,
    ))
    assert late["outcome"] == "recorded"
    replay = _rpc(database, "record_late_model_receipt", (
        attempt["attempt_id"], "provider-late", {}, "c" * 64,
        {"input_tokens": 5}, "completed", {"readback": True}, 4,
    ))
    assert replay["outcome"] == "already_recorded"
    conflict = _rpc(database, "record_late_model_receipt", (
        attempt["attempt_id"], "provider-late", {}, "c" * 64,
        {"input_tokens": 5}, "completed", {"readback": True}, 5,
    ))
    assert conflict["outcome"] == "receipt_conflict"
    with psycopg.connect(database) as conn:
        row = conn.execute(
            "SELECT late_outcome,late_actual_credits FROM agent_model_attempts WHERE id=%s",
            (attempt["attempt_id"],),
        ).fetchone()
        assert row == ("completed", 4)
        assert conn.execute("SELECT credits FROM users WHERE id=%s", (USER,)).fetchone()[0] == before - 3
        overage = conn.execute(
            "SELECT provider_actual_credits,user_charge_credits,overage_credits,status "
            "FROM agent_runtime_scheduled_credit_overages WHERE attempt_id=%s",
            (attempt["attempt_id"],),
        ).fetchone()
        assert overage == (4, 3, 1, "reconcile_required")
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_credit_overages WHERE attempt_id=%s",
            (attempt["attempt_id"],),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT status,adjusted_credits FROM agent_model_credit_settlements WHERE model_step_id=%s",
            (second["id"],),
        ).fetchone() == ("adjusted", 3)
    assert _budget(database, facts["run_id"]) == (10, 0, 7, 0, 3)


def test_late_overage_survives_insufficient_balance_as_pending(database: str) -> None:
    _setup(database)
    facts = _bound_run(database)
    step = _step(database, facts["run_id"], 1)
    attempt = _prepare_step(database, facts, step, 10, "pending-overage")
    _cancel_unknown(database, facts, step, attempt)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE users SET credits=0 WHERE id=%s", (USER,))
        conn.commit()
    result = _rpc(database, "record_late_model_receipt", (
        attempt["attempt_id"], "pending-provider", {}, "e" * 64,
        {"input_tokens": 15}, "completed", {"readback": True}, 15,
    ))
    assert result["outcome"] == "adjustment_pending"
    with psycopg.connect(database) as conn:
        settlement = conn.execute(
            "SELECT status,adjusted_credits FROM agent_model_credit_settlements WHERE model_step_id=%s",
            (step["id"],),
        ).fetchone()
        assert settlement == ("adjustment_pending", 10)
        assert conn.execute(
            "SELECT provider_actual_credits,user_charge_credits,overage_credits "
            "FROM agent_runtime_scheduled_credit_overages WHERE attempt_id=%s",
            (attempt["attempt_id"],),
        ).fetchone() == (15, 10, 5)
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE users SET credits=100 WHERE id=%s", (USER,))
        conn.commit()
    replay = _rpc(database, "record_late_model_receipt", (
        attempt["attempt_id"], "pending-provider", {}, "e" * 64,
        {"input_tokens": 15}, "completed", {"readback": True}, 15,
    ))
    assert replay["outcome"] == "already_recorded"
    conflict = _rpc(database, "record_late_model_receipt", (
        attempt["attempt_id"], "changed-provider", {}, "d" * 64,
        {"input_tokens": 16}, "completed", {"readback": True}, 16,
    ))
    assert conflict["outcome"] == "receipt_conflict"
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT credits FROM users WHERE id=%s", (USER,)).fetchone()[0] == 100
        assert conn.execute(
            "SELECT status,adjusted_credits FROM agent_model_credit_settlements WHERE model_step_id=%s",
            (step["id"],),
        ).fetchone() == ("adjustment_pending", 10)
        assert conn.execute("SELECT count(*) FROM agent_runtime_scheduled_credit_overages").fetchone()[0] == 1
    assert _budget(database, facts["run_id"]) == (10, 0, 0, 10, 0)


def test_late_actual_within_remaining_has_no_overage(database: str) -> None:
    _setup(database)
    facts = _bound_run(database)
    step = _step(database, facts["run_id"], 1)
    attempt = _prepare_step(database, facts, step, 5, "within-cap")
    _cancel_unknown(database, facts, step, attempt)
    with psycopg.connect(database) as conn:
        before = conn.execute("SELECT credits FROM users WHERE id=%s", (USER,)).fetchone()[0]
    result = _rpc(database, "record_late_model_receipt", (
        attempt["attempt_id"], "within-provider", {}, "f" * 64,
        {"input_tokens": 4}, "completed", {"readback": True}, 4,
    ))
    assert result["outcome"] == "recorded"
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT credits FROM users WHERE id=%s", (USER,)).fetchone()[0] == before - 4
        assert conn.execute("SELECT count(*) FROM agent_runtime_scheduled_credit_overages").fetchone()[0] == 0
    assert _budget(database, facts["run_id"]) == (10, 0, 0, 0, 4)


def test_late_adjustment_and_prepare_do_not_deadlock(database: str) -> None:
    _setup(database)
    late_facts = _bound_run(database)
    late_step = _step(database, late_facts["run_id"], 1)
    late_attempt = _prepare_step(database, late_facts, late_step, 10, "race-late")
    _cancel_unknown(database, late_facts, late_step, late_attempt)
    prepare_facts = _bound_run(database)
    prepare_step = _step(database, prepare_facts["run_id"], 1)
    with psycopg.connect(database) as conn:
        before = conn.execute("SELECT credits FROM users WHERE id=%s", (USER,)).fetchone()[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        late_future = pool.submit(_rpc, database, "record_late_model_receipt", (
            late_attempt["attempt_id"], "race-provider", {}, "1" * 64,
            {"input_tokens": 15}, "completed", {"readback": True}, 15,
        ))
        prepare_future = pool.submit(
            _prepare_step, database, prepare_facts, prepare_step, 10, "race-prepare",
        )
        assert late_future.result(timeout=15)["outcome"] == "recorded"
        assert prepare_future.result(timeout=15)["outcome"] == "prepared"
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT credits FROM users WHERE id=%s", (USER,)).fetchone()[0] == before - 20
    assert _budget(database, late_facts["run_id"]) == (10, 0, 0, 0, 10)
    assert _budget(database, prepare_facts["run_id"]) == (10, 10, 0, 0, 0)


def test_apply_fails_closed_for_historical_scheduled_settlement(database: str) -> None:
    _prepare(database)
    for name in (
        "227_32_agent_runtime_scheduled_finalization_apply.sql",
        "227_33_agent_runtime_scheduled_finalization_context.sql",
    ):
        _apply(database, name)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE users SET credits=1000 WHERE id=%s", (USER,))
        conn.commit()
    facts = _bound_run(database)
    step = _step(database, facts["run_id"], 1)
    assert _rpc(database, "prepare_model_attempt", (
        step["id"], facts["token"], step["version"], "historical-worker",
        "a" * 64, "historical", "test-provider", {}, 1, 90,
    ))["outcome"] == "prepared"
    with pytest.raises(Exception, match="HISTORICAL_FACTS_EXIST"):
        _apply(database, MIGRATION)


def test_acl_source_conflict_and_rollback_guard(database: str) -> None:
    _setup(database)
    facts = _bound_run(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("ALTER TABLE agent_runtime_scheduled_execution_profiles DISABLE TRIGGER "
                     "runtime_scheduled_profile_immutable")
        conn.execute("UPDATE agent_runtime_scheduled_execution_profiles SET budget_snapshot="
                     "jsonb_set(budget_snapshot,'{max_credits}','9') WHERE scheduled_task_id=%s",
                     (facts["task_id"],))
        conn.execute("ALTER TABLE agent_runtime_scheduled_execution_profiles ENABLE TRIGGER "
                     "runtime_scheduled_profile_immutable")
        conn.commit()
    with pytest.raises(Exception, match="BUDGET_SOURCE_INVALID"):
        _prepare_step(database, facts, _step(database, facts["run_id"], 1), 1, "bad-source")
    with psycopg.connect(database) as conn:
        for table in (
            "agent_runtime_scheduled_run_credit_budgets",
            "agent_runtime_scheduled_model_credit_allocations",
            "agent_runtime_scheduled_credit_overages",
        ):
            assert conn.execute(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname=%s", (table,),
            ).fetchone()[0]
            assert not conn.execute(
                "SELECT has_table_privilege('everydayai_agent_runtime_worker',%s,'SELECT')",
                (table,),
            ).fetchone()[0]
            assert not conn.execute(
                "SELECT has_table_privilege('everydayai_worker',%s,'SELECT')", (table,),
            ).fetchone()[0]
        signature = (
            "prepare_model_attempt(uuid,uuid,bigint,text,text,text,text,jsonb,integer,integer)"
        )
        assert conn.execute(
            "SELECT proconfig FROM pg_proc WHERE oid=%s::regprocedure", (signature,),
        ).fetchone()[0] == ["search_path=pg_catalog, public"]
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_agent_runtime_worker',%s,'EXECUTE')",
            (signature,),
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_function_privilege('everydayai_worker',%s,'EXECUTE')", (signature,),
        ).fetchone()[0]
        for private_signature in (
            "_prepare_model_attempt_without_scheduled_budget_v1(uuid,uuid,bigint,text,text,text,text,jsonb,integer,integer)",
            "_settle_agent_model_credits_without_scheduled_budget_v1(agent_model_steps,uuid,text,integer)",
            "_release_agent_model_credits_without_scheduled_budget_v1(uuid)",
            "_adjust_model_attempt_credits_without_scheduled_budget_v1(uuid,text,integer)",
        ):
            assert not conn.execute(
                "SELECT has_function_privilege('everydayai_agent_runtime_worker',%s,'EXECUTE')",
                (private_signature,),
            ).fetchone()[0]

    clean = _bound_run(database)
    _prepare_step(database, clean, _step(database, clean["run_id"], 1), 1, "guard")
    with pytest.raises(Exception, match="ROLLBACK_FACTS_EXIST"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("TRUNCATE agent_runtime_scheduled_credit_overages,"
                     "agent_runtime_scheduled_model_credit_allocations,"
                     "agent_runtime_scheduled_run_credit_budgets")
        conn.commit()
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("TRUNCATE agent_model_credit_settlements")
        conn.commit()
    _apply(database, MIGRATION)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("TRUNCATE agent_runtime_scheduled_credit_overages,"
                     "agent_runtime_scheduled_model_credit_allocations,"
                     "agent_runtime_scheduled_run_credit_budgets")
        conn.commit()
    _apply(database, ROLLBACK)
