#!/usr/bin/env python3
"""Provision the three flags-off control-plane worker environment files."""
from __future__ import annotations
import argparse
import ast
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import tempfile
from urllib.parse import quote, unquote, urlsplit, urlunsplit
RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
OWNER = "root"
GROUP = "everydayai-app"
DEFAULT_TRANSACTION_ROOT = Path("/var/backups/everydayai/control-plane-updates")
ENV_NAMES = (
    "agent-runtime-worker.env",
    "agent-projection-worker.env",
    "agent-authorization-worker.env",
)
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
    parser.add_argument("operation", choices=("prepare", "preflight", "publish", "verify",
                                               "rollback-preflight", "rollback"))
    parser.add_argument("--backend-dir", type=Path)
    parser.add_argument("--env-dir", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--transaction-root", type=Path, default=DEFAULT_TRANSACTION_ROOT)
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
def _resolve_transaction_owner() -> tuple[int, int]:
    return 0, 0
def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
def _write_secure_file(path: Path, content: bytes, uid: int, gid: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(path, uid, gid)
    except Exception:
        path.unlink(missing_ok=True)
        raise
def _snapshot(path: Path) -> tuple[dict[str, int | str | bool | None], bytes | None]:
    if not path.exists() and not path.is_symlink():
        return {"present": False, "sha256": None, "mode": None, "uid": None, "gid": None}, None
    file_stat = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode):
        raise ProvisioningError(f"control-plane env 目标必须是普通文件: {path}")
    content = path.read_bytes()
    return {
        "present": True,
        "sha256": _sha256(content),
        "mode": stat.S_IMODE(file_stat.st_mode),
        "uid": file_stat.st_uid,
        "gid": file_stat.st_gid,
    }, content
def _matches(path: Path, expected: dict[str, object]) -> bool:
    try:
        actual, _ = _snapshot(path)
    except (OSError, ProvisioningError):
        return False
    return actual == expected
def _secure_dir(path: Path, uid: int, gid: int, *, create: bool = False) -> None:
    try:
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
            os.chown(path, uid, gid)
        file_stat = path.lstat()
    except OSError as exc:
        raise ProvisioningError(f"事务目录不可用: {path}") from exc
    if path.is_symlink() or not stat.S_ISDIR(file_stat.st_mode):
        raise ProvisioningError(f"事务路径必须是安全目录: {path}")
    if stat.S_IMODE(file_stat.st_mode) != 0o700 \
        or (file_stat.st_uid, file_stat.st_gid) != (uid, gid):
        raise ProvisioningError(f"事务目录必须为 owner-only: {path}")
def _secure_artifact(path: Path, uid: int, gid: int) -> bytes:
    file_stat = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode):
        raise ProvisioningError("事务 artifact 必须是普通文件")
    if stat.S_IMODE(file_stat.st_mode) != 0o600 \
        or (file_stat.st_uid, file_stat.st_gid) != (uid, gid):
        raise ProvisioningError("事务 artifact 权限或 owner 无效")
    return path.read_bytes()
def _validate_parent(path: Path, uid: int) -> None:
    try:
        info = path.lstat()
    except OSError as exc: raise ProvisioningError("事务父目录不可用") from exc
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode) \
        or info.st_uid != uid or stat.S_IMODE(info.st_mode) & 0o022:
        raise ProvisioningError("事务父目录 owner 或权限不安全")
def _ensure_transaction_root(root: Path, uid: int, gid: int) -> None:
    parent = root.parent
    if parent.is_symlink():
        raise ProvisioningError("事务根目录的父目录不得是 symlink")
    if not parent.exists():
        grandparent = parent.parent
        _validate_parent(grandparent, uid)
        _secure_dir(parent, uid, gid, create=True)
    elif not parent.is_dir():
        raise ProvisioningError("事务根目录的父目录无效")
    _validate_parent(parent, uid)
    if root.exists() or root.is_symlink():
        _secure_dir(root, uid, gid)
    else:
        _secure_dir(root, uid, gid, create=True)
def _store_journal(release_dir: Path, journal: dict[str, object], uid: int, gid: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env-journal.", dir=release_dir)
    temporary = Path(temporary_name)
    try:
        payload = json.dumps(journal, sort_keys=True, separators=(",", ":")).encode()
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, uid, gid)
        os.replace(temporary, release_dir / "env-journal.json")
    finally:
        temporary.unlink(missing_ok=True)
def _load_transaction(
    root: Path, release_sha: str, uid: int, gid: int
) -> tuple[Path, dict[str, object]]:
    release_dir = root / release_sha
    _validate_parent(root.parent, uid)
    _secure_dir(root, uid, gid)
    _secure_dir(release_dir, uid, gid)
    payload = _secure_artifact(release_dir / "env-journal.json", uid, gid)
    try:
        journal = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProvisioningError("env transaction journal 无效") from exc
    if not isinstance(journal, dict):
        raise ProvisioningError("env transaction journal 无效")
    if journal.get("version") != 1 or journal.get("release_sha") != release_sha:
        raise ProvisioningError("env transaction release fence 不匹配")
    entries = journal.get("files")
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise ProvisioningError("env transaction 文件集合无效")
    if [item.get("name") for item in entries] != list(ENV_NAMES):
        raise ProvisioningError("env transaction 文件集合无效")
    for directory_name in ("env-backups", "env-staged"):
        _secure_dir(release_dir / directory_name, uid, gid)
    for entry in entries:
        name = entry["name"]
        staged = _secure_artifact(release_dir / "env-staged" / name, uid, gid)
        if _sha256(staged) != entry.get("staged_sha256"):
            raise ProvisioningError("env staged hash fence 不匹配")
        backup = release_dir / "env-backups" / name
        prior = entry.get("prior")
        if not isinstance(prior, dict):
            raise ProvisioningError("env transaction prior state 无效")
        if prior.get("present"):
            content = _secure_artifact(backup, uid, gid)
            if _sha256(content) != prior.get("sha256"):
                raise ProvisioningError("env backup hash fence 不匹配")
        elif backup.exists() or backup.is_symlink():
            raise ProvisioningError("不存在的旧 env 不得包含 backup")
    return release_dir, journal
def _prior_preflight(env_dir: Path, journal: dict[str, object]) -> None:
    for entry in journal["files"]:
        if not _matches(env_dir / entry["name"], entry["prior"]):
            raise ProvisioningError("env 旧状态 preflight 不匹配")
def _published_state(entry: dict[str, object], uid: int, gid: int) -> dict[str, object]:
    return {"present": True, "sha256": entry["staged_sha256"], "mode": 0o640, "uid": uid, "gid": gid}
def _verify_published(
    env_dir: Path, journal: dict[str, object], uid: int, gid: int
) -> None:
    for entry in journal["files"]:
        if not _matches(env_dir / entry["name"], _published_state(entry, uid, gid)):
            raise ProvisioningError("env publish 后验失败")
def _atomic_replace_from(source: Path, target: Path, uid: int, gid: int, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
def _rollback_plan(
    env_dir: Path, journal: dict[str, object], uid: int, gid: int
) -> list[tuple[dict[str, object], str]]:
    plan = []
    for entry in journal["files"]:
        target = env_dir / entry["name"]
        if _matches(target, entry["prior"]):
            plan.append((entry, "noop"))
        elif journal.get("status") == "restored":
            raise ProvisioningError("env rollback hash fence 不匹配")
        elif _matches(target, _published_state(entry, uid, gid)):
            plan.append((entry, "restore" if entry["prior"]["present"] else "delete"))
        else:
            raise ProvisioningError("env rollback hash fence 不匹配")
    return plan
def _rollback_envs(
    env_dir: Path, release_dir: Path, journal: dict[str, object], uid: int, gid: int,
    transaction_uid: int, transaction_gid: int,
) -> None:
    plan = _rollback_plan(env_dir, journal, uid, gid)
    for entry, action in plan:
        target = env_dir / entry["name"]
        if action == "restore":
            prior = entry["prior"]
            _atomic_replace_from(
                release_dir / "env-backups" / entry["name"], target,
                prior["uid"], prior["gid"], prior["mode"],
            )
        elif action == "delete":
            target.unlink()
    _prior_preflight(env_dir, journal)
    journal["status"] = "restored"
    _store_journal(release_dir, journal, transaction_uid, transaction_gid)
def _prepare(args: argparse.Namespace, uid: int, gid: int, tx_uid: int, tx_gid: int) -> None:
    if args.backend_dir is None:
        raise ProvisioningError("prepare 缺少 backend-dir")
    required = set(PASSWORD_KEYS.values()) | {
        "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB", "REDIS_SSL", "SENTRY_DSN", "ENVIRONMENT",
    }
    backend = _read_required_values(args.backend_dir / ".env", required, {0o600, 0o640})
    migrator = _read_required_values(
        args.backend_dir / ".env.migrator", {"MIGRATION_DATABASE_URL"}, {0o600}
    )
    rendered = {
        name: content.encode() for name, content in _render_envs(
            backend, migrator["MIGRATION_DATABASE_URL"], args.release_sha
        ).items()
    }
    _ensure_transaction_root(args.transaction_root, tx_uid, tx_gid)
    release_dir = args.transaction_root / args.release_sha
    if release_dir.exists() or release_dir.is_symlink():
        existing_dir, journal = _load_transaction(
            args.transaction_root, args.release_sha, tx_uid, tx_gid
        )
        if journal.get("status") not in {"prepared", "restored"}:
            raise ProvisioningError("env transaction 已发布，拒绝重复 prepare")
        _prior_preflight(args.env_dir, journal)
        for entry in journal["files"]:
            if _secure_artifact(existing_dir / "env-staged" / entry["name"], tx_uid, tx_gid) != rendered[entry["name"]]:
                raise ProvisioningError("同 release staged env 已变化")
        journal["status"] = "prepared"
        _store_journal(existing_dir, journal, tx_uid, tx_gid)
        return
    temporary = Path(tempfile.mkdtemp(prefix=f".{args.release_sha}.prepare.", dir=args.transaction_root))
    try:
        os.chmod(temporary, 0o700)
        os.chown(temporary, tx_uid, tx_gid)
        backup_dir = temporary / "env-backups"
        staged_dir = temporary / "env-staged"
        _secure_dir(backup_dir, tx_uid, tx_gid, create=True)
        _secure_dir(staged_dir, tx_uid, tx_gid, create=True)
        entries = []
        for name in ENV_NAMES:
            prior, content = _snapshot(args.env_dir / name)
            if content is not None:
                _write_secure_file(backup_dir / name, content, tx_uid, tx_gid)
            _write_secure_file(staged_dir / name, rendered[name], tx_uid, tx_gid)
            entries.append({"name": name, "prior": prior, "staged_sha256": _sha256(rendered[name])})
        journal = {"version": 1, "release_sha": args.release_sha, "status": "prepared", "files": entries}
        _store_journal(temporary, journal, tx_uid, tx_gid)
        os.replace(temporary, release_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise ProvisioningError("env transaction prepare 失败，目标未修改") from None
def _dispatch(args: argparse.Namespace) -> None:
    if not RELEASE_SHA_RE.fullmatch(args.release_sha):
        raise ProvisioningError("release revision 必须是 40 位小写 SHA")
    if not args.env_dir.is_dir() or args.env_dir.is_symlink():
        raise ProvisioningError(f"目标环境目录必须已存在: {args.env_dir}")
    if os.geteuid() != 0:
        raise ProvisioningError("control-plane env transaction 必须以 root 执行")
    uid, gid = _resolve_owner()
    tx_uid, tx_gid = _resolve_transaction_owner()
    if args.operation == "prepare":
        _prepare(args, uid, gid, tx_uid, tx_gid)
        return
    release_dir, journal = _load_transaction(
        args.transaction_root, args.release_sha, tx_uid, tx_gid
    )
    if args.operation == "preflight":
        if journal.get("status") != "prepared":
            raise ProvisioningError("env transaction 未处于 prepared")
        _prior_preflight(args.env_dir, journal)
    elif args.operation == "publish":
        if journal.get("status") != "prepared":
            raise ProvisioningError("env transaction 未处于 prepared")
        try:
            _prior_preflight(args.env_dir, journal)
            for entry in journal["files"]:
                _atomic_replace_from(
                    release_dir / "env-staged" / entry["name"],
                    args.env_dir / entry["name"], uid, gid, 0o640,
                )
            _verify_published(args.env_dir, journal, uid, gid)
            journal["status"] = "published"
            _store_journal(release_dir, journal, tx_uid, tx_gid)
        except Exception as exc:
            try:
                _rollback_envs(args.env_dir, release_dir, journal, uid, gid, tx_uid, tx_gid)
            except Exception as rollback_exc:
                raise ProvisioningError("env publish 失败且自动恢复失败") from rollback_exc
            raise ProvisioningError("env publish 失败，已恢复全部 env") from exc
    elif args.operation == "verify":
        if journal.get("status") != "published":
            raise ProvisioningError("env transaction 未处于 published")
        _verify_published(args.env_dir, journal, uid, gid)
    elif args.operation == "rollback-preflight":
        _rollback_plan(args.env_dir, journal, uid, gid)
    else:
        _rollback_envs(args.env_dir, release_dir, journal, uid, gid, tx_uid, tx_gid)
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _dispatch(args)
    print(f"control-plane worker env transaction {args.operation} completed")
    return 0
if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvisioningError as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        raise SystemExit(1) from None
