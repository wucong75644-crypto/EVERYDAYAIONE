"""历史生成 Turn 关系回填测试。"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from scripts.backfill_generation_turns import (
    RepairPlan,
    _candidate_sql,
    append_audit,
    apply_plan,
    audit_invariants,
    classify_candidate,
    fetch_batch,
    load_checkpoint,
    run,
    save_checkpoint,
)


OUTPUT_ID = "00000000-0000-4000-8000-000000000001"
INPUT_ID = "00000000-0000-4000-8000-000000000002"
TURN_ID = "00000000-0000-4000-8000-000000000003"


def _row(**values):
    return {
        "output_message_id": OUTPUT_ID,
        "output_created_at": datetime(2026, 7, 22, tzinfo=timezone.utc),
        "output_turn_id": None,
        "output_reply_to_message_id": None,
        "task_relations": [], "reply_relations": [],
        "same_turn_relations": [], "previous_relations": [],
        **values,
    }


def _relation(input_id=INPUT_ID, turn_id=TURN_ID):
    return {"input_id": input_id, "turn_id": turn_id}


@pytest.mark.parametrize("source", [
    "task_relations", "reply_relations", "same_turn_relations", "previous_relations",
])
def test_classify_accepts_each_deterministic_source(source):
    plan = classify_candidate(_row(**{source: [_relation()]}))

    assert plan.status == "repair"
    assert plan.input_message_id == INPUT_ID
    assert plan.turn_id == TURN_ID


def test_classify_uses_task_before_lower_priority_sources():
    plan = classify_candidate(_row(
        task_relations=[_relation()],
        reply_relations=[_relation("00000000-0000-4000-8000-000000000004")],
    ))

    assert plan.reason == "task"
    assert plan.input_message_id == INPUT_ID


def test_classify_rejects_multiple_authoritative_task_relations():
    plan = classify_candidate(_row(task_relations=[
        _relation(),
        _relation("00000000-0000-4000-8000-000000000004"),
    ]))

    assert plan.status == "conflict"
    assert plan.reason == "task_relations_conflict"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("output_turn_id", "00000000-0000-4000-8000-000000000004", "existing_turn_conflict"),
        ("output_reply_to_message_id", "00000000-0000-4000-8000-000000000004", "existing_reply_conflict"),
    ],
)
def test_classify_never_overwrites_existing_conflict(field, value, reason):
    plan = classify_candidate(_row(task_relations=[_relation()], **{field: value}))

    assert plan.status == "conflict"
    assert plan.reason == reason


def test_classify_recognizes_valid_and_ambiguous_rows():
    valid = classify_candidate(_row(
        output_turn_id=TURN_ID, output_reply_to_message_id=INPUT_ID,
        task_relations=[_relation()],
    ))
    ambiguous = classify_candidate(_row())

    assert valid.status == "already_valid"
    assert ambiguous.status == "ambiguous"


def test_apply_plan_uses_conditional_update():
    cursor = MagicMock()
    cursor.fetchone.return_value = (OUTPUT_ID,)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    plan = RepairPlan(OUTPUT_ID, INPUT_ID, TURN_ID, "repair", "task")

    assert apply_plan(conn, plan) is True
    sql = cursor.execute.call_args.args[0]
    assert "turn_id IS NULL OR turn_id =" in sql
    assert "reply_to_message_id IS NULL OR reply_to_message_id =" in sql


def test_apply_plan_ignores_non_repair_plan():
    assert apply_plan(
        MagicMock(), RepairPlan(OUTPUT_ID, None, None, "ambiguous", "none"),
    ) is False


def test_fetch_batch_applies_keyset_org_and_contiguous_lock():
    cursor = MagicMock()
    cursor.fetchall.return_value = [{"output_message_id": OUTPUT_ID}]
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor

    rows = fetch_batch(
        conn,
        cursor_value={"created_at": "2026-07-22T00:00:00+00:00", "id": OUTPUT_ID},
        batch_size=25, org_id="00000000-0000-4000-8000-000000000009",
        lock=True,
    )

    assert rows[0]["output_message_id"] == OUTPUT_ID
    sql, params = cursor.execute.call_args.args
    assert "FOR UPDATE OF o" in sql
    assert "SKIP LOCKED" not in sql
    assert "o.org_id = %s::uuid" in sql
    assert params[-1] == 25


def test_audit_invariants_normalizes_database_counts():
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "missing_input": 1, "invalid_role": 2,
        "scope_mismatch": 3, "turn_mismatch": 4,
    }
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor

    assert audit_invariants(conn) == {
        "missing_input": 1, "invalid_role": 2,
        "scope_mismatch": 3, "turn_mismatch": 4,
    }


def test_checkpoint_and_audit_never_store_message_content(tmp_path: Path):
    checkpoint = tmp_path / "checkpoint.json"
    audit = tmp_path / "audit.jsonl"
    value = {"created_at": "2026-07-22T00:00:00+00:00", "id": OUTPUT_ID}
    save_checkpoint(checkpoint, value)
    append_audit(audit, [{
        "output_message_id": OUTPUT_ID, "new_turn_id": TURN_ID,
        "new_reply_to_message_id": INPUT_ID,
    }])

    assert load_checkpoint(checkpoint) == value
    assert "content" not in audit.read_text(encoding="utf-8")
    checkpoint.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoint is invalid"):
        load_checkpoint(checkpoint)


def test_candidate_sql_is_keyset_bounded_and_lockable():
    sql = _candidate_sql(["o.role = 'assistant'"], " FOR UPDATE OF o")

    assert "ORDER BY o.created_at, o.id LIMIT %s" in sql
    assert "FOR UPDATE OF o" in sql
    assert "SKIP LOCKED" not in sql
    assert " OFFSET " not in sql


def test_run_dry_run_rolls_back_without_checkpoint(tmp_path: Path):
    conn = MagicMock()
    checkpoint = tmp_path / "checkpoint.json"
    with (
        patch("scripts.backfill_generation_turns.audit_invariants", return_value={"turn_mismatch": 0}),
        patch("scripts.backfill_generation_turns.fetch_batch", side_effect=[
            [_row(task_relations=[_relation()])], [],
        ]),
        patch("scripts.backfill_generation_turns.apply_plan") as apply,
    ):
        stats, before, after = run(
            conn, apply=False, batch_size=10, limit=None, org_id=None,
            checkpoint_path=checkpoint, audit_path=tmp_path / "audit.jsonl",
        )

    assert stats.repaired == 1
    assert before == after == {"turn_mismatch": 0}
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    apply.assert_not_called()
    assert not checkpoint.exists()


def test_run_apply_audits_before_update_and_checkpoints(tmp_path: Path):
    conn = MagicMock()
    checkpoint = tmp_path / "checkpoint.json"
    audit = tmp_path / "audit.jsonl"
    events = MagicMock()
    with (
        patch("scripts.backfill_generation_turns.audit_invariants", return_value={}),
        patch("scripts.backfill_generation_turns.fetch_batch", side_effect=[
            [_row(task_relations=[_relation()])], [],
        ]),
        patch("scripts.backfill_generation_turns.append_audit", side_effect=lambda *args: events("audit")),
        patch("scripts.backfill_generation_turns.apply_plan", side_effect=lambda *args: events("apply") or True),
    ):
        run(
            conn, apply=True, batch_size=10, limit=None, org_id=None,
            checkpoint_path=checkpoint, audit_path=audit,
        )

    assert events.call_args_list == [call("audit"), call("apply")]
    conn.commit.assert_called_once()
    assert json.loads(checkpoint.read_text())["id"] == OUTPUT_ID


def test_run_failed_batch_rolls_back_without_checkpoint(tmp_path: Path):
    conn = MagicMock()
    checkpoint = tmp_path / "checkpoint.json"
    with (
        patch("scripts.backfill_generation_turns.audit_invariants", return_value={}),
        patch("scripts.backfill_generation_turns.fetch_batch", return_value=[
            _row(task_relations=[_relation()]),
        ]),
        patch("scripts.backfill_generation_turns.append_audit"),
        patch("scripts.backfill_generation_turns.apply_plan", return_value=False),
    ):
        with pytest.raises(RuntimeError, match="CONCURRENT_CONFLICT"):
            run(
                conn, apply=True, batch_size=10, limit=1, org_id=None,
                checkpoint_path=checkpoint, audit_path=tmp_path / "audit.jsonl",
            )

    conn.rollback.assert_called_once()
    assert not checkpoint.exists()
