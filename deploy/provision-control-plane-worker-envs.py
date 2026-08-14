#!/usr/bin/env python3
"""Provision the four flags-off control-plane environment files."""
from __future__ import annotations
import argparse
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
try:
    from deploy.control_plane_env_source import (
        PASSWORD_KEYS, ProvisioningError, read_required_values,
        render_envs, validate_kek,
    )
except ModuleNotFoundError:
    from control_plane_env_source import (  # type: ignore[no-redef]
        PASSWORD_KEYS, ProvisioningError, read_required_values,
        render_envs, validate_kek,
    )
RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
OWNER = "root"
GROUP = "everydayai-app"
RUNTIME_MODEL_SECRET_GROUP = "everydayai-runtime-model-secret"
DEFAULT_TRANSACTION_ROOT = Path("/var/backups/everydayai/control-plane-updates")
ENV_NAMES = (
    "agent-runtime-worker.env",
    "agent-runtime-model.env",
    "agent-projection-worker.env",
    "agent-authorization-worker.env",
)
LEGACY_ENV_NAMES = (
    "agent-model-gateway.env",
    "agent-model-gateway-kek.env",
)
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("prepare", "preflight", "publish", "verify",
                                               "rollback-preflight", "rollback"))
    parser.add_argument("--backend-dir", type=Path)
    parser.add_argument("--env-dir", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--transaction-root", type=Path, default=DEFAULT_TRANSACTION_ROOT)
    return parser.parse_args(argv)
def _resolve_owner(name: str | None = None) -> tuple[int, int]:
    group = RUNTIME_MODEL_SECRET_GROUP if name == "agent-runtime-model.env" else GROUP
    try:
        return pwd.getpwnam(OWNER).pw_uid, grp.getgrnam(group).gr_gid
    except KeyError as exc:
        raise ProvisioningError(f"缺少目标 owner/group: {OWNER}:{group}") from exc
def _target_identities() -> dict[str, tuple[int, int]]:
    return {name: _resolve_owner(name) for name in ENV_NAMES}


def _reject_legacy_envs(env_dir: Path) -> None:
    for name in LEGACY_ENV_NAMES:
        path = env_dir / name
        if path.exists() or path.is_symlink():
            raise ProvisioningError("legacy Model Gateway env 必须先完成受审查退役")
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
        published = entry.get("published")
        if not isinstance(published, dict) or set(published) != {
            "present", "sha256", "mode", "uid", "gid",
        } or published.get("present") is not True \
            or published.get("sha256") != entry.get("staged_sha256") \
            or published.get("mode") != 0o640:
            raise ProvisioningError("env transaction published state 无效")
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
def _published_state(entry: dict[str, object]) -> dict[str, object]:
    return entry["published"]
def _verify_published(env_dir: Path, journal: dict[str, object]) -> None:
    for entry in journal["files"]:
        if not _matches(env_dir / entry["name"], _published_state(entry)):
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
    env_dir: Path, journal: dict[str, object]
) -> list[tuple[dict[str, object], str]]:
    plan = []
    for entry in journal["files"]:
        target = env_dir / entry["name"]
        if _matches(target, entry["prior"]):
            plan.append((entry, "noop"))
        elif journal.get("status") == "restored":
            raise ProvisioningError("env rollback hash fence 不匹配")
        elif _matches(target, _published_state(entry)):
            plan.append((entry, "restore" if entry["prior"]["present"] else "delete"))
        else:
            raise ProvisioningError("env rollback hash fence 不匹配")
    return plan
def _rollback_envs(
    env_dir: Path, release_dir: Path, journal: dict[str, object],
    transaction_uid: int, transaction_gid: int,
) -> None:
    plan = _rollback_plan(env_dir, journal)
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
def _prepare(
    args: argparse.Namespace, identities: dict[str, tuple[int, int]],
    tx_uid: int, tx_gid: int,
) -> None:
    if args.backend_dir is None:
        raise ProvisioningError("prepare 缺少 backend-dir")
    required = set(PASSWORD_KEYS.values()) | {
        "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB", "REDIS_SSL", "SENTRY_DSN", "ENVIRONMENT",
    }
    backend = read_required_values(args.backend_dir / ".env", required, {0o600, 0o640})
    migrator = read_required_values(
        args.backend_dir / ".env.migrator", {"MIGRATION_DATABASE_URL"}, {0o600}
    )
    kek = validate_kek(read_required_values(
        args.backend_dir / ".env.kek",
        {"CONFIG_KEK_CURRENT_VERSION", "CONFIG_KEK_KEYRING_JSON"},
        {0o600}, exact=True,
    ))
    rendered = {
        name: content.encode() for name, content in render_envs(
            backend, migrator["MIGRATION_DATABASE_URL"], args.release_sha,
            kek,
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
            uid, gid = identities[name]
            entries.append({
                "name": name, "prior": prior, "staged_sha256": _sha256(rendered[name]),
                "published": {"present": True, "sha256": _sha256(rendered[name]),
                              "mode": 0o640, "uid": uid, "gid": gid},
            })
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
    _reject_legacy_envs(args.env_dir)
    if os.geteuid() != 0:
        raise ProvisioningError("control-plane env transaction 必须以 root 执行")
    identities = _target_identities()
    tx_uid, tx_gid = _resolve_transaction_owner()
    if args.operation == "prepare":
        _prepare(args, identities, tx_uid, tx_gid)
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
                published = _published_state(entry)
                _atomic_replace_from(
                    release_dir / "env-staged" / entry["name"],
                    args.env_dir / entry["name"],
                    int(published["uid"]), int(published["gid"]), 0o640,
                )
            _verify_published(args.env_dir, journal)
            journal["status"] = "published"
            _store_journal(release_dir, journal, tx_uid, tx_gid)
        except Exception as exc:
            try:
                _rollback_envs(args.env_dir, release_dir, journal, tx_uid, tx_gid)
            except Exception as rollback_exc:
                raise ProvisioningError("env publish 失败且自动恢复失败") from rollback_exc
            raise ProvisioningError("env publish 失败，已恢复全部 env") from exc
    elif args.operation == "verify":
        if journal.get("status") != "published":
            raise ProvisioningError("env transaction 未处于 published")
        _verify_published(args.env_dir, journal)
    elif args.operation == "rollback-preflight":
        _rollback_plan(args.env_dir, journal)
    else:
        _rollback_envs(args.env_dir, release_dir, journal, tx_uid, tx_gid)
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
