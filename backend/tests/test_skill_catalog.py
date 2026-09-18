"""Feature gate and fail-closed assignment checks require no external services."""

from unittest.mock import Mock
from uuid import uuid4

import pytest

from core.config import Settings
from services.skills.catalog import SkillCatalog
from services.skills.contracts import SkillError
from tests.test_skill_storage import PACKAGE, publication


def settings(**kwargs):
    return Settings(_env_file=None, database_url="postgresql://unused", jwt_secret_key="test-only", **kwargs)


def test_feature_flag_defaults_off_and_root_is_not_workspace(monkeypatch):
    monkeypatch.delenv("SKILL_CATALOG_ENABLED", raising=False)
    monkeypatch.delenv("SKILL_STORAGE_ROOT", raising=False)
    configured = settings()
    assert configured.skill_catalog_enabled is False
    assert configured.skill_storage_root is None
    monkeypatch.setenv("SKILL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("SKILL_STORAGE_ROOT", "/mnt/controlled-skills")
    assert settings().skill_catalog_enabled is True
    assert settings().skill_storage_root == "/mnt/controlled-skills"


@pytest.mark.parametrize("method,args,kwargs", [
    ("create_package", (PACKAGE,), {}), ("list_packages", (), {}),
    ("publish_revision", (uuid4(), publication()), {}),
    ("set_assignment", (uuid4(), uuid4()), {"enabled": True}),
    ("retire_revision", (uuid4(), uuid4()), {}),
    ("enabled_revisions", (), {}), ("read_assigned_skill", (uuid4(),), {}),
])
def test_disabled_catalog_does_no_database_or_filesystem_work(method, args, kwargs):
    repository = Mock()
    catalog = SkillCatalog(repository, settings(skill_catalog_enabled=False))
    catalog._storage = Mock(side_effect=AssertionError("must not read storage"))
    with pytest.raises(SkillError, match="SKILL_CATALOG_DISABLED"):
        getattr(catalog, method)(*args, **kwargs)
    assert repository.mock_calls == []
    catalog._storage.assert_not_called()


def test_disabled_assignment_cannot_read_content():
    repository = Mock()
    repository.enabled_revisions.return_value = []
    catalog = SkillCatalog(repository, settings(skill_catalog_enabled=True))
    catalog._storage = Mock(side_effect=AssertionError("must not read storage"))
    with pytest.raises(SkillError, match="SKILL_ASSIGNMENT_DISABLED_OR_UNAVAILABLE"):
        catalog.read_assigned_skill(uuid4())
    catalog._storage.assert_not_called()
