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


def service_state(monkeypatch, tmp_path, **overrides):
    properties = dict(ActiveState="failed", MainPID="0", ControlPID="0",
                      ControlGroup="/system.slice/everydayai-backend.service")
    properties.update(overrides)
    monkeypatch.setattr(module.subprocess, "check_output", Mock(
        return_value="\n".join(f"{key}={value}" for key, value in properties.items())))
    (tmp_path / "cgroup.controllers").write_text("pids")
    group = tmp_path / properties["ControlGroup"].lstrip("/")
    group.mkdir(parents=True, exist_ok=True)
    (group / "cgroup.procs").write_text("")
    return group


@pytest.mark.parametrize("state", ["inactive", "failed"])
def test_stopped_service_accepts_empty_cgroup_even_after_stop_timeout(monkeypatch, tmp_path, state):
    service_state(monkeypatch, tmp_path, ActiveState=state)
    module.assert_service_stopped("everydayai-backend", cgroup_root=tmp_path)


@pytest.mark.parametrize("properties", [
    {"ActiveState": "active"}, {"ActiveState": "deactivating"},
    {"MainPID": "123"}, {"ControlPID": "456"},
])
def test_service_with_active_state_or_process_never_allows_cutover(monkeypatch, tmp_path, properties):
    service_state(monkeypatch, tmp_path, **properties)
    with pytest.raises(module.DrainError, match="SERVICE_NOT_STOPPED"):
        module.assert_service_stopped("everydayai-backend", cgroup_root=tmp_path)


@pytest.mark.parametrize("child", [False, True])
def test_zero_main_pid_does_not_hide_surviving_cgroup_processes(monkeypatch, tmp_path, child):
    group = service_state(monkeypatch, tmp_path)
    if child:
        group = group / "worker"
        group.mkdir()
    (group / "cgroup.procs").write_text("123\n")
    with pytest.raises(module.DrainError, match="SERVICE_PROCESSES_REMAIN"):
        module.assert_service_stopped("everydayai-backend", cgroup_root=tmp_path)


def test_missing_cgroup_process_interface_fails_closed(monkeypatch, tmp_path):
    group = service_state(monkeypatch, tmp_path)
    (group / "cgroup.procs").unlink()
    with pytest.raises(module.DrainError, match="PROCESS_CHECK_UNAVAILABLE"):
        module.assert_service_stopped("everydayai-backend", cgroup_root=tmp_path)


def test_removed_empty_cgroup_is_stopped(monkeypatch, tmp_path):
    group = service_state(monkeypatch, tmp_path)
    (group / "cgroup.procs").unlink()
    group.rmdir()
    module.assert_service_stopped("everydayai-backend", cgroup_root=tmp_path)


def test_cgroup_v1_checks_systemd_hierarchy(monkeypatch, tmp_path):
    group = service_state(monkeypatch, tmp_path)
    (tmp_path / "cgroup.controllers").unlink()
    v1 = tmp_path / "systemd" / group.relative_to(tmp_path)
    v1.parent.mkdir(parents=True)
    group.rename(v1)
    module.assert_service_stopped("everydayai-backend", cgroup_root=tmp_path)
    (v1 / "cgroup.procs").write_text("123\n")
    with pytest.raises(module.DrainError, match="SERVICE_PROCESSES_REMAIN"):
        module.assert_service_stopped("everydayai-backend", cgroup_root=tmp_path)


def test_main_completes_cutover_after_systemd_timeout_with_no_remaining_processes(monkeypatch, tmp_path, capsys):
    token = "test-release-owner"
    owner = tmp_path / "owner"
    owner.write_text(token)
    real_path = Path
    monkeypatch.setattr(module, "Path", lambda path: owner if path.endswith("release-lock/owner") else real_path(path))
    monkeypatch.setattr(module.sys, "argv", [str(PATH), token])
    conn = Connection([0])
    monkeypatch.setattr(module.psycopg, "connect", Mock(return_value=Mock(
        __enter__=Mock(return_value=conn), __exit__=Mock(return_value=False))))
    import dotenv
    monkeypatch.setattr(dotenv, "dotenv_values", Mock(return_value={"DATABASE_URL": "test"}))
    stopped = []
    def stop(*args, **kwargs):
        assert conn.in_transaction
        stopped.append(True)
    monkeypatch.setattr(module.subprocess, "run", stop)
    def show(args, **kwargs):
        if "--property=LoadState" in args:
            return "loaded\n"
        if "--property=ActiveState" in args:
            return "failed\n"  # The production result rejected by the old check.
        return "ActiveState=failed\nMainPID=0\nControlPID=0\nControlGroup=\n"
    monkeypatch.setattr(module.subprocess, "check_output", show)
    module.main()
    assert stopped == [True]
    assert "status=stopped running=0" in capsys.readouterr().out
