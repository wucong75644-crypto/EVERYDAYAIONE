"""Failure matrix for the single-Runtime four-env transaction."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROVISIONER = ROOT / "deploy/provision-control-plane-worker-envs.py"
RELEASE_SHA = "d" * 40
ENV_NAMES = (
    "agent-runtime-worker.env",
    "agent-runtime-model.env",
    "agent-projection-worker.env",
    "agent-authorization-worker.env",
)
SECRETS = (
    "runtime!@:/?#[]%+ secret-0123456789",
    "projection!@:/?#[]%+ secret-0123456789",
    "authorization!@:/?#[]%+ secret-0123456789",
)


def _load_provisioner():
    spec = importlib.util.spec_from_file_location("env_transaction", PROVISIONER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sources(path: Path) -> None:
    path.mkdir()
    keys = (
        "EVERYDAYAI_AGENT_RUNTIME_WORKER_PASSWORD",
        "EVERYDAYAI_PROJECTION_WORKER_PASSWORD",
        "EVERYDAYAI_AUTHORIZATION_WORKER_PASSWORD",
    )
    lines = [f'{key}="{secret}"' for key, secret in zip(keys, SECRETS, strict=True)]
    lines += [
        "REDIS_HOST=redis.internal", "REDIS_PORT=6380",
        'REDIS_PASSWORD="redis-secret"', "REDIS_DB=4", "REDIS_SSL=true",
        "SENTRY_DSN=https://public@example.invalid/1", "ENVIRONMENT=production",
    ]
    (path / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (path / ".env").chmod(0o640)
    (path / ".env.migrator").write_text(
        'MIGRATION_DATABASE_URL="postgresql://everydayai_migrator:migrator-secret'
        '@db.internal:5433/everydayai?sslmode=require&connect_timeout=8"\n',
        encoding="utf-8",
    )
    (path / ".env.migrator").chmod(0o600)
    (path / ".env.kek").write_text(
        "CONFIG_KEK_CURRENT_VERSION=v1\n"
        "CONFIG_KEK_KEYRING_JSON='"
        '{"v1":"BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ="}'
        "'\n",
        encoding="utf-8",
    )
    (path / ".env.kek").chmod(0o600)


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_provisioner()
    backend = tmp_path / "backend"
    env_dir = tmp_path / "etc"
    transaction_root = tmp_path / "transactions"
    _sources(backend)
    env_dir.mkdir()
    (env_dir / ENV_NAMES[0]).write_bytes(b"old-runtime\x00bytes\n")
    (env_dir / ENV_NAMES[0]).chmod(0o600)
    (env_dir / ENV_NAMES[1]).write_bytes(b"old-runtime-model\n")
    (env_dir / ENV_NAMES[1]).chmod(0o640)
    (env_dir / ENV_NAMES[2]).write_bytes(b"old-projection\n")
    (env_dir / ENV_NAMES[2]).chmod(0o640)
    (env_dir / ENV_NAMES[3]).write_bytes(b"old-authorization\n")
    (env_dir / ENV_NAMES[3]).chmod(0o640)
    uid, gid = os.getuid(), os.getgid()
    monkeypatch.setattr(module, "_resolve_owner", lambda _name=None: (uid, gid))
    monkeypatch.setattr(module, "_resolve_transaction_owner", lambda: (uid, gid))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    prepare = [
        "prepare", "--backend-dir", str(backend), "--env-dir", str(env_dir),
        "--release-sha", RELEASE_SHA, "--transaction-root", str(transaction_root),
    ]
    command = lambda op: [
        op, "--env-dir", str(env_dir), "--release-sha", RELEASE_SHA,
        "--transaction-root", str(transaction_root),
    ]
    return module, env_dir, transaction_root, prepare, command


def _snapshot(env_dir: Path) -> dict[str, tuple[bytes, int, int, int] | None]:
    result = {}
    for name in ENV_NAMES:
        path = env_dir / name
        if not path.exists():
            result[name] = None
        else:
            info = path.stat()
            result[name] = (
                path.read_bytes(), info.st_mode & 0o777, info.st_uid, info.st_gid,
            )
    return result


def test_runtime_model_target_uses_secret_gid_in_release_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, _, root, prepare, _ = _setup(tmp_path, monkeypatch)
    uid, app_gid = os.getuid(), os.getgid()
    secret_gid = app_gid + 10000
    monkeypatch.setattr(
        module,
        "_resolve_owner",
        lambda name=None: (
            uid,
            secret_gid if name == "agent-runtime-model.env" else app_gid,
        ),
    )
    assert module.main(prepare) == 0
    journal = json.loads((root / RELEASE_SHA / "env-journal.json").read_text())
    published = {entry["name"]: entry["published"] for entry in journal["files"]}
    assert published["agent-runtime-model.env"]["gid"] == secret_gid
    assert published["agent-runtime-worker.env"]["gid"] == app_gid


@pytest.mark.parametrize("failure_index", (1, 2, 3, 4))
def test_each_stage_failure_has_zero_target_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_index: int
) -> None:
    module, env_dir, root, prepare, _ = _setup(tmp_path, monkeypatch)
    before = _snapshot(env_dir)
    original = module._write_secure_file
    count = 0

    def fail_stage(path, content, uid, gid):
        nonlocal count
        if path.parent.name == "env-staged":
            count += 1
            if count == failure_index:
                raise OSError("injected stage failure")
        return original(path, content, uid, gid)

    monkeypatch.setattr(module, "_write_secure_file", fail_stage)
    with pytest.raises(module.ProvisioningError, match="目标未修改"):
        module.main(prepare)
    assert _snapshot(env_dir) == before
    assert not (root / RELEASE_SHA).exists()
    assert not (env_dir / "sandbox-worker.env").exists()


@pytest.mark.parametrize("failure_index", (1, 2, 3, 4))
def test_each_publish_failure_restores_all_bytes_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_index: int
) -> None:
    module, env_dir, _, prepare, command = _setup(tmp_path, monkeypatch)
    before = _snapshot(env_dir)
    assert module.main(prepare) == 0
    original = module._atomic_replace_from
    count = 0

    def fail_publish(source, target, uid, gid, mode):
        nonlocal count
        if source.parent.name == "env-staged":
            count += 1
            if count == failure_index:
                raise OSError("injected publish failure")
        return original(source, target, uid, gid, mode)

    monkeypatch.setattr(module, "_atomic_replace_from", fail_publish)
    with pytest.raises(module.ProvisioningError, match="已恢复全部 env"):
        module.main(command("publish"))
    assert _snapshot(env_dir) == before


def test_publish_postcheck_failure_restores_all_prior_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, env_dir, _, prepare, command = _setup(tmp_path, monkeypatch)
    before = _snapshot(env_dir)
    assert module.main(prepare) == 0
    original = module._verify_published
    failed = False

    def fail_once(*args):
        nonlocal failed
        if not failed:
            failed = True
            raise module.ProvisioningError("injected postcheck")
        return original(*args)

    monkeypatch.setattr(module, "_verify_published", fail_once)
    with pytest.raises(module.ProvisioningError, match="已恢复全部 env"):
        module.main(command("publish"))
    assert _snapshot(env_dir) == before


def test_success_rollback_is_idempotent_and_hash_release_fenced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, env_dir, root, prepare, command = _setup(tmp_path, monkeypatch)
    before = _snapshot(env_dir)
    module.main(prepare)
    module.main(command("publish"))
    uid, gid = os.getuid(), os.getgid()
    for name in ENV_NAMES:
        info = (env_dir / name).stat()
        assert (info.st_mode & 0o777, info.st_uid, info.st_gid) == (0o640, uid, gid)
    journal = json.loads((root / RELEASE_SHA / "env-journal.json").read_text())
    serialized = json.dumps(journal)
    assert all(secret not in serialized for secret in SECRETS)
    module.main(command("rollback"))
    module.main(command("rollback"))
    assert _snapshot(env_dir) == before

    module.main(prepare)
    module.main(command("publish"))
    (env_dir / ENV_NAMES[0]).write_text("foreign-change\n", encoding="utf-8")
    changed = _snapshot(env_dir)
    with pytest.raises(module.ProvisioningError, match="hash fence"):
        module.main(command("rollback"))
    assert _snapshot(env_dir) == changed
    wrong = [part if part != RELEASE_SHA else "e" * 40 for part in command("rollback")]
    with pytest.raises((module.ProvisioningError, FileNotFoundError)):
        module.main(wrong)
    assert _snapshot(env_dir) == changed
    assert not (env_dir / "sandbox-worker.env").exists()


def test_kek_source_rejects_unapproved_key_before_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, env_dir, root, prepare, _ = _setup(tmp_path, monkeypatch)
    kek = tmp_path / "backend/.env.kek"
    kek.write_text(
        kek.read_text(encoding="utf-8") + "PROVIDER_API_KEY=forbidden\n",
        encoding="utf-8",
    )
    before = _snapshot(env_dir)
    with pytest.raises(module.ProvisioningError, match="未批准键"):
        module.main(prepare)
    assert _snapshot(env_dir) == before
    assert not (root / RELEASE_SHA).exists()


def test_legacy_gateway_env_blocks_transaction_before_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, env_dir, root, prepare, _ = _setup(tmp_path, monkeypatch)
    legacy = env_dir / "agent-model-gateway-kek.env"
    legacy.write_text("legacy-secret-material\n", encoding="utf-8")
    before = _snapshot(env_dir)
    with pytest.raises(module.ProvisioningError, match="legacy Model Gateway"):
        module.main(prepare)
    assert _snapshot(env_dir) == before
    assert legacy.read_text(encoding="utf-8") == "legacy-secret-material\n"
    assert not (root / RELEASE_SHA).exists()


def test_kek_source_preserves_approved_rotation_keyring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, env_dir, _, prepare, command = _setup(tmp_path, monkeypatch)
    key = "BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ="
    kek = tmp_path / "backend/.env.kek"
    kek.write_text(
        "CONFIG_KEK_CURRENT_VERSION=v2\n"
        f"CONFIG_KEK_KEYRING_JSON='{{\"v1\":\"{key}\",\"v2\":\"{key}\"}}'\n",
        encoding="utf-8",
    )
    module.main(prepare)
    module.main(command("publish"))
    published = (env_dir / "agent-runtime-model.env").read_text(encoding="utf-8")
    assert set(line.split("=", 1)[0] for line in published.splitlines()) == {
        "CONFIG_KEK_CURRENT_VERSION", "CONFIG_KEK_KEYRING_JSON",
    }
    assert '"v1"' in published and '"v2"' in published
