#!/usr/bin/env python3
"""Provision the three flags-off control-plane worker environment files."""

from __future__ import annotations

import argparse
import ast
import grp
import os
from pathlib import Path
import pwd
import re
import stat
import tempfile
from urllib.parse import quote, unquote, urlsplit, urlunsplit


RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
OWNER = "root"
GROUP = "everydayai-app"
PASSWORD_KEYS = {
    "agent-runtime-worker.env": "EVERYDAYAI_AGENT_RUNTIME_WORKER_PASSWORD",
    "agent-projection-worker.env": "EVERYDAYAI_PROJECTION_WORKER_PASSWORD",
    "agent-authorization-worker.env": "EVERYDAYAI_AUTHORIZATION_WORKER_PASSWORD",
}
ROLES = {
    "agent-runtime-worker.env": "everydayai_agent_runtime_worker",
    "agent-projection-worker.env": "everydayai_projection_worker",
    "agent-authorization-worker.env": "everydayai_authorization_worker",
}


class ProvisioningError(RuntimeError):
    """A redacted provisioning contract failure."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-dir", type=Path, required=True)
    parser.add_argument("--env-dir", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args(argv)


def _parse_quoted_value(raw: str, quote_char: str) -> str:
    escaped = False
    for index in range(1, len(raw)):
        character = raw[index]
        if quote_char == '"' and character == "\\" and not escaped:
            escaped = True
            continue
        if character == quote_char and not escaped:
            suffix = raw[index + 1 :].strip()
            if suffix and not suffix.startswith("#"):
                raise ProvisioningError("环境文件包含无效的引号后内容")
            token = raw[: index + 1]
            if quote_char == "'":
                return token[1:-1]
            value = ast.literal_eval(token)
            if not isinstance(value, str):
                raise ProvisioningError("环境文件包含非字符串配置值")
            return value
        escaped = False
    raise ProvisioningError("环境文件包含未闭合引号")


def _parse_env_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in {"'", '"'}:
        return _parse_quoted_value(raw, raw[0])
    comment = re.search(r"[ \t]+#", raw)
    if comment:
        raw = raw[: comment.start()]
    return raw.rstrip()


def _read_required_values(
    path: Path, required: set[str], allowed_modes: set[int]
) -> dict[str, str]:
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise ProvisioningError(f"缺少安全配置文件: {path}") from exc
    if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink():
        raise ProvisioningError(f"安全配置路径必须是普通文件: {path}")
    if stat.S_IMODE(file_stat.st_mode) not in allowed_modes:
        allowed = "/".join(f"{mode:04o}" for mode in sorted(allowed_modes))
        raise ProvisioningError(f"安全配置文件权限必须为 {allowed}: {path}")

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProvisioningError(f"无法读取安全配置文件: {path}") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise ProvisioningError(f"安全配置文件包含无效配置行: {path}")
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.fullmatch(key):
            raise ProvisioningError(f"安全配置文件包含无效配置键: {path}")
        if key not in required:
            continue
        if key in values:
            raise ProvisioningError(f"安全配置文件包含重复必需键: {key}")
        value = _parse_env_value(raw_value)
        if "\n" in value or "\r" in value:
            raise ProvisioningError(f"安全配置值不能包含换行符: {key}")
        values[key] = value
    missing = sorted(required - values.keys())
    if missing:
        raise ProvisioningError(f"安全配置文件缺少必需键: {', '.join(missing)}")
    return values


def _worker_dsn(migrator_dsn: str, role: str, password: str) -> str:
    try:
        parts = urlsplit(migrator_dsn)
        port = parts.port
    except ValueError as exc:
        raise ProvisioningError("MIGRATION_DATABASE_URL 无效") from exc
    if parts.scheme not in {"postgres", "postgresql"}:
        raise ProvisioningError("MIGRATION_DATABASE_URL 必须使用 PostgreSQL")
    if unquote(parts.username or "") != "everydayai_migrator":
        raise ProvisioningError("MIGRATION_DATABASE_URL 必须使用 migrator 角色")
    if not parts.password or not parts.hostname or not parts.path.strip("/"):
        raise ProvisioningError("MIGRATION_DATABASE_URL 缺少凭证、主机或数据库名")
    if parts.fragment:
        raise ProvisioningError("MIGRATION_DATABASE_URL 不得包含 fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ProvisioningError("MIGRATION_DATABASE_URL 端口无效")
    _, separator, host_port = parts.netloc.rpartition("@")
    if not separator or not host_port:
        raise ProvisioningError("MIGRATION_DATABASE_URL 缺少 userinfo")
    userinfo = f"{quote(role, safe='')}:{quote(password, safe='')}"
    return urlunsplit((parts.scheme, f"{userinfo}@{host_port}", parts.path, parts.query, ""))


def _render_envs(
    backend_values: dict[str, str], migrator_dsn: str, release_sha: str
) -> dict[str, str]:
    passwords = {name: backend_values[key] for name, key in PASSWORD_KEYS.items()}
    for key, value in passwords.items():
        if len(value) < 24 or "\n" in value or "\r" in value:
            raise ProvisioningError(f"{PASSWORD_KEYS[key]} 不符合密码合同")
    urls = {
        name: _worker_dsn(migrator_dsn, ROLES[name], password)
        for name, password in passwords.items()
    }
    shared = {key: backend_values[key] for key in ("SENTRY_DSN", "ENVIRONMENT")}
    projection = {
        key: backend_values[key]
        for key in ("REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB", "REDIS_SSL")
    }
    required_nonempty = {
        "REDIS_HOST", "REDIS_PORT", "REDIS_DB", "REDIS_SSL", "ENVIRONMENT"
    }
    if any(not backend_values[key] for key in required_nonempty):
        raise ProvisioningError("现有 Redis/Environment 配置不能为空")

    rendered = {
        "agent-runtime-worker.env": {
            "WORKER_DATABASE_URL": urls["agent-runtime-worker.env"],
            "AGENT_RUNTIME_PROCESS_ROLE": "agent_runtime",
            "AGENT_RUNTIME_WORKER_ID": "agent-runtime-01",
            "AGENT_RUNTIME_RELEASE_REVISION": release_sha,
            "AGENT_RUNTIME_HEALTH_SOCKET": "/run/everydayai-agent-runtime/health.sock",
            "AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED": "false",
            "SANDBOX_JOB_ROOT": "/var/lib/everydayai/sandbox-jobs",
            "SANDBOX_RUNTIME_REVISION": "unprovisioned",
        },
        "agent-projection-worker.env": {
            "WORKER_DATABASE_URL": urls["agent-projection-worker.env"],
            **projection,
            "AGENT_RUNTIME_PROCESS_ROLE": "projection",
            "AGENT_RUNTIME_WORKER_ID": "agent-projection-01",
            "AGENT_RUNTIME_RELEASE_REVISION": release_sha,
            "AGENT_RUNTIME_HEALTH_SOCKET": "/run/everydayai-agent-projection/health.sock",
            "AGENT_RUNTIME_POLL_INTERVAL_SECONDS": "1",
            "AGENT_RUNTIME_HEARTBEAT_SECONDS": "10",
            **shared,
        },
        "agent-authorization-worker.env": {
            "WORKER_DATABASE_URL": urls["agent-authorization-worker.env"],
            "AGENT_RUNTIME_PROCESS_ROLE": "authorization",
            "AGENT_RUNTIME_WORKER_ID": "agent-authorization-01",
            "AGENT_RUNTIME_RELEASE_REVISION": release_sha,
            "AGENT_RUNTIME_HEALTH_SOCKET": "/run/everydayai-agent-authorization/health.sock",
            "AGENT_RUNTIME_POLL_INTERVAL_SECONDS": "1",
            "AGENT_RUNTIME_HEARTBEAT_SECONDS": "10",
            **shared,
        },
    }
    return {
        name: "".join(f"{key}={value}\n" for key, value in values.items())
        for name, values in rendered.items()
    }


def _resolve_owner() -> tuple[int, int]:
    try:
        return pwd.getpwnam(OWNER).pw_uid, grp.getgrnam(GROUP).gr_gid
    except KeyError as exc:
        raise ProvisioningError(f"缺少目标 owner/group: {OWNER}:{GROUP}") from exc


def _atomic_write(path: Path, content: str, uid: int, gid: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_targets(env_dir: Path, names: set[str], uid: int, gid: int) -> None:
    for name in names:
        target = env_dir / name
        target_stat = target.stat()
        if target.is_symlink() or not stat.S_ISREG(target_stat.st_mode):
            raise ProvisioningError(f"生成目标不是普通文件: {target}")
        if stat.S_IMODE(target_stat.st_mode) != 0o640:
            raise ProvisioningError(f"生成目标权限不是 0640: {target}")
        if (target_stat.st_uid, target_stat.st_gid) != (uid, gid):
            raise ProvisioningError(f"生成目标 owner/group 不正确: {target}")


def _validate_existing_targets(env_dir: Path, names: set[str]) -> None:
    for name in names:
        target = env_dir / name
        if not target.exists() and not target.is_symlink():
            continue
        if target.is_symlink() or not target.is_file():
            raise ProvisioningError(f"已有生成目标必须是普通文件: {target}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not RELEASE_SHA_RE.fullmatch(args.release_sha):
        raise ProvisioningError("release revision 必须是 40 位小写 SHA")
    required_backend = set(PASSWORD_KEYS.values()) | {
        "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB", "REDIS_SSL",
        "SENTRY_DSN", "ENVIRONMENT",
    }
    backend_values = _read_required_values(
        args.backend_dir / ".env", required_backend, {0o600, 0o640}
    )
    migrator_values = _read_required_values(
        args.backend_dir / ".env.migrator", {"MIGRATION_DATABASE_URL"}, {0o600}
    )
    rendered = _render_envs(
        backend_values, migrator_values["MIGRATION_DATABASE_URL"], args.release_sha
    )
    uid, gid = _resolve_owner()
    if not args.env_dir.is_dir() or args.env_dir.is_symlink():
        raise ProvisioningError(f"目标环境目录必须已存在: {args.env_dir}")
    _validate_existing_targets(args.env_dir, set(rendered))
    if args.check_only:
        print("control-plane worker env provisioning preflight passed")
        return 0
    if os.geteuid() != 0:
        raise ProvisioningError("写入 control-plane worker env 必须以 root 执行")
    for name, content in rendered.items():
        _atomic_write(args.env_dir / name, content, uid, gid)
    _verify_targets(args.env_dir, set(rendered), uid, gid)
    print("control-plane worker env provisioning completed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvisioningError as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        raise SystemExit(1) from None
