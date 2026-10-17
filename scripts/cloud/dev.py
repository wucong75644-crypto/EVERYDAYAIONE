#!/usr/bin/env python3
"""Prepare and run task-local development; no deployment or schema mutations."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
STATE = ROOT / ".cloud-dev"


def init() -> None:
    from dotenv import dotenv_values
    path = BACKEND / ".env"
    if path.exists():
        print("backend/.env already exists; preserved unchanged")
        return
    STATE.mkdir(mode=0o700, exist_ok=True)
    text = (ROOT / "scripts/cloud/development.env.example").read_text()
    text = text.replace("GENERATED_BY_INIT", secrets.token_urlsafe(48))
    text += f'FILE_WORKSPACE_ROOT="{STATE / "workspace"}"\n'
    # Never copy the root .env or deployment credentials.
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(text)
    assert dotenv_values(path)["APP_ENV"] == "development"
    print("Created backend/.env (0600); configure an isolated development database and Redis")


def environment() -> dict[str, str]:
    from dotenv import dotenv_values
    if not (BACKEND / ".env").exists():
        raise RuntimeError("Run init first")
    env = dict(os.environ)
    # Explicit process variables (e.g. Cloud vault values) take precedence.
    for key, value in dotenv_values(BACKEND / ".env").items():
        if value is not None:
            env.setdefault(key, value)
    return env


def check_backend(env: dict[str, str]) -> None:
    code = '''
from core.config import Settings
s = Settings()
assert s.app_env == "development" and s.environment == "development", "Development environment required"
assert not s.wecom_bot_enabled, "Disable WeCom bot in cloud development"
assert not s.agent_reach_enabled and not s.social_ops_scheduled_publish_enabled, "Disable external automation"
assert not s.conversation_actor_worker_enabled, "Do not enable independent workers"
assert not s.sentry_dsn, "Do not send development events to production monitoring"
assert s.redis_db != 0, "Use a dedicated development Redis DB"
assert "change-me" not in s.database_url, "Configure a development DATABASE_URL"
from pathlib import Path
assert Path(s.file_workspace_root).resolve().is_relative_to(Path.cwd().parent / ".cloud-dev"), "Use task-local file workspace"
import psycopg
from psycopg.conninfo import conninfo_to_dict
assert conninfo_to_dict(s.database_url).get('host') in ('127.0.0.1','localhost'), "Cloud native development requires loopback PostgreSQL"
assert s.redis_host in ('127.0.0.1','localhost'), "Cloud native development requires loopback Redis"
assert not s.sandbox_enabled and not s.kb_enabled, "Disable sandbox and external knowledge loading"
import sys
sys.path.insert(0,str(Path.cwd().parent / 'scripts/cloud/bootstrap'))
from apply import verify
with psycopg.connect(s.database_url, connect_timeout=5) as conn:
    verify(conn)
import redis
assert redis.Redis.from_url(s.redis_url, socket_connect_timeout=5).ping()
print("Development configuration, PostgreSQL schema and Redis checks passed")
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True)
    if result.returncode:
        # Connection errors can contain credentials; keep output private.
        STATE.mkdir(mode=0o700, exist_ok=True)
        log = STATE / "check.log"
        with log.open("w") as stream:
            os.chmod(log, 0o600)
            stream.write(result.stdout + result.stderr)
        raise RuntimeError("Backend preflight failed; inspect .cloud-dev/check.log locally (may contain secrets)")
    print(result.stdout.strip())


def wait_http(url: str, child: subprocess.Popen, *, api: bool = False) -> None:
    for _ in range(90):
        if child.poll() is not None:
            raise RuntimeError("Development service exited before readiness; inspect task-local logs")
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200 and (not api or json.load(response).get("status") == "ok"):
                    return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("Development service did not become ready within 90 attempts")


def start(frontend_only: bool) -> None:
    env = environment()
    if not frontend_only:
        check_backend(env)
    ports = [3000] if frontend_only else [8000, 3000]
    for port in ports:
        with socket.socket() as sock:
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                raise RuntimeError(f"Port {port} already in use; stop the owning task before starting") from None
    STATE.mkdir(mode=0o700, exist_ok=True)
    # Foreground supervisor: task tools can keep this command in a terminal session.
    children = []
    streams = []
    try:
        commands = []
        if not frontend_only:
            commands.append(("backend", [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"], BACKEND, "http://127.0.0.1:8000/api/health", True))
        commands.append(("frontend", ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--strictPort"], ROOT / "frontend", "http://127.0.0.1:3000", False))
        for name, command, cwd, url, api in commands:
            log_path = STATE / f"{name}.log"
            offset = log_path.stat().st_size if log_path.exists() else 0
            log = log_path.open("a")
            os.chmod(STATE / f"{name}.log", 0o600)
            streams.append(log)
            child = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            children.append(child)
            wait_http(url, child, api=api)
            if api:
                wait_http(url+'/db',child,api=True)
            print(f"{name} ready: {url}", flush=True)
            if api:
                receipt = STATE / 'readiness.json'
                receipt.write_text(json.dumps({'supervisor_pid':os.getpid(),
                    'backend_pid':child.pid,'backend_log_offset':offset}))
                os.chmod(receipt,0o600)
        print("Services ready. Ctrl-C stops only this command's process groups.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(1)
        raise RuntimeError("A development service exited; inspect .cloud-dev logs")
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        for stream in streams:
            stream.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "check", "start"))
    parser.add_argument("--frontend-only", action="store_true")
    args = parser.parse_args()
    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    if args.command == "init":
        init()
    elif args.command == "check":
        check_backend(environment())
    else:
        start(args.frontend_only)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (RuntimeError, OSError) as exc:
        print(f"Development error: {exc}", file=sys.stderr)
        sys.exit(1)
