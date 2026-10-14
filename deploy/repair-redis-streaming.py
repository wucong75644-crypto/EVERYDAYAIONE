#!/usr/bin/env python3
"""Stop only the specifically identified malicious Redis replication; dry-run by default.

This does not restore CONFIG, restart Redis, clear data, or release the deployment
lock. Evidence contains only the metadata needed to identify this incident.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import uuid
from typing import Any, Callable


class RepairRefused(RuntimeError):
    pass


def verify_lock(app_dir: Path, token: str | None) -> tuple[int, int, int, int]:
    if not token or not re.fullmatch(r"[A-Za-z0-9-]+", token):
        raise RepairRefused("release_lock_required")
    directory = Path(f"{app_dir}.release-lock")
    owner = directory / "owner"
    try:
        directory_stat, owner_stat = directory.lstat(), owner.lstat()
        if (not stat.S_ISDIR(directory_stat.st_mode)
                or not stat.S_ISREG(owner_stat.st_mode)
                or owner.read_text().rstrip("\n") != token):
            raise RepairRefused("release_lock_required")
    except OSError:
        raise RepairRefused("release_lock_required") from None
    return directory_stat.st_dev, directory_stat.st_ino, owner_stat.st_dev, owner_stat.st_ino


def create_client(env_file: Path) -> tuple[Any, str]:
    from dotenv import dotenv_values
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    if not env_file.is_file():
        raise RepairRefused("configuration_unavailable")
    values = dotenv_values(env_file, interpolate=False)
    host = values.get("REDIS_HOST") or "localhost"
    ssl_value = (values.get("REDIS_SSL") or "false").lower()
    if ssl_value not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        raise RepairRefused("configuration_invalid")
    client = Redis(
        host=host,
        port=int(values.get("REDIS_PORT") or 6379),
        password=values.get("REDIS_PASSWORD") or None,
        db=int(values.get("REDIS_DB") or 0),
        ssl=ssl_value in {"true", "1", "yes", "on"},
        decode_responses=True,
        socket_connect_timeout=15,
        socket_timeout=15,
        retry=Retry(NoBackoff(), 0),
        retry_on_timeout=False,
        retry_on_error=[],
    )
    return client, host


def _addresses(host: str) -> set[str]:
    try:
        return {str(ipaddress.ip_address(host))}
    except ValueError:
        return {row[4][0] for row in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}


def _role(info: dict[str, Any]) -> str:
    return info.get("role") if info.get("role") in {"master", "slave"} else "unknown"


def inspect(client: Any, host: str) -> dict[str, Any]:
    replication = client.info("replication")
    role = _role(replication)
    if role == "master":
        return {"role": role}
    master_host = replication.get("master_host")
    if (role != "slave" or not isinstance(master_host, str) or not master_host.strip()
            or replication.get("master_port") != 60100
            or replication.get("connected_slaves") != 0):
        raise RepairRefused("incident_guard_mismatch")
    master_host = master_host.strip()
    master_addresses, own_addresses = _addresses(master_host), _addresses(host)
    if (master_host.lower().rstrip(".") == host.lower().rstrip(".")
            or not master_addresses or not own_addresses
            or master_addresses & own_addresses
            or any(ipaddress.ip_address(address).is_loopback for address in master_addresses)):
        raise RepairRefused("incident_guard_mismatch")
    directory = client.config_get("dir").get("dir")
    filename = client.config_get("dbfilename").get("dbfilename")
    run_id = client.info("server").get("run_id")
    if (directory != "/etc" or filename != "crontab"
            or not isinstance(run_id, str) or not run_id.strip()):
        raise RepairRefused("incident_guard_mismatch")
    return {
        "run_id": run_id,
        "role": role,
        "master_host": master_host,
        "master_port": 60100,
        "connected_slaves": 0,
        "dir": directory,
        "dbfilename": filename,
    }


def save_evidence(app_dir: Path, metadata: dict[str, Any]) -> None:
    directory = app_dir / ".incident-evidence"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory_stat = directory.lstat()
    if not stat.S_ISDIR(directory_stat.st_mode) or directory_stat.st_mode & 0o077:
        raise RepairRefused("evidence_directory_unsafe")
    path = directory / f"redis-replication-{uuid.uuid4().hex}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(metadata, stream, ensure_ascii=True, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _postcondition(client: Any, expected_run_id: str) -> tuple[str, bool]:
    role = _role(client.info("replication"))
    run_id = client.info("server").get("run_id")
    return role, role == "master" and run_id == expected_run_id


def repair(
    app_dir: Path,
    *,
    apply: bool = False,
    lock_token: str | None = None,
    client_factory: Callable[[Path], tuple[Any, str]] | None = None,
) -> tuple[dict[str, Any], int]:
    client = None
    role = "unknown"
    try:
        if not app_dir.is_absolute() or app_dir == Path("/") or not app_dir.is_dir():
            raise RepairRefused("application_directory_invalid")
        lock_identity = verify_lock(app_dir, lock_token) if apply else None
        client, host = (client_factory or create_client)(app_dir / "backend" / ".env")
        metadata = inspect(client, host)
        role = metadata["role"]
        if role == "master":
            return {"status": "nochange", "role": role, "no_data_cleared": True}, 0
        if not apply:
            return {"status": "dry_run", "role": role, "no_data_cleared": True}, 0
        if verify_lock(app_dir, lock_token) != lock_identity:
            raise RepairRefused("release_lock_changed")
        save_evidence(app_dir, metadata)
        # Evidence is durable before the one mutation; lock ownership is checked
        # again immediately before dispatch. Never retry this command blindly.
        if verify_lock(app_dir, lock_token) != lock_identity:
            raise RepairRefused("release_lock_changed")
        try:
            response = client.execute_command("REPLICAOF", "NO", "ONE")
            if response not in (True, "OK", b"OK"):
                raise RepairRefused("command_not_acknowledged")
        except Exception as error:
            verified = False
            try:
                role, verified = _postcondition(client, metadata["run_id"])
            except Exception:
                role = "unknown"
            return {
                "status": "error", "role": role, "no_data_cleared": True,
                "reason": "command_failed_state_verified" if verified else "command_failed_state_unknown",
                "error_type": type(error).__name__,
            }, 1
        role, verified = _postcondition(client, metadata["run_id"])
        if not verified:
            raise RepairRefused("postcondition_failed")
        return {"status": "updated", "role": role, "no_data_cleared": True}, 0
    except Exception as error:
        return {
            "status": "error", "role": role, "no_data_cleared": True,
            "reason": str(error) if isinstance(error, RepairRefused) else "inspection_failed",
            "error_type": type(error).__name__,
        }, 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=Path("/var/www/everydayai"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--lock-token")
    args = parser.parse_args()
    result, code = repair(args.app_dir, apply=args.apply, lock_token=args.lock_token)
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
