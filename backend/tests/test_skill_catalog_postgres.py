"""Real migrations, RLS, repository and catalog in a disposable PostgreSQL.

Reuses the project's socket-only server fixture, never DATABASE_URL or an
existing database. Run with initdb/pg_ctl in PATH; absent binaries are a skip.
"""

from contextlib import contextmanager
import getpass
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope, SET_DATABASE_SCOPE_SQL
from services.skills.catalog import SkillCatalog
from services.skills.contracts import ActivationAuditCreate, PackageCreate, SkillError, revision_path
from services.skills.repository import SkillRepository
from services.skills.storage import SkillStorage
from tests.test_scheduled_task_draft_delete_integration import postgres_socket  # noqa: F401
from tests.test_skill_catalog import settings
from tests.test_skill_storage import DOCUMENT, PACKAGE, publication, write_skill

MIGRATIONS = Path(__file__).parents[1] / "migrations"
MIGRATION = MIGRATIONS / "256_skill_catalog.sql"
ROLLBACK = MIGRATIONS / "rollback/256_skill_catalog_rollback.sql"
SKILL_TABLES = ("skill_packages", "skill_revisions", "skill_assignments", "skill_activation_audits")


class _Pool:
    def __init__(self, connection):
        self.conn = connection

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def database(postgres_socket):
    with psycopg.connect(host=postgres_socket, dbname="postgres", user=getpass.getuser()) as conn:
        conn.execute("CREATE ROLE everydayai")
        conn.execute("CREATE TABLE organizations (id uuid PRIMARY KEY)")
        # Sentinel models existing application data unaffected by up/down.
        conn.execute("CREATE TABLE chat_sentinel (body text); INSERT INTO chat_sentinel VALUES ('unchanged')")
        conn.execute(MIGRATION.read_text())
        conn.execute((MIGRATIONS / "257_skill_catalog_metadata.sql").read_text())
        org_a, org_b, actor = uuid4(), uuid4(), uuid4()
        conn.execute("INSERT INTO organizations VALUES (%s), (%s)", (org_a, org_b))
        conn.execute("SET LOCAL ROLE everydayai")

        def repository(org=None, access=DatabaseAccessKind.RUNTIME_ADMIN):
            return SkillRepository(_Pool(conn), DatabaseScope(str(actor), str(org) if org else None, access))

        try:
            yield conn, repository, org_a, org_b
        finally:
            conn.rollback()


@pytest.fixture
def published(database, tmp_path):
    conn, repository, a, b = database
    root = tmp_path / "skills"
    root.mkdir()
    storage = SkillStorage(str(root), workspace_root=str(tmp_path / "workspace"))
    write_skill(storage)
    platform = repository()
    package = platform.create_package(PACKAGE)
    revision = platform.publish_revision(package.id, storage.validate(package, publication()))
    return conn, repository, a, b, package, revision, storage


def test_migration_structure_and_empty_rollback_reapply(database):
    conn, _, _, _ = database
    conn.execute("RESET ROLE")
    rows = conn.execute("""SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
        WHERE relname IN ('skill_packages','skill_revisions','skill_assignments','skill_activation_audits')""").fetchall()
    assert len(rows) == 4 and all(row[1:] == (True, True) for row in rows)
    assert conn.execute("""SELECT count(*) FROM pg_constraint WHERE contype = 'f'
        AND conrelid IN ('skill_revisions'::regclass, 'skill_assignments'::regclass,
                        'skill_activation_audits'::regclass)""").fetchone()[0] == 6
    conn.execute(ROLLBACK.read_text())
    assert conn.execute("SELECT to_regclass('skill_packages')").fetchone()[0] is None
    assert conn.execute("SELECT body FROM chat_sentinel").fetchone()[0] == "unchanged"
    conn.execute(MIGRATION.read_text())
    assert conn.execute("SELECT count(*) FROM skill_packages").fetchone()[0] == 0


def test_populated_rollback_is_refused_atomically(published):
    conn, _, _, _, package, *_ = published
    conn.execute("RESET ROLE")
    with pytest.raises(psycopg.errors.RaiseException, match="SKILL_CATALOG_NOT_EMPTY"):
        with conn.transaction():
            conn.execute(ROLLBACK.read_text())
    assert conn.execute("SELECT id FROM skill_packages").fetchone()[0] == package.id
    assert conn.execute("SELECT body FROM chat_sentinel").fetchone()[0] == "unchanged"


def _use_non_bypass_migration_owner(conn):
    conn.execute("RESET ROLE")
    conn.execute("CREATE ROLE skill_catalog_migrator NOSUPERUSER NOBYPASSRLS")
    conn.execute("GRANT CREATE ON SCHEMA public TO skill_catalog_migrator")
    conn.execute("GRANT REFERENCES ON organizations TO skill_catalog_migrator")
    for table in SKILL_TABLES:
        conn.execute(psycopg.sql.SQL("ALTER TABLE public.{} OWNER TO skill_catalog_migrator").format(
            psycopg.sql.Identifier(table)))
    conn.execute("ALTER FUNCTION skill_catalog_guard() OWNER TO skill_catalog_migrator")
    conn.execute("ALTER FUNCTION skill_catalog_org_id() OWNER TO skill_catalog_migrator")
    conn.execute("SET LOCAL ROLE skill_catalog_migrator")
    assert conn.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone() == (False, False)


def test_non_bypass_owner_can_rollback_empty_catalog_and_reapply(database):
    conn, *_ = database
    _use_non_bypass_migration_owner(conn)
    with conn.transaction():
        conn.execute(ROLLBACK.read_text())
    assert conn.execute("SELECT to_regclass('skill_packages')").fetchone()[0] is None
    conn.execute(MIGRATION.read_text())
    conn.execute("RESET ROLE")
    assert conn.execute("SELECT count(*) FROM skill_packages").fetchone()[0] == 0
    assert conn.execute("SELECT body FROM chat_sentinel").fetchone()[0] == "unchanged"


@pytest.mark.parametrize("scope", ["unset", "same_org", "other_org"])
def test_rollback_detects_hidden_rows_and_restores_rls(published, scope):
    conn, repository, a, b, package, revision, _ = published
    repository(a).set_assignment(package.id, revision.id)
    repository(a).record_activation(ActivationAuditCreate(package_id=package.id,
        revision_id=revision.id, outcome="skipped", reason_code="test.rollback"))
    conn.execute("RESET ROLE")
    before = {
        table: conn.execute(psycopg.sql.SQL("SELECT * FROM public.{}").format(
            psycopg.sql.Identifier(table))).fetchall() for table in SKILL_TABLES
    }
    _use_non_bypass_migration_owner(conn)
    org_id = {"unset": "", "same_org": str(a), "other_org": str(b)}[scope]
    conn.execute("SELECT set_config('app.org_id', %s, true)", (org_id,))
    # This owner sees no rows under FORCE RLS: the original rollback dropped data.
    assert conn.execute("SELECT count(*) FROM skill_packages").fetchone()[0] == 0
    with pytest.raises(psycopg.errors.RaiseException, match="SKILL_CATALOG_NOT_EMPTY"):
        with conn.transaction():
            conn.execute(ROLLBACK.read_text())
    assert conn.execute("SHOW row_security").fetchone()[0] == "on"
    flags = conn.execute("""SELECT relrowsecurity, relforcerowsecurity FROM pg_class
        WHERE relname = ANY(%s)""", (list(SKILL_TABLES),)).fetchall()
    assert len(flags) == 4 and all(row == (True, True) for row in flags)
    conn.execute("RESET ROLE")
    for table in SKILL_TABLES:
        assert conn.execute(psycopg.sql.SQL("SELECT * FROM public.{}").format(
            psycopg.sql.Identifier(table))).fetchall() == before[table]
    assert conn.execute("SELECT body FROM chat_sentinel").fetchone()[0] == "unchanged"


def test_service_role_cannot_rollback_catalog(database):
    conn, *_ = database
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute(ROLLBACK.read_text())
    flags = conn.execute("""SELECT relrowsecurity, relforcerowsecurity FROM pg_class
        WHERE relname = ANY(%s)""", (list(SKILL_TABLES),)).fetchall()
    assert len(flags) == 4 and all(row == (True, True) for row in flags)


def test_package_namespaces_visibility_and_owner_checks(database):
    conn, repository, a, b = database
    platform = repository().create_package(PACKAGE)
    pa = repository(a).create_package(PackageCreate(**(PACKAGE.model_dump() | {"scope_kind": "org", "org_id": a})))
    pb = repository(b).create_package(PackageCreate(**(PACKAGE.model_dump() | {"scope_kind": "org", "org_id": b})))
    assert {p.id for p in repository(a).list_packages()} == {platform.id, pa.id}
    assert {p.id for p in repository(b).list_packages()} == {platform.id, pb.id}
    assert {p.id for p in repository().list_packages()} == {platform.id}
    with pytest.raises(SkillError, match="PACKAGE_UNAVAILABLE"):
        repository(a).get_package(pb.id)
    with pytest.raises(SkillError, match="OWNER_SCOPE_MISMATCH"):
        repository(a).create_package(PACKAGE)
    with pytest.raises(SkillError, match="CONTROL_ACCESS_REQUIRED"):
        repository(a, DatabaseAccessKind.PROJECTION).create_package(PACKAGE)
    with pytest.raises(psycopg.errors.UniqueViolation):
        repository().create_package(PACKAGE)
    # RLS applies to direct SQL, not only repository predicates.
    conn.execute(SET_DATABASE_SCOPE_SQL, repository(a).scope.settings)
    assert {row[0] for row in conn.execute("SELECT id FROM skill_packages").fetchall()} == {platform.id, pa.id}
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute("""INSERT INTO skill_packages(skill_key,source,scope_kind,org_id)
                VALUES ('foreign','test','org',%s)""", (b,))


def test_revision_unique_and_retirement_is_final(published):
    conn, repository, a, _, package, revision, storage = published
    with pytest.raises(SkillError, match="REVISION_ALREADY_PUBLISHED"):
        repository().publish_revision(package.id, storage.validate(package, publication()))
    with pytest.raises(SkillError, match="OWNER_SCOPE_MISMATCH"):
        repository(a).retire_revision(package.id, revision.id)
    assert repository().retire_revision(package.id, revision.id).status == "retired"
    conn.execute("RESET ROLE")
    with pytest.raises(psycopg.errors.CheckViolation, match="REVISION_IMMUTABLE"):
        with conn.transaction():
            conn.execute("UPDATE skill_revisions SET status='published' WHERE id=%s", (revision.id,))


@pytest.mark.parametrize("column,value", [
    ("revision", "v2"), ("nas_path", "platform/report/v2/SKILL.md"),
    ("summary", "changed"), ("content_sha256", "0" * 64), ("body_sha256", "0" * 64),
])
def test_published_revision_metadata_cannot_be_overwritten_even_by_owner(published, column, value):
    conn, _, _, _, _, revision, _ = published
    conn.execute("RESET ROLE")
    with pytest.raises(psycopg.errors.CheckViolation, match="REVISION_IMMUTABLE"):
        with conn.transaction():
            conn.execute(psycopg.sql.SQL("UPDATE skill_revisions SET {}=%s WHERE id=%s").format(
                psycopg.sql.Identifier(column)), (value, revision.id))


@pytest.mark.parametrize("table", ["skill_packages", "skill_revisions"])
def test_published_identity_cannot_be_deleted(published, table):
    conn, *_ = published
    conn.execute("RESET ROLE")
    with pytest.raises(psycopg.errors.CheckViolation, match="IMMUTABLE"):
        with conn.transaction():
            conn.execute(psycopg.sql.SQL("DELETE FROM {}").format(psycopg.sql.Identifier(table)))


def test_package_identity_cannot_change(published):
    conn, *_ = published
    conn.execute("RESET ROLE")
    with pytest.raises(psycopg.errors.CheckViolation, match="RECORD_IMMUTABLE"):
        with conn.transaction():
            conn.execute("UPDATE skill_packages SET skill_key='renamed'")


def test_assignment_is_scoped_disabled_by_default_and_retired_is_not_resolved(published):
    conn, repository, a, b, package, revision, _ = published
    assert repository(a).set_assignment(package.id, revision.id).enabled is False
    assert repository(a).enabled_revisions() == []
    repository(a).set_assignment(package.id, revision.id, enabled=True, priority=9)
    assert [r.id for r in repository(a).enabled_revisions()] == [revision.id]
    assert repository(b).enabled_revisions() == []
    repository(a).set_assignment(package.id, revision.id, enabled=False)
    assert repository(a).enabled_revisions() == []
    repository(a).set_assignment(package.id, revision.id, enabled=True)
    repository().retire_revision(package.id, revision.id)
    assert repository(a).enabled_revisions() == []
    with pytest.raises(psycopg.errors.CheckViolation, match="REVISION_UNAVAILABLE"):
        repository(a).set_assignment(package.id, revision.id, enabled=True)
    assert repository(a).set_assignment(package.id, revision.id, enabled=False).enabled is False
    with pytest.raises(SkillError, match="ORG_REQUIRED"):
        repository().enabled_revisions()


def test_foreign_assignment_and_mismatched_revision_are_rejected(published):
    conn, repository, a, b, package, revision, _ = published
    private = repository(b).create_package(PackageCreate(skill_key="private", source="org",
                                                         scope_kind="org", org_id=b))
    with pytest.raises(SkillError, match="PACKAGE_UNAVAILABLE"):
        repository(a).set_assignment(private.id, revision.id, enabled=False)
    # Composite FK checks package/revision pairs even for disabled assignments.
    second = repository().create_package(PackageCreate(skill_key="second", source="test", scope_kind="platform"))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        repository(a).set_assignment(second.id, revision.id, enabled=False)
    conn.execute(SET_DATABASE_SCOPE_SQL, repository(a).scope.settings)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute("INSERT INTO skill_assignments(org_id,package_id,revision_id) VALUES (%s,%s,%s)",
                         (b, package.id, revision.id))


def test_organization_cannot_modify_platform_revision_through_sql(published):
    conn, repository, a, _, package, revision, _ = published
    conn.execute(SET_DATABASE_SCOPE_SQL, repository(a).scope.settings)
    assert conn.execute("UPDATE skill_revisions SET status='retired' WHERE id=%s RETURNING id",
                        (revision.id,)).fetchall() == []
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute("""INSERT INTO skill_revisions(package_id,revision,nas_path,content_sha256,body_sha256,summary)
                VALUES (%s,'v2','platform/report/v2/SKILL.md',%s,%s,'test')""",
                (package.id, "a" * 64, "b" * 64))


def test_invalid_nas_path_is_rejected_by_database(published):
    conn, repository, _, _, package, _, _ = published
    conn.execute(SET_DATABASE_SCOPE_SQL, repository().scope.settings)
    with pytest.raises(psycopg.errors.CheckViolation, match="PATH_IDENTITY_MISMATCH"):
        with conn.transaction():
            conn.execute("""INSERT INTO skill_revisions(package_id,revision,nas_path,content_sha256,body_sha256,summary)
                VALUES (%s,'v2','../workspace/SKILL.md',%s,%s,'test')""", (package.id, "a" * 64, "b" * 64))


def test_audits_append_only_scoped_and_no_automatic_events(published):
    conn, repository, a, b, package, revision, _ = published
    conn.execute(SET_DATABASE_SCOPE_SQL, repository(a).scope.settings)
    assert conn.execute("SELECT count(*) FROM skill_activation_audits").fetchone()[0] == 0
    aid = repository(a).record_activation(ActivationAuditCreate(package_id=package.id,
        revision_id=revision.id, outcome="skipped", reason_code="test.disabled"))
    conn.execute(SET_DATABASE_SCOPE_SQL, repository(b).scope.settings)
    assert conn.execute("SELECT * FROM skill_activation_audits").fetchall() == []
    conn.execute("RESET ROLE")
    assert conn.execute("SELECT id FROM skill_activation_audits").fetchone()[0] == aid
    for statement in ("DELETE FROM skill_activation_audits", "UPDATE skill_activation_audits SET outcome='activated'"):
        with pytest.raises(psycopg.errors.CheckViolation, match="RECORD_IMMUTABLE"):
            with conn.transaction():
                conn.execute(statement)


def test_full_catalog_publish_assign_read_disable_and_tamper(database, tmp_path):
    _, repository, a, _ = database
    root = tmp_path / "skills"
    root.mkdir()
    configured = settings(skill_catalog_enabled=True, skill_storage_root=str(root),
                          file_workspace_root=str(tmp_path / "workspace"))
    platform = SkillCatalog(repository(), configured)
    org = SkillCatalog(repository(a), configured)
    package = platform.create_package(PACKAGE)
    storage = SkillStorage(str(root), workspace_root=configured.file_workspace_root)
    target = write_skill(storage)
    revision = platform.publish_revision(package.id, publication())
    org.set_assignment(package.id, revision.id, enabled=True)
    assert org.read_assigned_skill(package.id).summary == "报表说明"
    target.write_text(DOCUMENT + "tampered")
    with pytest.raises(SkillError, match="CONTENT_HASH_MISMATCH"):
        org.read_assigned_skill(package.id)
    org.set_assignment(package.id, revision.id, enabled=False)
    with pytest.raises(SkillError, match="ASSIGNMENT_DISABLED"):
        org.read_assigned_skill(package.id)


def test_private_revisions_are_org_scoped_and_priority_is_descending(published):
    conn, repository, a, b, platform, platform_revision, storage = published
    private_revisions = []
    for org in (a, b):
        package = repository(org).create_package(PackageCreate(**(PACKAGE.model_dump()
            | {"scope_kind": "org", "org_id": org})))
        target = storage.root / revision_path(package, "v1")
        target.parent.mkdir(parents=True)
        target.write_text(DOCUMENT)
        revision = repository(org).publish_revision(package.id, storage.validate(package, publication()))
        repository(org).set_assignment(package.id, revision.id, enabled=True, priority=10)
        private_revisions.append(revision)
    repository(a).set_assignment(platform.id, platform_revision.id, enabled=True, priority=1)
    assert [r.id for r in repository(a).enabled_revisions()] == [private_revisions[0].id, platform_revision.id]
    assert [r.id for r in repository(b).enabled_revisions()] == [private_revisions[1].id]
    conn.execute(SET_DATABASE_SCOPE_SQL, repository(a).scope.settings)
    assert {row[0] for row in conn.execute("SELECT id FROM skill_revisions").fetchall()} == {
        private_revisions[0].id, platform_revision.id}
    with pytest.raises(SkillError, match="PACKAGE_UNAVAILABLE"):
        repository(a).record_activation(ActivationAuditCreate(package_id=private_revisions[1].package_id,
            revision_id=private_revisions[1].id, outcome="skipped", reason_code="test.foreign"))


def test_database_rejects_unscoped_or_readonly_writes(published):
    conn, repository, a, _, package, revision, _ = published
    for access_kind in ("", "projection"):
        conn.execute("SELECT set_config('app.access_kind', %s, true)", (access_kind,))
        conn.execute("SELECT set_config('app.org_id', %s, true)", (str(a),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("INSERT INTO skill_assignments(org_id,package_id,revision_id) VALUES (%s,%s,%s)",
                             (a, package.id, revision.id))
    with pytest.raises(SkillError, match="CONTROL_ACCESS_REQUIRED"):
        repository(a, DatabaseAccessKind.PROJECTION).set_assignment(package.id, revision.id)


def test_migration_is_discoverable_with_rollback():
    from scripts.migration_runner import discover_migrations
    migration = next(item for item in discover_migrations() if item.identity == MIGRATION.name)
    assert migration.rollback_identity == ROLLBACK.name
