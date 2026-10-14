#!/usr/bin/env python3
"""Restore only the identified Redis incident configuration; default is read-only.

Replication must already be stopped. Never restart, save, rewrite config, load a
snapshot, or touch keys. On any partial failure preserve evidence and the lock.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import socket
import stat
import subprocess
import tempfile
import uuid


DATA_DIR = Path("/var/lib/redis")
FIELDS = {"dir", "dbfilename", "replicaof", "replica-read-only"}
ALIASES = {"slaveof": "replicaof", "slave-read-only": "replica-read-only"}


class Refused(RuntimeError):
    pass


def verify_lock(app_dir, token):
    if not token or not re.fullmatch(r"[A-Za-z0-9-]+", token):
        raise Refused("release_lock_required")
    directory = Path(f"{app_dir}.release-lock")
    owner = directory / "owner"
    ds, fs = directory.lstat(), owner.lstat()
    if (not stat.S_ISDIR(ds.st_mode) or not stat.S_ISREG(fs.st_mode)
            or owner.read_text().rstrip("\n") != token):
        raise Refused("release_lock_required")
    return ds.st_dev, ds.st_ino, fs.st_dev, fs.st_ino


def create_client(env_file):
    from dotenv import dotenv_values
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    if not env_file.is_file():
        raise Refused("configuration_unavailable")
    values = dotenv_values(env_file, interpolate=False)
    host = values.get("REDIS_HOST") or "localhost"
    ssl_value = (values.get("REDIS_SSL") or "false").lower()
    if ssl_value not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        raise Refused("configuration_invalid")
    return Redis(host=host, port=int(values.get("REDIS_PORT") or 6379),
                 password=values.get("REDIS_PASSWORD") or None, db=int(values.get("REDIS_DB") or 0),
                 ssl=ssl_value in {"true", "1", "yes", "on"}, decode_responses=True,
                 socket_timeout=15, socket_connect_timeout=15, retry=Retry(NoBackoff(), 0),
                 retry_on_timeout=False, retry_on_error=[]), host


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise Refused("configuration_file_unsafe")
        return stream.read(), metadata


def parse_config(raw):
    lines = raw.decode("utf-8").splitlines(keepends=True)
    fields = {}
    for index, line in enumerate(lines):
        tokens = shlex.split(line, comments=True)
        if not tokens:
            continue
        key = ALIASES.get(tokens[0].lower(), tokens[0].lower())
        if key == "include":
            raise Refused("configuration_include_unsupported")
        if key in FIELDS:
            if key in fields:
                raise Refused("configuration_ambiguous")
            fields[key] = (index, tokens[1:])
    return lines, fields


def render_config(lines, fields):
    desired = {"dir": str(DATA_DIR), "dbfilename": "dump.rdb", "replica-read-only": "yes"}
    replacements = {}
    for key, (index, _) in fields.items():
        if key == "replicaof":
            replacements[index] = ""
            continue
        line = lines[index]
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        # These guarded values contain no spaces or '#'; keep their comment,
        # indentation, trailing whitespace and original line ending.
        suffix = re.search(r"[ \t]*(?:#[^\r\n]*)?$", body).group()
        indent = re.match(r"[ \t]*", body).group()
        replacements[index] = f"{indent}{key} {desired[key]}{suffix}{ending}"
    return "".join(replacements.get(index, line) for index, line in enumerate(lines)).encode("utf-8")


def verify_service(server):
    completed = subprocess.run(["systemctl", "show", "redis.service", "--property=User", "--property=MainPID"],
                               capture_output=True, text=True, check=True, timeout=5)
    properties = dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
    uid = pwd.getpwnam("redis").pw_uid
    pid = int(properties.get("MainPID") or 0)
    if (properties.get("User") != "redis" or pid <= 0 or server.get("process_id") != pid
            or Path(f"/proc/{pid}").stat().st_uid != uid):
        raise Refused("service_identity_unverified")
    return uid


def verify_data_directory(uid):
    metadata = DATA_DIR.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid or metadata.st_mode & 0o002:
        raise Refused("data_directory_unsafe")


def external_upstream(host, own_host):
    def addresses(value):
        try:
            return {str(ipaddress.ip_address(value))}
        except ValueError:
            return {row[4][0] for row in socket.getaddrinfo(value, None, type=socket.SOCK_STREAM)}
    upstream, own = addresses(host), addresses(own_host)
    return bool(upstream and own and not upstream & own
                and not any(ipaddress.ip_address(value).is_loopback for value in upstream))


def runtime_state(client):
    replication = client.info("replication")
    if replication.get("role") != "master" or replication.get("connected_slaves") != 0:
        raise Refused("master_without_replicas_required")
    return {field: client.config_get(field).get(field) for field in ("dir", "dbfilename", "replica-read-only")}


def verify_runtime_identity(client, run_id):
    replication = client.info("replication")
    if (replication.get("role") != "master" or replication.get("connected_slaves") != 0
            or client.info("server").get("run_id") != run_id):
        raise Refused("runtime_identity_changed")


def save_backups(app_dir, original):
    directory = app_dir / ".incident-evidence"
    directory.mkdir(mode=0o700, exist_ok=True)
    metadata = directory.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o077:
        raise Refused("evidence_directory_unsafe")
    identity = uuid.uuid4().hex
    backups = [(f"redis-conf-{identity}.bak", original)]
    dump = DATA_DIR / "dump.rdb"
    if dump.exists() or dump.is_symlink():
        backups.append((f"dump-rdb-{identity}.bak", read_regular(dump)[0]))
    for name, contents in backups:
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    fsync_directory(directory)


def fsync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_config(path, original, metadata, restored, assert_lock):
    fd, temporary = tempfile.mkstemp(prefix=".redis-restore-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            current = os.fstat(stream.fileno())
            if (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid):
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            stream.write(restored)
            stream.flush()
            os.fsync(stream.fileno())
        current, now = read_regular(path)
        if current != original or (now.st_dev, now.st_ino, now.st_uid, now.st_gid, now.st_mode) != (
                metadata.st_dev, metadata.st_ino, metadata.st_uid, metadata.st_gid, metadata.st_mode):
            raise Refused("configuration_changed")
        assert_lock()
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore(app_dir, *, apply=False, lock_token=None, client_factory=None):
    client = None
    try:
        if not app_dir.is_absolute() or app_dir == Path("/") or not app_dir.is_dir():
            raise Refused("application_directory_invalid")
        lock = verify_lock(app_dir, lock_token) if apply else None
        def assert_lock():
            if verify_lock(app_dir, lock_token) != lock:
                raise Refused("release_lock_changed")
        client, host = (client_factory or create_client)(app_dir / "backend/.env")
        runtime = runtime_state(client)
        server = client.info("server")
        if not server.get("run_id") or not server.get("config_file"):
            raise Refused("server_identity_unverified")
        path = Path(server["config_file"])
        if not path.is_absolute():
            raise Refused("configuration_file_unsafe")
        original, metadata = read_regular(path)
        lines, fields = parse_config(original)
        values = {key: value for key, (_, value) in fields.items()}
        desired = {"dir": str(DATA_DIR), "dbfilename": "dump.rdb", "replica-read-only": "yes"}
        uid = verify_service(server)
        verify_data_directory(uid)
        if runtime == desired and values == {key: [value] for key, value in desired.items()}:
            return {"status": "nochange", "role": "master", "no_data_cleared": True}, 0
        upstream = values.get("replicaof", [])
        if (runtime["dir"] != "/etc" or runtime["dbfilename"] != "crontab"
                or values.get("dir") != ["/tmp"] or values.get("dbfilename") != ["exp.so"]
                or values.get("replica-read-only") != ["no"] or len(upstream) != 2
                or upstream[1] != "7178" or not external_upstream(upstream[0], host)):
            raise Refused("incident_guard_mismatch")
        restored = render_config(lines, fields)
        if not apply:
            return {"status": "dry_run", "role": "master", "no_data_cleared": True}, 0
        assert_lock()
        save_backups(app_dir, original)
        replace_config(path, original, metadata, restored, assert_lock)
        for field, value in desired.items():
            assert_lock()
            # Do not make a replication destination writable if the process
            # restarted or an external controller reattached its upstream.
            verify_runtime_identity(client, server["run_id"])
            if client.execute_command("CONFIG", "SET", field, value) not in (True, "OK", b"OK"):
                raise Refused("runtime_update_not_acknowledged")
        if (runtime_state(client) != desired or client.info("server").get("run_id") != server["run_id"]
                or read_regular(path)[0] != restored):
            raise Refused("postcondition_failed")
        return {"status": "updated", "role": "master", "no_data_cleared": True}, 0
    except Exception as error:
        return {"status": "error", "no_data_cleared": True,
                "reason": str(error) if isinstance(error, Refused) else "restore_failed",
                "error_type": type(error).__name__}, 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=Path("/var/www/everydayai"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--lock-token")
    args = parser.parse_args()
    result, code = restore(args.app_dir, apply=args.apply, lock_token=args.lock_token)
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
