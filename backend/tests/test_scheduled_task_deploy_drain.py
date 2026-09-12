"""Cutover waits for active work and closes the claim/stop race without task writes."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import psycopg
import pytest

PATH = Path(__file__).resolve().parents[2] / "deploy/scheduled-task-drain.py"
SPEC = importlib.util.spec_from_file_location("scheduled_task_drain", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class Connection:
    def __init__(self, counts):
        self.counts = iter(counts)
        self.in_transaction = False
        self.statements = []

    @contextmanager
    def transaction(self):
        self.in_transaction = True
        try:
            yield
        finally:
            self.in_transaction = False

    def execute(self, sql):
        assert self.in_transaction
        self.statements.append(sql)
        if sql.startswith("SELECT"):
            return Mock(fetchone=lambda: (next(self.counts),))


def test_waits_without_stopping_running_tasks_then_stops_under_claim_lock():
    conn = Connection([1, 0])
    stopped = []
    def stop():
        assert conn.in_transaction
        assert conn.statements[-2] == "LOCK TABLE public.scheduled_tasks IN SHARE ROW EXCLUSIVE MODE"
        stopped.append(True)
    def sleep(_):
        assert not conn.in_transaction and not stopped
    module.drain(conn, stop, sleep=sleep, report=lambda _: None)
    assert stopped == [True] and not conn.in_transaction
    assert all(not sql.startswith(("UPDATE", "DELETE", "INSERT")) for sql in conn.statements)


def test_timeout_never_stops_or_relabels_existing_runs():
    conn, stop = Connection([1]), Mock()
    ticks = iter([0, 0, 2])
    with pytest.raises(RuntimeError, match="SCHEDULED_DRAIN_TIMEOUT"):
        module.drain(conn, stop, timeout=1, clock=lambda: next(ticks), sleep=lambda _: None, report=lambda _: None)
    stop.assert_not_called()
    assert not conn.in_transaction


def test_service_stop_failure_propagates_and_does_not_report_success():
    conn, report = Connection([0]), Mock()
    with pytest.raises(RuntimeError, match="service failure"):
        module.drain(conn, Mock(side_effect=RuntimeError("service failure")), report=report)
    report.assert_not_called()
    assert not conn.in_transaction


def test_executor_cuts_over_after_local_checks_and_before_backend_sync():
    script = (PATH.parent / "deploy.sh").read_text()
    assert "build_backend\n        prepare_scheduled_task_cutover\n        sync_backend\n        apply_migrations\n        deploy_backend" in script
    assert "< deploy/scheduled-task-drain.py" in script
