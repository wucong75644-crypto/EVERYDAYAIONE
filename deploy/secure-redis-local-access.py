#!/usr/bin/env python3
"""One-off migration of this server's Redis clients to loopback, then close bind.

Default is read-only. Applying restarts the four application services, briefly
interrupts the backend and disconnects WebSockets. A read-only database guard
rejects running work before each restart. Redis itself is never restarted; no
keys, snapshots, passwords or application data are changed. Partial failure
retains backups and the release lock, without reopening a wildcard listener.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import stat
import subprocess
import tempfile
import time
import urllib.request
import uuid


UNITS = ("everydayai-backend.service", "everydayai-sync.service", "everydayai-wecom.service", "everydayai-conversation-actor.service")


class Refused(RuntimeError):
    pass


def command(args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=timeout).stdout


def verify_lock(app_dir, token):
    if not token or not re.fullmatch(r"[A-Za-z0-9-]+", token):
        raise Refused("release_lock_required")
    directory, owner = Path(f"{app_dir}.release-lock"), Path(f"{app_dir}.release-lock/owner")
    ds, fs = directory.lstat(), owner.lstat()
    if (not stat.S_ISDIR(ds.st_mode) or not stat.S_ISREG(fs.st_mode)
            or owner.read_text().rstrip("\n") != token):
        raise Refused("release_lock_required")
    return ds.st_dev, ds.st_ino, fs.st_dev, fs.st_ino


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise Refused("configuration_file_unsafe")
        return stream.read(), metadata


def addresses(host):
    try:
        return {str(ipaddress.IPv4Address(host))}
    except ipaddress.AddressValueError:
        return {row[4][0] for row in socket.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)}


def load_environment(path, raw):
    from dotenv import dotenv_values
    values = dotenv_values(path, interpolate=False)
    if (int(values.get("REDIS_PORT") or 6379) != 6379
            or (values.get("REDIS_SSL") or "false").lower() not in {"false", "0", "no", "off"}):
        raise Refused("redis_endpoint_guard_mismatch")
    starts = re.findall(rb"(?m)^[ \t]*(?:export[ \t]+)?REDIS_HOST[ \t]*=", raw)
    pattern = rb'''(?m)^([ \t]*(?:export[ \t]+)?REDIS_HOST[ \t]*=[ \t]*)(["']?)([A-Za-z0-9.:-]+)\2([ \t]*(?:#[^\r\n]*)?)(\r?)$'''
    matches = list(re.finditer(pattern, raw))
    if len(starts) != 1 or len(matches) != 1:
        raise Refused("redis_host_ambiguous")
    match = matches[0]
    host = match[3].decode("ascii")
    if values.get("REDIS_HOST") != host:
        raise Refused("redis_host_ambiguous")
    replacement = match[1] + match[2] + b"127.0.0.1" + match[2] + match[4] + match[5]
    return values, host, raw[:match.start()] + replacement + raw[match.end():]


def connect(values, host):
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    return Redis(host=host, port=6379, password=values.get("REDIS_PASSWORD") or None,
                 db=int(values.get("REDIS_DB") or 0), decode_responses=True,
                 socket_timeout=15, socket_connect_timeout=15, retry=Retry(NoBackoff(), 0),
                 retry_on_timeout=False, retry_on_error=[])


def verify_instance(client, run_id=None, pid=None):
    replication, server = client.info("replication"), client.info("server")
    if (replication.get("role") != "master" or replication.get("connected_slaves") != 0
            or not server.get("run_id") or server.get("tcp_port") != 6379
            or run_id is not None and server["run_id"] != run_id
            or pid is not None and server.get("process_id") != pid):
        raise Refused("redis_instance_guard_mismatch")
    return server


def unit_properties(unit):
    properties = ("ActiveState", "MainPID", "EnvironmentFiles", "PrivateNetwork", "NetworkNamespacePath", "JoinsNamespaceOf")
    output = command(["systemctl", "show", unit, *[f"--property={key}" for key in properties]])
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def process_host(pid):
    entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    hosts = [entry.split(b"=", 1)[1].decode("ascii") for entry in entries if entry.startswith(b"REDIS_HOST=")]
    if len(hosts) != 1:
        raise Refused("service_environment_unverified")
    return hosts[0]


def verify_unit(unit, env_file, *, loopback=False, public_address=None):
    values = unit_properties(unit)
    if (values.get("ActiveState") != "active" or int(values.get("MainPID") or 0) <= 0
            or values.get("EnvironmentFiles") != f"{env_file} (ignore_errors=no)"
            or values.get("PrivateNetwork") != "no"
            or values.get("NetworkNamespacePath") or values.get("JoinsNamespaceOf")):
        raise Refused("service_configuration_guard_mismatch")
    host = process_host(int(values["MainPID"]))
    if loopback and host != "127.0.0.1":
        raise Refused("service_environment_unverified")
    if not loopback and host != "127.0.0.1" and addresses(host) != {public_address}:
        raise Refused("service_environment_unverified")
    return host


def verify_redis_unit(server):
    output = command(["systemctl", "show", "redis.service", "--property=ActiveState", "--property=MainPID"])
    values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if values.get("ActiveState") != "active" or int(values.get("MainPID") or 0) != server.get("process_id"):
        raise Refused("redis_service_identity_unverified")


def wait_restarted_unit(unit, env_file):
    # Type=simple can report restart complete before exec/environ is visible.
    # Recheck the full guard instead of treating a successful restart as ready.
    deadline = time.monotonic() + 15
    while True:
        try:
            verify_unit(unit, env_file, loopback=True)
            return
        except (Refused, FileNotFoundError, ProcessLookupError):
            if time.monotonic() >= deadline:
                raise
        time.sleep(0.1)


def verify_clients(client, public_address=None):
    for item in client.client_list():
        address = item.get("addr", "").rsplit(":", 1)[0].strip("[]")
        parsed = ipaddress.ip_address(address)
        if not parsed.is_loopback and str(parsed) != public_address:
            raise Refused("non_loopback_clients_present" if public_address is None else "foreign_clients_present")


def restored_bind(raw):
    lines, indices = raw.decode("utf-8").splitlines(keepends=True), []
    for index, line in enumerate(lines):
        tokens = shlex.split(line, comments=True)
        if tokens and tokens[0].lower() == "include":
            raise Refused("configuration_include_unsupported")
        if tokens and tokens[0].lower() == "bind":
            if tokens[1:] not in (["0.0.0.0"], ["127.0.0.1"]):
                raise Refused("bind_guard_mismatch")
            indices.append(index)
    if len(indices) != 1:
        raise Refused("bind_ambiguous")
    index = indices[0]
    line = lines[index]
    body = line.rstrip("\r\n")
    suffix = re.search(r"[ \t]*(?:#[^\r\n]*)?$", body).group()
    lines[index] = re.match(r"[ \t]*", body).group() + "bind 127.0.0.1" + suffix + line[len(body):]
    return "".join(lines).encode("utf-8")


def fsync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def backups(app_dir, env_raw, config_raw):
    directory = app_dir / ".incident-evidence"
    directory.mkdir(mode=0o700, exist_ok=True)
    metadata = directory.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o077:
        raise Refused("evidence_directory_unsafe")
    identity = uuid.uuid4().hex
    for name, raw in (("backend-env", env_raw), ("redis-conf", config_raw)):
        fd = os.open(directory / f"{name}-local-{identity}.bak", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    fsync_directory(directory)


def replace_file(path, original, metadata, replacement, assert_lock):
    fd, temporary = tempfile.mkstemp(prefix=".redis-local-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            current = os.fstat(stream.fileno())
            if (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid):
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        current, now = read_regular(path)
        if current != original or (now.st_dev, now.st_ino, now.st_mode, now.st_uid, now.st_gid) != (
                metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid, metadata.st_gid):
            raise Refused("configuration_changed")
        assert_lock()
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ensure_health():
    deadline = time.monotonic() + 30
    while True:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2) as response:
                body = response.read(65537)
                if response.status == 200 and len(body) <= 65536 and json.loads(body).get("status") == "ok":
                    return
        except Exception:
            pass
        if time.monotonic() >= deadline:
            raise Refused("backend_health_failed")
        time.sleep(0.25)


def ensure_idle(values):
    import psycopg
    with psycopg.connect(values["DATABASE_URL"], connect_timeout=5) as database:
        with database.transaction():
            database.execute("SET TRANSACTION READ ONLY")
            counts = database.execute(
                "SELECT (SELECT count(*) FROM tasks WHERE status = 'running'), "
                "(SELECT count(*) FROM scheduled_tasks WHERE status = 'running')"
            ).fetchone()
            if counts != (0, 0):
                raise Refused("running_work_present")


def verify_listeners(server, public_address):
    lines = command(["ss", "-H", "-ltnp", "sport = :6379"]).splitlines()
    if len(lines) != 1 or len(lines[0].split()) < 5 or lines[0].split()[3] != "127.0.0.1:6379" \
            or f"pid={server['process_id']}," not in lines[0]:
        raise Refused("listener_verification_failed")
    try:
        connection = socket.create_connection((public_address, 6379), timeout=3)
    except OSError:
        return
    connection.close()
    raise Refused("non_loopback_connection_still_open")


def secure(app_dir, server_host, *, apply=False, lock_token=None):
    local = public = None
    try:
        if not app_dir.is_absolute() or app_dir == Path("/") or not app_dir.is_dir():
            raise Refused("application_directory_invalid")
        lock = verify_lock(app_dir, lock_token) if apply else None
        def assert_lock():
            if verify_lock(app_dir, lock_token) != lock:
                raise Refused("release_lock_changed")
        resolved = addresses(server_host)
        if len(resolved) != 1 or not ipaddress.ip_address(next(iter(resolved))).is_global:
            raise Refused("server_address_unverified")
        public_address = next(iter(resolved))
        env_file = app_dir / "backend/.env"
        env_raw, env_metadata = read_regular(env_file)
        values, old_host, env_new = load_environment(env_file, env_raw)
        if old_host != "127.0.0.1" and addresses(old_host) != resolved:
            raise Refused("redis_route_not_this_server")
        local = connect(values, "127.0.0.1")
        server = verify_instance(local)
        if old_host != "127.0.0.1":
            public = connect(values, old_host)
            verify_instance(public, server["run_id"], server["process_id"])
            public.close()
            public = None
        verify_redis_unit(server)
        for unit in UNITS:
            verify_unit(unit, env_file, public_address=public_address)
        verify_clients(local, public_address)
        path = Path(server.get("config_file") or "")
        if not path.is_absolute():
            raise Refused("configuration_file_unsafe")
        config_raw, config_metadata = read_regular(path)
        config_new = restored_bind(config_raw)
        bind = local.config_get("bind").get("bind")
        if bind not in {"0.0.0.0", "127.0.0.1"}:
            raise Refused("bind_guard_mismatch")
        ensure_idle(values)
        ensure_health()
        if env_raw == env_new and config_raw == config_new and bind == "127.0.0.1":
            for unit in UNITS:
                verify_unit(unit, env_file, loopback=True)
            verify_clients(local)
            verify_listeners(server, public_address)
            return {"status": "nochange", "no_data_cleared": True}, 0
        if not apply:
            return {"status": "dry_run", "backend_restart_interrupts_websockets": True, "no_data_cleared": True}, 0
        assert_lock()
        backups(app_dir, env_raw, config_raw)
        if env_raw != env_new:
            replace_file(env_file, env_raw, env_metadata, env_new, assert_lock)
        for unit in UNITS:
            assert_lock()
            host = verify_unit(unit, env_file, public_address=public_address)
            if host == "127.0.0.1":
                verify_unit(unit, env_file, loopback=True)
                ensure_health()
                continue
            ensure_idle(values)
            command(["systemctl", "restart", unit], timeout=90)
            wait_restarted_unit(unit, env_file)
            ensure_health()
        verify_clients(local)
        verify_instance(local, server["run_id"], server["process_id"])
        if config_raw != config_new:
            replace_file(path, config_raw, config_metadata, config_new, assert_lock)
        assert_lock()
        verify_instance(local, server["run_id"], server["process_id"])
        if local.execute_command("CONFIG", "SET", "bind", "127.0.0.1") not in (True, "OK", b"OK"):
            raise Refused("bind_not_acknowledged")
        verify_instance(local, server["run_id"], server["process_id"])
        if local.config_get("bind").get("bind") != "127.0.0.1" or read_regular(path)[0] != config_new:
            raise Refused("bind_postcondition_failed")
        verify_clients(local)
        verify_listeners(server, public_address)
        ensure_health()
        return {"status": "updated", "backend_restart_interrupts_websockets": True, "no_data_cleared": True}, 0
    except Exception as error:
        return {"status": "error", "no_data_cleared": True,
                "reason": str(error) if isinstance(error, Refused) else "local_access_failed",
                "error_type": type(error).__name__}, 1
    finally:
        for client in (public, local):
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=Path("/var/www/everydayai"))
    parser.add_argument("--server-host", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--lock-token")
    args = parser.parse_args()
    result, code = secure(args.app_dir, args.server_host, apply=args.apply, lock_token=args.lock_token)
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
