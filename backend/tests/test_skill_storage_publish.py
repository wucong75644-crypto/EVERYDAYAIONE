"""Exclusive NAS creation, verified retries and failed writes."""
import os
from pathlib import Path

import pytest

from services.skills.contracts import SkillError, revision_path
from tests.test_skill_storage import storage, PACKAGE, DOCUMENT, publication  # noqa: F401


def test_publish_creates_readonly_file_and_identical_retry_keeps_inode(storage):
    result = storage.publish(PACKAGE, publication(), DOCUMENT.encode())
    target = storage.root / result.nas_path
    inode = target.stat().st_ino
    assert target.stat().st_mode & 0o222 == 0
    assert target.stat().st_nlink == 1 and target.read_text() == DOCUMENT
    assert storage.publish(PACKAGE, publication(), DOCUMENT.encode()) == result
    assert target.stat().st_ino == inode
    assert not list(storage.root.rglob('.publishing-*'))


def test_existing_file_with_wrong_hash_never_overwritten(storage):
    target = storage.root / revision_path(PACKAGE, 'v1')
    target.parent.mkdir(parents=True)
    target.write_text('unreviewed')
    with pytest.raises(SkillError, match='HASH_MISMATCH'):
        storage.publish(PACKAGE, publication(), DOCUMENT.encode())
    assert target.read_text() == 'unreviewed'


@pytest.mark.parametrize('operation', ['mkdir', 'rename', 'fsync'])
def test_nas_io_failure_returns_error_without_publishing_partial_body(storage, monkeypatch, operation):
    def fail(*args, **kwargs):
        raise PermissionError('NAS read only')
    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(SkillError, match='WRITE_REJECTED'):
        storage.publish(PACKAGE, publication(), DOCUMENT.encode())
    assert not list(storage.root.rglob('SKILL.md'))
    assert not list(storage.root.rglob('.publishing-*'))


@pytest.mark.parametrize('component', ['platform', 'platform/report', 'platform/report/v1', 'platform/report/v1/SKILL.md'])
def test_publish_rejects_symlink_at_every_boundary(storage, tmp_path, component):
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = storage.root / component
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside)
    with pytest.raises(SkillError, match='WRITE_REJECTED|READ_REJECTED|OUTSIDE_STORAGE'):
        storage.publish(PACKAGE, publication(), DOCUMENT.encode())
    assert not list(outside.iterdir())


def test_retry_ignores_staging_directory_left_by_interrupted_writer(storage):
    parent = storage.root / 'platform/report'
    abandoned = parent / '.publishing-interrupted'
    abandoned.mkdir(parents=True)
    (abandoned / 'SKILL.md').write_text('partial')
    validated = storage.publish(PACKAGE, publication(), DOCUMENT.encode())
    final = storage.root / validated.nas_path
    assert final.read_text() == DOCUMENT and final.stat().st_nlink == 1
    assert (abandoned / 'SKILL.md').read_text() == 'partial'


def test_nas_rename_acknowledgement_loss_keeps_complete_release_for_retry(storage, monkeypatch):
    rename = os.rename
    def uncertain(*args, **kwargs):
        rename(*args, **kwargs)
        raise OSError('NAS acknowledgement lost')
    monkeypatch.setattr(os, 'rename', uncertain)
    with pytest.raises(SkillError, match='WRITE_REJECTED'):
        storage.publish(PACKAGE, publication(), DOCUMENT.encode())
    final = storage.root / revision_path(PACKAGE, 'v1')
    assert final.read_text() == DOCUMENT
    inode = final.stat().st_ino
    monkeypatch.setattr(os, 'rename', rename)
    storage.publish(PACKAGE, publication(), DOCUMENT.encode())
    assert final.stat().st_ino == inode


def test_cleanup_failure_is_safe_and_does_not_prevent_future_publication(storage, monkeypatch):
    rename, unlink = os.rename, os.unlink
    def denied(*args, **kwargs):
        raise PermissionError('NAS permission temporarily lost')
    monkeypatch.setattr(os, 'rename', denied)
    monkeypatch.setattr(os, 'unlink', denied)
    with pytest.raises(SkillError, match='WRITE_REJECTED'):
        storage.publish(PACKAGE, publication(), DOCUMENT.encode())
    assert not (storage.root / revision_path(PACKAGE, 'v1')).exists()
    monkeypatch.setattr(os, 'rename', rename)
    monkeypatch.setattr(os, 'unlink', unlink)
    result = storage.publish(PACKAGE, publication(), DOCUMENT.encode())
    assert (storage.root / result.nas_path).read_text() == DOCUMENT
