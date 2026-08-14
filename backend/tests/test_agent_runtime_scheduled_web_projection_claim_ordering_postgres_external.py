from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar18_b7_s2_b1d1b_projection_postgres_external import (
    _finalized,
    _setup,
)


pytestmark = pytest.mark.external


def _claim_with_plan(database_url: str, index: int) -> dict:
    projection_url = database_url.replace(
        "postgres@", "everydayai_projection_worker@",
    )
    with psycopg.connect(projection_url) as connection:
        connection.execute("SELECT set_config('app.access_kind','projection',false)")
        if index % 2:
            connection.execute("SET enable_seqscan=off")
            connection.execute("SET enable_bitmapscan=off")
        else:
            connection.execute("SET enable_indexscan=off")
            connection.execute("SET enable_bitmapscan=off")
        return connection.execute(
            "SELECT claim_agent_runtime_scheduled_web_projection_v1(%s,%s,60)",
            (f"projection-stress-{index}", uuid4()),
        ).fetchone()[0]


def test_repeated_high_contention_claims_do_not_deadlock(database: str) -> None:
    _setup(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "CREATE INDEX test_runtime_delivery_target_reverse_claim_order "
            "ON agent_runtime_scheduled_delivery_targets("
            "target_type,scheduled_run_id DESC,target_key DESC,target_hash)"
        )

    for _round in range(4):
        expected_intents = {
            _finalized(database)[0]["scheduled_run_id"]
            for _ in range(6)
        }
        start = Barrier(50)

        def concurrent_claim(_index: int) -> dict:
            start.wait(timeout=15)
            return _claim_with_plan(database, _index)

        with ThreadPoolExecutor(max_workers=50) as pool:
            results = list(pool.map(concurrent_claim, range(50)))

        winners = [row for row in results if row["outcome"] == "claimed"]
        assert len(winners) == len(expected_intents)
        assert {row["scheduled_run_id"] for row in winners} == expected_intents
        assert len({row["intent_id"] for row in winners}) == len(winners)

    projection_url = database.replace(
        "postgres@", "everydayai_projection_worker@",
    )
    with psycopg.connect(projection_url) as connection:
        connection.execute("SELECT set_config('app.access_kind','projection',false)")
        assert connection.execute(
            "SELECT claim_agent_runtime_scheduled_web_projection_v1(%s,%s,60)",
            ("projection-no-gap", uuid4()),
        ).fetchone()[0]["outcome"] == "not_found"
        assert connection.execute(
            "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() "
            "AND locktype='advisory'"
        ).fetchone()[0] == 0
