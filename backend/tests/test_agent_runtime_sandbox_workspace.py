from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from services.agent.runtime.sandbox.workspace import SandboxWorkspaceStore
from services.agent.runtime.sandbox.contracts import bounded_summary


ACTION_ID = "11111111-1111-1111-1111-111111111111"
JOB_ID = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_code_staging_is_immutable_and_hash_bound(tmp_path: Path) -> None:
    store = SandboxWorkspaceStore(tmp_path.resolve())
    content = b"print(1)"
    digest = hashlib.sha256(content).hexdigest()
    await store.stage_code(
        action_id=ACTION_ID, attempt_id=JOB_ID,
        content=content, expected_sha256=digest,
    )
    await store.stage_code(
        action_id=ACTION_ID, attempt_id=JOB_ID,
        content=content, expected_sha256=digest,
    )
    assert store.read_code(
        action_id=ACTION_ID, attempt_id=JOB_ID, expected_sha256=digest,
    ) == content
    with pytest.raises(ValueError, match="HASH_CONFLICT"):
        await store.stage_code(
            action_id=ACTION_ID, content=b"changed",
            attempt_id=JOB_ID,
            expected_sha256=digest,
        )


@pytest.mark.asyncio
async def test_content_addressed_materialize_is_idempotent(
    tmp_path: Path,
) -> None:
    store = SandboxWorkspaceStore(tmp_path.resolve())
    _, output = store.prepare_job(JOB_ID)
    (output / "result.bin").write_bytes(b"result")
    first = await store.materialize_outputs(
        job_id=JOB_ID, output_dir=output,
        max_bytes=1024, max_files=2,
    )
    second = await store.materialize_outputs(
        job_id=JOB_ID, output_dir=output,
        max_bytes=1024, max_files=2,
    )
    assert first == second
    assert first[0].reference.startswith("workspace-object:sha256:")


@pytest.mark.asyncio
async def test_symlink_and_hardlink_outputs_are_rejected(tmp_path: Path) -> None:
    store = SandboxWorkspaceStore(tmp_path.resolve())
    _, output = store.prepare_job(JOB_ID)
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    os.symlink(outside, output / "escape")
    with pytest.raises(ValueError, match="LINK_REJECTED"):
        await store.materialize_outputs(
            job_id=JOB_ID, output_dir=output,
            max_bytes=1024, max_files=2,
        )
    (output / "escape").unlink()
    os.link(outside, output / "hard")
    with pytest.raises(ValueError, match="LINK_REJECTED"):
        await store.materialize_outputs(
            job_id=JOB_ID, output_dir=output,
            max_bytes=1024, max_files=2,
        )


def test_partial_quarantine_and_cleanup_are_job_scoped(tmp_path: Path) -> None:
    store = SandboxWorkspaceStore(tmp_path.resolve())
    _, output = store.prepare_job(JOB_ID)
    (output / "partial.bin").write_bytes(b"partial")
    manifest = store.quarantine(
        JOB_ID, output, max_bytes=1024, max_files=2,
    )
    assert manifest[0]["temporary_object_ref"] == f"sandbox-temp:{JOB_ID}"
    assert store.cleanup_job(JOB_ID)
    assert not (tmp_path / "jobs" / JOB_ID).exists()
    assert not (tmp_path / "quarantine" / JOB_ID).exists()


def test_output_summary_never_persists_user_controlled_text() -> None:
    value = (
        b"safe line\nerror:/home/user/private.txt\n"
        b"api_key=do-not-store\n" + b"A" * 100 + b"\n" + b"x" * 9000
    )
    summary, original, digest, truncated = bounded_summary(value)
    assert summary == ""
    assert original == len(value)
    assert len(digest) == 64
    assert truncated


def test_invalid_utf8_summary_remains_within_database_byte_limit() -> None:
    summary, original, _, truncated = bounded_summary(b"\xff" * 8192)
    assert summary == ""
    assert original == 8192
    assert truncated


def test_workspace_root_rejects_symlink_and_group_access(
    tmp_path: Path,
) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o770)
    unsafe.chmod(0o770)
    with pytest.raises(ValueError, match="ROOT_UNSAFE"):
        SandboxWorkspaceStore(unsafe)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="ROOT_UNSAFE"):
        SandboxWorkspaceStore(link)
