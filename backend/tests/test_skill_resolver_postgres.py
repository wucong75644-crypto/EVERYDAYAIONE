"""Real catalog projection, immutable metadata and additive migration rollback."""

from dataclasses import replace

import psycopg
from psycopg.types.json import Jsonb
import pytest

from core.db_scope import DatabaseAccessKind
from services.skills.contracts import PackageCreate, SkillCatalogMetadata, SkillError
from services.skills.resolver import SkillResolutionContext, SkillResolver
from tests.test_skill_catalog_postgres import (  # noqa: F401
    MIGRATIONS, _use_non_bypass_migration_owner, database, postgres_socket, published,
)
from tests.test_skill_storage import publication, PACKAGE


METADATA_UP = MIGRATIONS / "257_skill_catalog_metadata.sql"
METADATA_DOWN = MIGRATIONS / "rollback/257_skill_catalog_metadata_rollback.sql"


def resolve(repository, org):
    reader = repository(org, DatabaseAccessKind.PROJECTION)
    return SkillResolver().resolve(SkillResolutionContext(
        actor_user_id=reader.scope.actor_user_id, org_id=org, conversation_scope="user",
        agent_domain="general", execution_mode="interactive",
        enabled_feature_flags={"skill_catalog_enabled"},
    ), reader.catalog_candidates())


def test_repository_isolates_pins_disables_retires_and_never_reads_nas(published, monkeypatch):
    conn, repository, a, b, package, v1, storage = published
    v2_data = replace(storage.validate(PACKAGE, publication()), revision="v2",
                      nas_path="platform/report/v2/SKILL.md",
                      catalog_metadata=SkillCatalogMetadata(name="新版本", triggers=("report",), model_selectable=True))
    v2 = repository().publish_revision(package.id, v2_data)
    repository(a).set_assignment(package.id, v1.id, enabled=True)
    repository(b).set_assignment(package.id, v2.id, enabled=True)
    monkeypatch.setattr(storage.__class__, "_read", lambda *args: pytest.fail("Resolver touched NAS"))
    assert [s.revision for s in resolve(repository, a)] == ["v1"]
    assert [s.name for s in resolve(repository, b)] == ["新版本"]
    rows = repository(a, DatabaseAccessKind.PROJECTION).catalog_candidates()
    assert len(rows) == 1 and rows[0].assignment_org_id == a
    assert not {"nas_path", "content_sha256", "body_sha256", "body", "source"} & rows[0].model_dump().keys()
    repository(a).set_assignment(package.id, v2.id, enabled=True)
    assert [s.revision for s in resolve(repository, a)] == ["v2"]
    repository(a).set_assignment(package.id, v2.id, enabled=False)
    assert resolve(repository, a) == []
    assert len(resolve(repository, b)) == 1
    repository().retire_revision(package.id, v2.id)
    assert resolve(repository, b) == []
    conn.execute("RESET ROLE")
    assert conn.execute("SELECT count(*) FROM skill_activation_audits").fetchone()[0] == 0


def test_platform_and_two_org_packages_same_key_never_overwrite(published):
    conn, repository, a, b, platform, platform_revision, storage = published
    base = storage.validate(PACKAGE, publication())
    for org in (a, b):
        package = repository(org).create_package(PackageCreate(
            skill_key="report", source="internal provenance /do-not-expose", scope_kind="org", org_id=org,
        ))
        revision = repository(org).publish_revision(package.id, replace(
            base, nas_path=f"org/{org}/report/v1/SKILL.md", summary=str(org),
        ))
        repository(org).set_assignment(package.id, revision.id, enabled=True)
        repository(org).set_assignment(platform.id, platform_revision.id, enabled=True)
        result = resolve(repository, org)
        assert len(result) == 1 and result[0].source == "org" and result[0].description == str(org)
    repository(a).set_assignment(platform.id, platform_revision.id, enabled=True, priority=1)
    assert resolve(repository, a)[0].source == "platform"
    assert resolve(repository, b)[0].source == "org"
    conn.execute("RESET ROLE")
    assert conn.execute("SELECT count(*) FROM skill_packages").fetchone()[0] == 3


def test_metadata_immutable_and_invalid_direct_db_metadata_fails_closed(published):
    conn, repository, a, _, package, revision, _ = published
    conn.execute("RESET ROLE")
    with pytest.raises(psycopg.errors.CheckViolation, match="REVISION_IMMUTABLE"):
        with conn.transaction():
            conn.execute("UPDATE skill_revisions SET catalog_metadata = %s WHERE id = %s",
                         (Jsonb({"model_selectable": True}), revision.id))
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("""INSERT INTO skill_revisions(package_id,revision,nas_path,content_sha256,body_sha256,summary,catalog_metadata)
                VALUES (%s,'v2','platform/report/v2/SKILL.md',%s,%s,'test','[]')""", (package.id, "a"*64, "b"*64))
    conn.execute("""INSERT INTO skill_revisions(package_id,revision,nas_path,content_sha256,body_sha256,summary,catalog_metadata)
        VALUES (%s,'v2','platform/report/v2/SKILL.md',%s,%s,'test','{"body":"secret"}') RETURNING id""",
        (package.id, "a"*64, "b"*64))
    invalid = conn.execute("SELECT id FROM skill_revisions WHERE revision='v2'").fetchone()[0]
    repository(a).set_assignment(package.id, invalid, enabled=True)
    with pytest.raises(SkillError, match="^SKILL_CATALOG_METADATA_INVALID$"):
        repository(a).catalog_candidates()


def test_metadata_empty_rollback_reapply_preserves_p1_rows(published):
    conn, _, _, _, _, revision, _ = published
    _use_non_bypass_migration_owner(conn)
    conn.execute(METADATA_DOWN.read_text())
    conn.execute(METADATA_UP.read_text())
    conn.execute("RESET ROLE")
    assert conn.execute("SELECT id,catalog_metadata FROM skill_revisions").fetchone() == (revision.id, {})
    assert conn.execute("SELECT relforcerowsecurity FROM pg_class WHERE relname='skill_revisions'").fetchone()[0]


@pytest.mark.parametrize("scope", ["unset", "foreign_org"])
def test_metadata_rollback_refuses_even_hidden_declarations_and_restores_rls(published, scope):
    conn, repository, _, b, package, _, storage = published
    repository().publish_revision(package.id, replace(storage.validate(PACKAGE, publication()),
        revision="v2", nas_path="platform/report/v2/SKILL.md",
        catalog_metadata=SkillCatalogMetadata(model_selectable=True)))
    _use_non_bypass_migration_owner(conn)
    conn.execute("SELECT set_config('app.org_id', %s, true)", ("" if scope == "unset" else str(b),))
    with pytest.raises(psycopg.errors.RaiseException, match="SKILL_CATALOG_METADATA_NOT_EMPTY"):
        with conn.transaction():
            conn.execute(METADATA_DOWN.read_text())
    conn.execute("RESET ROLE")
    assert conn.execute("SELECT count(*) FROM skill_revisions").fetchone()[0] == 2
    assert conn.execute("SELECT relforcerowsecurity FROM pg_class WHERE relname='skill_revisions'").fetchone()[0]


def test_metadata_migration_is_discoverable_with_rollback():
    from scripts.migration_runner import discover_migrations
    migration = next(item for item in discover_migrations() if item.identity == METADATA_UP.name)
    assert migration.rollback_identity == METADATA_DOWN.name
