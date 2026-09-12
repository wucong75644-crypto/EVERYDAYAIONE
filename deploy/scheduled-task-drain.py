"""Run only under release.sh's owned production lock, before the 255 cutover.

Wait for existing runs; hold the task-table write lock while stopping all old
workers so no claim can slip between the empty check and service shutdown.
This script never changes task status or applies a migration.
"""
from pathlib import Path
import re
import subprocess
import sys
import time

import psycopg

SERVICES = (
    "everydayai-backend", "everydayai-sync", "everydayai-wecom",
    "everydayai-conversation-actor",
)


class DrainError(RuntimeError):
    """Only locally constructed, credential-free diagnostics may be reported."""


def assert_service_stopped(service, *, cgroup_root=Path("/sys/fs/cgroup")):
    properties = dict(line.split("=", 1) for line in subprocess.check_output(
        ["systemctl", "show", service, "--property=ActiveState,MainPID,ControlPID,ControlGroup"],
        text=True,
    ).splitlines() if "=" in line)
    # systemd retains `failed` after a stop timeout even when SIGKILL has removed
    # every process. Neither that label nor MainPID=0 alone proves a safe cutover.
    if (properties.get("ActiveState") not in {"inactive", "failed"}
            or properties.get("MainPID") != "0" or properties.get("ControlPID") != "0"):
        raise DrainError(f"SCHEDULED_DRAIN_SERVICE_NOT_STOPPED:{service}")
    group = properties.get("ControlGroup")
    if group is None:
        raise DrainError(f"SCHEDULED_DRAIN_PROCESS_CHECK_UNAVAILABLE:{service}")
    if not group:
        return  # systemd has no cgroup assigned to this stopped service.
    mount = cgroup_root if (cgroup_root / "cgroup.controllers").is_file() else cgroup_root / "systemd"
    if not mount.is_dir() or not group.startswith("/") or ".." in Path(group).parts:
        raise DrainError(f"SCHEDULED_DRAIN_PROCESS_CHECK_UNAVAILABLE:{service}")
    group_path = mount / group.lstrip("/")
    if not group_path.exists():
        return  # systemd already removed the empty cgroup.
    if not (group_path / "cgroup.procs").is_file():
        raise DrainError(f"SCHEDULED_DRAIN_PROCESS_CHECK_UNAVAILABLE:{service}")
    for processes in group_path.rglob("cgroup.procs"):
        try:
            if processes.read_text().strip():
                raise DrainError(f"SCHEDULED_DRAIN_SERVICE_PROCESSES_REMAIN:{service}")
        except FileNotFoundError:
            pass  # A stopped service's empty child cgroup may disappear.


def drain(conn, stop_services, *, timeout=900, clock=time.monotonic, sleep=time.sleep, report=print):
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            with conn.transaction():
                conn.execute("SET LOCAL lock_timeout = '1s'")
                conn.execute("LOCK TABLE public.scheduled_tasks IN SHARE ROW EXCLUSIVE MODE")
                running = conn.execute("SELECT count(*) FROM public.scheduled_tasks WHERE status='running'").fetchone()[0]
                if not running:
                    # Keep the transaction open until every old process has stopped.
                    stop_services()
                    report("SCHEDULED_DRAIN_RESULT status=stopped running=0")
                    return
                report(f"SCHEDULED_DRAIN_WAIT running={running}")
        except psycopg.errors.LockNotAvailable:
            report("SCHEDULED_DRAIN_WAIT task_write_in_progress=true")
        sleep(5)
    raise DrainError("SCHEDULED_DRAIN_TIMEOUT")


def main():
    token = sys.argv[1] if len(sys.argv) == 2 else ""
    if not re.fullmatch(r"[a-zA-Z0-9-]+", token):
        raise DrainError("RELEASE_OWNER_REQUIRED")
    owner = Path("/var/www/everydayai.release-lock/owner")

    def assert_owner():
        if owner.read_text().strip() != token:
            raise DrainError("RELEASE_OWNER_MISMATCH")

    def stop_services():
        assert_owner()
        subprocess.run(["sudo", "-n", "systemctl", "stop", *SERVICES], check=True, timeout=180)
        for service in SERVICES:
            assert_service_stopped(service)

    assert_owner()
    for service in SERVICES:
        loaded = subprocess.check_output(
            ["systemctl", "show", service, "--property=LoadState", "--value"], text=True,
        ).strip()
        if loaded != "loaded":
            raise DrainError(f"SCHEDULED_DRAIN_SERVICE_MISSING:{service}")
    # Read only the connection setting; do not export credentials or print a DSN.
    from dotenv import dotenv_values
    dsn = dotenv_values("/var/www/everydayai/backend/.env").get("DATABASE_URL")
    if not dsn:
        raise DrainError("SCHEDULED_DRAIN_DATABASE_NOT_CONFIGURED")
    with psycopg.connect(dsn, application_name="everydayai-release-drain", connect_timeout=10) as conn:
        drain(conn, stop_services)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Deployment diagnostics must not expose connection strings from exceptions.
        reason = f" reason={exc}" if isinstance(exc, DrainError) else ""
        print(f"SCHEDULED_DRAIN_RESULT status=failed error_type={type(exc).__name__}{reason}", file=sys.stderr)
        sys.exit(1)
