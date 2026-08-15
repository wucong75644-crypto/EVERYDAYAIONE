#!/usr/bin/env python3
"""Fail-closed one-off rollback of the 2026-08 Runtime production cutover."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import psycopg


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "backend/migrations"
ROLLBACKS = MIGRATIONS / "rollback"
LOCK_KEY = "everydayai:schema-migrations:v1"
BOUNDARY = "227_12_agent_runtime_cost_side_effect_observability.sql"
FIRST = "227_13_agent_runtime_additive_ingress_compatibility.sql"
LAST = "228_08q_agent_runtime_single_owner_convergence.sql"
EXPECTED_TARGET_MIGRATIONS = 280
EXPECTED_LANE = 89
EXPECTED_ROLLBACK_BUNDLE_SHA256 = (
    "7408205254a50e16abd2f9b2572fe2fab2f0fc2044bdabfa50375f333db26108"
)

USER_ID = "7be35735-e97a-4a2e-a65b-1104ca4ee36a"
SESSION_IDS = (
    "76adab33-0988-4ada-b1f0-d9f668430c9f",
    "3b87eb16-a81c-46f6-accd-8d77b6040d76",
    "77e87cfe-8d90-442d-b857-dbe08b4a7394",
)
CONVERSATION_IDS = (
    "74407d12-f723-43da-b51b-30b42d573e14",
    "5aa0fd2f-c821-469a-aa3a-2c9ddddda51e",
    "2a24adaa-c22a-4587-9e72-772f23f55a1c",
)
SETTLEMENT_IDS = (
    "1fa08e16-e9f2-4853-9488-4a21f3d14457",
    "99c34f0f-3542-404a-89ec-767cc0d5babc",
    "ba70e0bd-f63f-4648-b8e1-c77c4c7afec5",
    "144bb56b-eb8e-4712-9930-e1dced25fd5c",
    "13b36d34-2909-48c8-ba8b-4556fe97ea86",
    "a0241b7a-869b-43c2-bc81-52cc40d625a3",
    "bb40f30a-65a7-477e-b970-3b6e33540bdb",
    "91dbe089-517c-408e-b6b6-059108039f2b",
    "96c2a09a-abfb-4b0d-b60b-2942df4fd3fd",
    "c394c91e-dfda-4871-ab94-27a9b3ecce55",
    "d68bdddf-abde-4bb2-97f7-d9eecf9eabdd",
    "6bd5e7d0-b299-42b9-8a69-3e285f9247ce",
)
CREDIT_TRANSACTION_IDS = (
    "10571805-302d-4ce5-be82-7a8c0260556b",
    "d096510f-48da-4e8a-8cfa-df39732695e9",
    "02909d83-0949-4db8-9e34-16b8f4c3e64e",
    "5717b2ab-8c38-4e20-9719-327dc9317fc5",
    "0c385ade-1884-41fd-b12d-c0d30fa75f4b",
    "daadbfad-8f04-43e2-9bc5-534a229e05e2",
    "9ca19eb1-2742-47ab-89a1-6398b8f5b595",
    "b90f5dce-ba9c-42e3-ada2-927692b99e41",
    "7a909e65-250b-463e-b393-292434fab6a0",
    "cfdb534f-9558-40fa-9749-663e188c31b6",
    "b5310e8a-bbdd-4b10-8fe1-fef165f82ced",
    "84c97772-d961-40a8-8825-a4c3caf3e750",
)
CREDIT_HISTORY_IDS = (
    "f4d5eb16-e204-4695-8ef9-b7021bd5c8cd", "b90620d9-4144-4ad5-a16e-f4647be85328",
    "ded53203-fd5b-4e88-9760-29bf58c68aed", "f0309394-0254-4e90-b3b4-7ae5b552990b",
    "b3689892-b5c2-4074-b82f-3792b4ce929d", "a89ac25e-1cb2-4910-bce3-666dcc9ec4d0",
    "1a21265c-a29a-4e49-8493-452ae14eef86", "1d816889-d4b6-4749-afd7-85c05bf508bb",
    "c6997d8f-2dba-4dc2-ba04-0f43385e60dc", "7ad3b6d9-4454-422b-b789-ea1b85ff5a6a",
    "08eafa0a-0743-4377-a6b2-8f79b3c6fe3f", "f564a685-228a-4c92-a27c-dfdb990a41b6",
    "7fb7c9d7-6ed7-4b99-94df-3fb6c6a10493", "5f652af4-76ff-46d2-88f5-674f5d572ad2",
    "788b2728-e03e-4628-81be-9fdbd3d35290", "ac951bd1-08ce-49fc-b4ff-1e8e7ff9bf2b",
    "63e0af49-7b34-4961-99fa-a7ce6a6d7126",
    "ff817f56-db3a-40a0-8adf-bf00e370e920",
    "b957c2f5-4518-49c0-acab-eaec74f53b4f",
    "c58bd990-3de3-4b26-9fca-5ed73224a6ef",
    "fef27fe7-28a5-4d17-a3aa-f8389ca1d8a9",
    "1acb28ee-02a2-4a32-a4ed-5f6a44fc216c",
    "03088483-7d4b-4807-8628-31a1bbe1cc3d",
    "fdc1b023-3884-456c-984b-9abbb3a2de14",
)

SESSION_COUNTS = {
    "agent_action_attempts": 3,
    "agent_action_cost_settlements": 2,
    "agent_action_dispatch_intents": 2,
    "agent_actions": 2,
    "agent_command_claims": 15,
    "agent_compat_projection_checkpoints": 5,
    "agent_compat_projection_results": 61,
    "agent_model_attempts": 12,
    "agent_model_results": 7,
    "agent_model_steps": 12,
    "agent_policy_receipts": 3,
    "agent_projection_dead_recoveries": 1,
    "agent_projection_outbox": 154,
    "agent_runs": 15,
    "agent_runtime_events": 103,
    "agent_runtime_sessions": 3,
    "agent_session_commands": 15,
}


class RollbackError(RuntimeError):
    pass


@dataclass(frozen=True)
class RollbackItem:
    identity: str
    path: Path


def _sort_key(name: str) -> tuple[int, str]:
    match = re.match(r"^(\d+)_", name)
    return (int(match.group(1)) if match else sys.maxsize, name)


def _target_migrations() -> dict[str, str]:
    result: dict[str, str] = {}
    paths = filter(lambda path: _sort_key(path.name) <= _sort_key(BOUNDARY), [*MIGRATIONS.glob("*.sql"), *MIGRATIONS.glob("*.py")])
    for path in sorted(paths, key=lambda value: _sort_key(value.name)):
        result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if len(result) != EXPECTED_TARGET_MIGRATIONS or BOUNDARY not in result:
        raise RollbackError("rollback target migration inventory changed")
    return result


def _rollback_lane() -> list[RollbackItem]:
    items: list[RollbackItem] = []
    for path in ROLLBACKS.glob("*_rollback.sql"):
        identity = f"{path.name.removesuffix('_rollback.sql')}.sql"
        if _sort_key(FIRST) <= _sort_key(identity) <= _sort_key(LAST):
            items.append(RollbackItem(identity, path))
    items.sort(key=lambda item: _sort_key(item.identity))
    if len(items) != EXPECTED_LANE or items[0].identity != FIRST:
        raise RollbackError("rollback SQL lane changed")
    if items[-1].identity != LAST:
        raise RollbackError("rollback SQL terminal identity changed")
    digest = hashlib.sha256()
    for item in items:
        digest.update(item.identity.encode())
        digest.update(b"\0")
        digest.update(item.path.read_bytes())
        digest.update(b"\0")
    if digest.hexdigest() != EXPECTED_ROLLBACK_BUNDLE_SHA256:
        raise RollbackError("rollback SQL bundle checksum changed")
    return items


def _ledger(connection: Any) -> dict[str, tuple[str, str]]:
    rows = connection.execute(
        "SELECT identity,checksum_sha256,status FROM schema_migration_ledger"
    ).fetchall()
    return {identity: (checksum, status) for identity, checksum, status in rows}


def _remaining_lane(connection: Any) -> list[RollbackItem]:
    target = _target_migrations()
    lane = _rollback_lane()
    ledger = _ledger(connection)
    unknown = sorted(set(ledger) - set(target) - {item.identity for item in lane})
    if unknown:
        raise RollbackError(f"unexpected ledger identities: {unknown}")
    for identity, checksum in target.items():
        row = ledger.get(identity)
        if row != (checksum, "applied"):
            raise RollbackError(f"target migration mismatch: {identity}")
    applied = [item for item in lane if item.identity in ledger]
    if applied != lane[: len(applied)]:
        raise RollbackError("rollback lane is not a contiguous resumable prefix")
    if any(ledger[item.identity][1] != "applied" for item in applied):
        raise RollbackError("rollback lane contains non-applied ledger rows")
    if len(ledger) != len(target) + len(applied):
        raise RollbackError("migration ledger cardinality changed")
    return applied


def _scalar(connection: Any, sql: str, params: Iterable[Any] = ()) -> Any:
    return connection.execute(sql, tuple(params)).fetchone()[0]


def _assert_ids(connection: Any, table: str, expected: tuple[str, ...]) -> None:
    actual = {
        str(row[0]) for row in connection.execute(
            f"SELECT id FROM {table} WHERE id=ANY(%s::uuid[])",
            (list(expected),),
        ).fetchall()
    }
    if actual != set(expected):
        raise RollbackError(f"{table} identity set changed")


def _session_scope(table: str) -> str:
    if table == "agent_runtime_sessions":
        return "id=ANY(%s::uuid[])"
    if table == "agent_action_dispatch_intents":
        return (
            "action_id IN (SELECT id FROM agent_actions "
            "WHERE session_id=ANY(%s::uuid[]))"
        )
    if table == "agent_action_cost_settlements":
        return (
            "run_id IN (SELECT id FROM agent_runs "
            "WHERE session_id=ANY(%s::uuid[]))"
        )
    return "session_id=ANY(%s::uuid[])"


def _data_state(connection: Any) -> str:
    sessions = {
        str(row[0])
        for row in connection.execute("SELECT id FROM agent_runtime_sessions").fetchall()
    }
    conversations = {
        str(row[0])
        for row in connection.execute(
            "SELECT id FROM conversations WHERE id=ANY(%s::uuid[])",
            (list(CONVERSATION_IDS),),
        ).fetchall()
    }
    balance = _scalar(connection, "SELECT credits FROM users WHERE id=%s", (USER_ID,))
    if not sessions and not conversations:
        remaining_history = _scalar(
            connection,
            "SELECT count(*) FROM credits_history WHERE id=ANY(%s::uuid[])",
            (list(CREDIT_HISTORY_IDS),),
        )
        remaining_transactions = _scalar(
            connection,
            "SELECT count(*) FROM credit_transactions WHERE id=ANY(%s::uuid[])",
            (list(CREDIT_TRANSACTION_IDS),),
        )
        if balance == 909 and remaining_history == 0 and remaining_transactions == 0:
            return "cleaned"
        raise RollbackError("partially cleaned Runtime business data")
    if sessions != set(SESSION_IDS) or conversations != set(CONVERSATION_IDS):
        raise RollbackError("Runtime session or conversation inventory changed")
    if balance != 891:
        raise RollbackError("billing user balance changed")
    return "present"


def _validate_data(connection: Any, *, require_drained: bool = True) -> None:
    session_array = list(SESSION_IDS)
    for table, expected in SESSION_COUNTS.items():
        count = _scalar(
            connection,
            f"SELECT count(*) FROM {table} WHERE {_session_scope(table)}",
            (session_array,),
        )
        if count != expected:
            raise RollbackError(f"{table} count changed: {count}")
    for table, expected in (("agent_run_attempts", 21),):
        count = _scalar(
            connection,
            f"SELECT count(*) FROM {table} WHERE run_id IN ("
            "SELECT id FROM agent_runs WHERE session_id=ANY(%s::uuid[]))",
            (session_array,),
        )
        if count != expected:
            raise RollbackError(f"{table} count changed: {count}")
    _assert_ids(connection, "agent_model_credit_settlements", SETTLEMENT_IDS)
    _assert_ids(connection, "credit_transactions", CREDIT_TRANSACTION_IDS)
    settlement_totals = connection.execute(
        "SELECT count(*),sum(reserved_credits),sum(settled_credits),"
        "count(*) FILTER(WHERE status='released'),"
        "count(*) FILTER(WHERE status='settled') "
        "FROM agent_model_credit_settlements WHERE id=ANY(%s::uuid[]) "
        "AND billing_user_id=%s",
        (list(SETTLEMENT_IDS), USER_ID),
    ).fetchone()
    if settlement_totals != (12, 216, 18, 3, 9):
        raise RollbackError("Runtime model credit settlements changed")
    transaction_totals = connection.execute(
        "SELECT count(*),sum(amount),count(*) FILTER(WHERE status='refunded'),"
        "count(*) FILTER(WHERE status='confirmed') FROM credit_transactions "
        "WHERE id=ANY(%s::uuid[]) AND user_id=%s",
        (list(CREDIT_TRANSACTION_IDS), USER_ID),
    ).fetchone()
    if transaction_totals != (12, 216, 3, 9):
        raise RollbackError("Runtime credit transactions changed")
    history = connection.execute(
        "SELECT id,change_amount FROM credits_history "
        "WHERE id=ANY(%s::uuid[])", (list(CREDIT_HISTORY_IDS),),
    ).fetchall()
    if {str(row[0]) for row in history} != set(CREDIT_HISTORY_IDS):
        raise RollbackError("Runtime credit history identity set changed")
    if sum(row[1] for row in history) != -18:
        raise RollbackError("Runtime credit history net amount changed")
    latest = _scalar(
        connection,
        "SELECT id FROM credits_history WHERE user_id=%s "
        "ORDER BY created_at DESC,id DESC LIMIT 1", (USER_ID,),
    )
    if str(latest) != CREDIT_HISTORY_IDS[-1]:
        raise RollbackError("billing user has newer credit history")
    for table, expected in (("tasks", 15), ("messages", 30),
                            ("message_generation_requests", 15)):
        count = _scalar(
            connection,
            f"SELECT count(*) FROM {table} WHERE conversation_id=ANY(%s::uuid[])",
            (list(CONVERSATION_IDS),),
        )
        if count != expected:
            raise RollbackError(f"{table} count changed: {count}")
    forbidden_references = (
        ("agent_runtime_conversation_scope_adoptions", "conversation_id"),
        ("image_generations", "conversation_id"),
        ("user_asset_refs", "conversation_id"),
        ("conversation_artifacts", "conversation_id"),
        ("conversation_attachment_refs", "conversation_id"),
        ("conversation_context_items", "conversation_id"),
        ("conversation_context_receipts", "conversation_id"),
        ("conversation_data_evidence", "conversation_id"),
        ("pending_interaction", "conversation_id"),
    )
    for table, column in forbidden_references:
        count = _scalar(
            connection,
            f"SELECT count(*) FROM {table} WHERE {column}=ANY(%s::uuid[])",
            (list(CONVERSATION_IDS),),
        )
        if count:
            raise RollbackError(f"{table} gained business references")
    dependent_references = (
        ("user_asset_refs_message", "user_asset_refs", "source_message_id", "messages"),
        ("user_asset_refs_task", "user_asset_refs", "source_task_id", "tasks"),
        ("task_attachment_refs", "task_attachment_refs", "task_id", "tasks"),
        ("conversation_deliveries", "conversation_deliveries", "task_id", "tasks"),
    )
    for label, table, column, parent in dependent_references:
        count = _scalar(
            connection,
            f"SELECT count(*) FROM {table} WHERE {column} IN (SELECT id FROM {parent} "
            "WHERE conversation_id=ANY(%s::uuid[]))",
            (list(CONVERSATION_IDS),),
        )
        if count:
            raise RollbackError(f"{label} gained business references")
    active_workers = _scalar(
        connection,
        "SELECT count(*) FROM agent_runtime_worker_heartbeats "
        "WHERE process_role IN ('runtime','projection','authorization','sandbox') "
        "AND (ready OR NOT draining OR status_code<>'draining')",
    )
    if require_drained and active_workers:
        raise RollbackError("Runtime workers are not fully drained")


def _delete_exact(connection: Any, table: str, where: str, params: tuple[Any, ...],
                  expected: int) -> None:
    cursor = connection.execute(f"DELETE FROM {table} WHERE {where}", params)
    if cursor.rowcount != expected:
        raise RollbackError(f"{table} delete count changed: {cursor.rowcount}")


def _cleanup_data(connection: Any) -> None:
    if _data_state(connection) == "cleaned":
        return
    _validate_data(connection)
    sessions = (list(SESSION_IDS),)
    with connection.transaction():
        attempt_ids = [
            row[0] for row in connection.execute(
                "SELECT id FROM agent_action_attempts WHERE session_id=ANY(%s::uuid[])",
                sessions,
            ).fetchall()
        ]
        claim_ids = [
            row[0] for row in connection.execute(
                "SELECT claim_request_id FROM agent_action_attempts "
                "WHERE session_id=ANY(%s::uuid[]) AND claim_request_id IS NOT NULL",
                sessions,
            ).fetchall()
        ]
        _delete_exact(connection, "agent_runtime_owner_fences",
                      "owner_kind='attempt' AND owner_id=ANY(%s::uuid[])",
                      (attempt_ids,), 3)
        connection.execute(
            "ALTER TABLE agent_safe_action_activations DISABLE TRIGGER "
            "trg_agent_safe_activation_immutable"
        )
        _delete_exact(connection, "agent_safe_action_activations",
                      "action_id IN (SELECT id FROM agent_actions "
                      "WHERE session_id=ANY(%s::uuid[]))", sessions, 1)
        connection.execute(
            "ALTER TABLE agent_safe_action_activations ENABLE TRIGGER "
            "trg_agent_safe_activation_immutable"
        )
        for table in (
            "agent_compat_projection_results", "agent_projection_dead_recoveries",
            "agent_compat_projection_checkpoints", "agent_projection_outbox",
            "agent_action_dispatch_intents", "agent_action_cost_settlements",
            "agent_policy_receipts", "agent_runtime_events",
        ):
            _delete_exact(connection, table, _session_scope(table), sessions,
                          SESSION_COUNTS[table])
        _delete_exact(connection, "agent_model_credit_settlements",
                      "id=ANY(%s::uuid[])", (list(SETTLEMENT_IDS),), 12)
        for table in ("agent_action_attempts", "agent_actions", "agent_model_results",
                      "agent_model_attempts", "agent_model_steps"):
            _delete_exact(connection, table, "session_id=ANY(%s::uuid[])", sessions,
                          SESSION_COUNTS[table])
        _delete_exact(connection, "agent_action_claim_batches",
                      "claim_request_id=ANY(%s::text[])", (claim_ids,), 3)
        _delete_exact(connection, "agent_run_attempts",
                      "run_id IN (SELECT id FROM agent_runs "
                      "WHERE session_id=ANY(%s::uuid[]))", sessions, 21)
        for table in ("agent_command_claims", "agent_runs", "agent_session_commands",
                      "agent_runtime_sessions"):
            _delete_exact(connection, table, _session_scope(table), sessions,
                          SESSION_COUNTS[table])
        _delete_exact(connection, "message_generation_requests",
                      "conversation_id=ANY(%s::uuid[])",
                      (list(CONVERSATION_IDS),), 15)
        _delete_exact(connection, "credits_history", "id=ANY(%s::uuid[])",
                      (list(CREDIT_HISTORY_IDS),), 24)
        updated = connection.execute(
            "UPDATE users SET credits=909,updated_at=clock_timestamp() "
            "WHERE id=%s AND credits=891 RETURNING id", (USER_ID,),
        ).fetchone()
        if updated is None:
            raise RollbackError("billing user balance changed during cleanup")
        _delete_exact(connection, "credit_transactions", "id=ANY(%s::uuid[])",
                      (list(CREDIT_TRANSACTION_IDS),), 12)
        _delete_exact(connection, "conversations", "id=ANY(%s::uuid[])",
                      (list(CONVERSATION_IDS),), 3)
    if _data_state(connection) != "cleaned":
        raise RollbackError("Runtime data cleanup did not converge")
    connection.commit()


def _rollback_schema(connection: Any) -> int:
    remaining = _remaining_lane(connection)
    for item in reversed(remaining):
        with connection.transaction():
            connection.execute(item.path.read_text(encoding="utf-8"))
            deleted = connection.execute(
                "DELETE FROM schema_migration_ledger "
                "WHERE identity=%s AND status='applied' RETURNING identity",
                (item.identity,),
            ).fetchone()
            if deleted != (item.identity,):
                raise RollbackError(f"ledger changed during rollback: {item.identity}")
        print(f"rolled_back={item.identity}")
    if _remaining_lane(connection):
        raise RollbackError("migration rollback did not converge")
    connection.commit()
    return len(remaining)


def run(database_url: str, execute: bool) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_KEY,))
        connection.commit()
        try:
            data_state = _data_state(connection)
            remaining = _remaining_lane(connection)
            if data_state == "present":
                _validate_data(connection, require_drained=execute)
            print(f"data_state={data_state}")
            print(f"remaining_migrations={len(remaining)}")
            if not execute:
                return
            _cleanup_data(connection)
            rolled_back = _rollback_schema(connection)
            print(f"schema_rolled_back={rolled_back}")
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_KEY,))
            connection.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    database_url = os.getenv("TENANT_DB_ADMIN_URL")
    if not database_url:
        print("TENANT_DB_ADMIN_URL is required", file=sys.stderr)
        return 2
    if args.execute and os.getenv("ALLOW_RUNTIME_CUTOVER_ROLLBACK") != "true":
        print("ALLOW_RUNTIME_CUTOVER_ROLLBACK=true is required", file=sys.stderr)
        return 2
    try:
        run(database_url, args.execute)
    except (RollbackError, psycopg.Error) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
