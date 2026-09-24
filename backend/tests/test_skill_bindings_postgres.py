"""Real RLS, publication, session pins and snapshots in disposable PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope, SET_DATABASE_SCOPE_SQL
from services.skills.binding_repository import SkillBindingRepository
from services.skills.contracts import SkillError
from services.skills.resolver import SkillResolutionContext
from services.skills.runtime import SkillReplayError
from services.skills.runtime_source import ActorSkillSource
from services.tools import ToolContext
from tests.test_skill_authoring_postgres import (
    environment, postgres_socket, create, action, publish, MIGRATIONS,  # noqa: F401
)
from tests.test_skill_runtime import state


@pytest.fixture
def configured(environment):
    env = environment
    env.conversation, env.other_conversation, env.member, env.admin = uuid4(), uuid4(), uuid4(), uuid4()
    with env.pool.connection(privileged=True) as conn:
        conn.execute("ALTER TABLE organizations ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        conn.execute("CREATE TABLE users(id UUID PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active')")
        conn.execute("CREATE TABLE org_members(org_id UUID, user_id UUID, status TEXT, role TEXT)")
        conn.execute("""CREATE TABLE conversations(id UUID PRIMARY KEY, org_id UUID, user_id UUID,
                     scope_type TEXT NOT NULL DEFAULT 'user', scope_id TEXT)""")
        for user, role in [(env.actor, "member"), (env.member, "member"), (env.admin, "admin")]:
            conn.execute("INSERT INTO users(id) VALUES (%s)", (user,))
            conn.execute("INSERT INTO org_members VALUES (%s,%s,'active',%s)", (env.org, user, role))
        for cid, user in [(env.conversation, env.actor), (env.other_conversation, env.member)]:
            conn.execute("INSERT INTO conversations(id,org_id,user_id,scope_id) VALUES (%s,%s,%s,%s)",
                         (cid, env.org, user, str(user)))
        conn.execute("GRANT SELECT ON users,org_members,organizations,conversations TO everydayai")
        conn.execute((MIGRATIONS / "263_conversation_skill_bindings.sql").read_text())
    def repo(user=env.actor, access=DatabaseAccessKind.RUNTIME_ADMIN, org=env.org):
        return SkillBindingRepository(env.pool, DatabaseScope(str(user), str(org), access))
    env.repo = repo
    svc = env.service()
    env.pid = create(svc)
    publish(svc, env.pid)
    env.candidate = repo().catalog_candidates()[0]
    return env


def source(env):
    context = ToolContext(actor_user_id=str(env.actor), workspace_owner_id=str(env.actor),
        org_id=str(env.org), conversation_id=str(env.conversation), context_scope="user",
        personal_context_allowed=True, permission_mode="auto", execution_mode="interactive", agent_domain="general")
    result = ActorSkillSource(SimpleNamespace(db=SimpleNamespace(pool=env.pool)), context, env.config)
    result._resolution_context = AsyncMock(return_value=SkillResolutionContext(
        actor_user_id=env.actor, org_id=env.org, conversation_scope="user", agent_domain="general",
        execution_mode="interactive", enabled_feature_flags={"skill_catalog_enabled"}))
    return result


async def test_binding_new_turn_and_checkpoint_keep_original_revision_after_publication_and_removal(configured):
    env = configured
    binding_id = env.repo().add_binding(env.conversation, env.candidate)
    original = state(source(env))
    await original.initialize()
    assert (await original.activate_session("orders"))["ok"]
    checkpoint = original.checkpoint()
    svc = env.service()
    action(svc, env.pid, "start_draft")
    v2 = publish(svc, env.pid)["draft"]["revision"]
    assert v2 != env.candidate.revision
    fresh = state(source(env))
    await fresh.initialize()
    assert fresh.directory["orders"].revision == env.candidate.revision
    assert (await fresh.activate_session("orders"))["ok"]
    with pytest.raises(SkillError, match="BINDING_CONFLICT"):
        env.repo().add_binding(env.conversation, env.repo().catalog_candidates()[0])
    env.repo().remove_binding(env.conversation, binding_id)
    assert env.repo().bindings(env.conversation) == []
    restored = state(source(env))
    await restored.initialize(checkpoint)
    assert restored.checkpoint() == checkpoint
    new = state(source(env))
    await new.initialize()
    assert new.session_skill_ids == ()
    assert new.directory["orders"].revision == v2


async def test_revoked_assignment_keeps_binding_and_stops_activation_and_resume(configured):
    env = configured
    env.repo().add_binding(env.conversation, env.candidate)
    original = state(source(env))
    await original.initialize()
    await original.activate_session("orders")
    checkpoint = original.checkpoint()
    revision = env.repo().assigned_revision(env.pid, env.candidate.revision)
    env.repo().set_assignment(env.pid, revision.id, enabled=False)
    assert env.repo().bindings(env.conversation)[0]["available"] is False
    fresh = state(source(env))
    await fresh.initialize()
    assert (await fresh.activate_session("orders"))["code"] == "SKILL_PINNED_REVISION_UNAVAILABLE"
    with pytest.raises(SkillReplayError, match="PINNED_REVISION_UNAVAILABLE"):
        await state(source(env)).initialize(checkpoint)


def test_owner_admin_and_tenant_rls_and_no_runtime_writer(configured):
    env = configured
    repo = env.repo()
    bid = repo.add_binding(env.conversation, env.candidate)
    assert repo.add_binding(env.conversation, env.candidate) == bid
    assert env.repo(env.member).bindings(env.conversation) == []
    assert env.repo(org=env.other).bindings(env.conversation) == []
    env.repo(env.member).remove_binding(env.conversation, bid)
    assert len(repo.bindings(env.conversation)) == 1
    env.repo(env.admin).remove_binding(env.conversation, bid)
    assert repo.bindings(env.conversation) == []
    env.repo(env.admin).add_binding(env.other_conversation, env.candidate)
    for access in (DatabaseAccessKind.PROJECTION, DatabaseAccessKind.RUNTIME):
        with pytest.raises(SkillError, match="CONTROL_ACCESS_REQUIRED"):
            env.repo(access=access).add_binding(env.conversation, env.candidate)
        with env.pool.connection() as conn:
            conn.execute(SET_DATABASE_SCOPE_SQL, env.repo(access=access).scope.settings)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute("""INSERT INTO conversation_skill_bindings
                        (conversation_id,org_id,package_id,revision_id,skill_key,created_by)
                        SELECT %s,%s,package_id,id,'orders',%s FROM skill_revisions WHERE package_id=%s""",
                        (env.conversation, env.org, env.actor, env.pid))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.repo(env.member).add_binding(env.conversation, env.candidate)


def test_binding_cannot_be_updated_even_by_database_owner(configured):
    env = configured
    env.repo().add_binding(env.conversation, env.candidate)
    with env.pool.connection(privileged=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match="BINDING_IMMUTABLE"):
            with conn.transaction():
                conn.execute("UPDATE conversation_skill_bindings SET skill_key='changed'")


def test_bound_revision_and_cross_package_identity_are_checked(configured):
    env = configured
    with env.pool.connection() as conn:
        conn.execute(SET_DATABASE_SCOPE_SQL, env.repo().scope.settings)
        with pytest.raises(psycopg.errors.CheckViolation, match="REVISION_UNAVAILABLE"):
            with conn.transaction():
                conn.execute("""INSERT INTO conversation_skill_bindings
                    (conversation_id,org_id,package_id,revision_id,skill_key,created_by)
                    SELECT %s,%s,package_id,id,'forged',%s FROM skill_revisions WHERE package_id=%s""",
                    (env.conversation, env.org, env.actor, env.pid))


def test_concurrent_binding_limit_is_serialized(configured):
    env = configured
    svc = env.service()
    for index in range(5):
        publish(svc, create(svc, key=f"extra-{index}"))
    candidates = env.repo().catalog_candidates()
    for candidate in candidates[:3]:
        env.repo().add_binding(env.conversation, candidate)
    def add(candidate):
        try:
            return env.repo().add_binding(env.conversation, candidate)
        except psycopg.errors.CheckViolation as error:
            assert "SKILL_BINDING_LIMIT" in str(error)
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(add, candidates[3:5]))
    assert len([r for r in results if r]) == 1
    assert len(env.repo().bindings(env.conversation)) == 4


def test_populated_rollback_refused_and_empty_rollback_reapplies(configured):
    env = configured
    bid = env.repo().add_binding(env.conversation, env.candidate)
    rollback = (MIGRATIONS / "rollback/263_conversation_skill_bindings_rollback.sql").read_text()
    with env.pool.connection(privileged=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="BINDINGS_NOT_EMPTY"):
            with conn.transaction():
                conn.execute(rollback)
    assert len(env.repo().bindings(env.conversation)) == 1
    env.repo().remove_binding(env.conversation, bid)
    with env.pool.connection(privileged=True) as conn:
        conn.execute(rollback)
        conn.execute((MIGRATIONS / "263_conversation_skill_bindings.sql").read_text())
    assert env.repo().bindings(env.conversation) == []
