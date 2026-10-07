"""Opt-in global platform grants, personal discovery and real RLS boundaries."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import hashlib
from pathlib import Path
import psycopg
import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope, SET_DATABASE_SCOPE_SQL
from services.skills.authoring_contracts import CreateSkill, DraftContent, TransitionDraft
from services.skills.contracts import SkillError, PackageCreate, PublishRevision
from services.skills.storage import SkillStorage
from services.skills.renderer import render, validate_resource_templates
from services.skills.repository import SkillRepository
from services.skills.binding_repository import SkillBindingRepository
from services.skills.resolver import SkillResolutionContext, SkillResolver
from services.skills.runtime_source import ActorSkillSource
from tests.test_skill_runtime import state, activate
from tests.test_skill_personal_postgres import environment, postgres_socket, MIGRATIONS  # noqa: F401


def test_actual_main_image_entry_passes_publication_and_runtime_template_validation():
    raw = (Path(__file__).parents[2] / 'examples/skills/catalog/platform/ecommerce-main-images/v1/SKILL.md').read_bytes()
    body = raw.split(b'---\n', 2)[2]
    package = PackageCreate(skill_key='ecommerce-main-images', source='test', scope_kind='platform')
    publication = PublishRevision(revision='v1', content_sha256=hashlib.sha256(raw).hexdigest(),
        body_sha256=hashlib.sha256(body).hexdigest())
    skill = SkillStorage.validate_bytes(package, publication, raw)
    validate_resource_templates(skill, {})
    assert render(skill.body, {}) == body.decode()
    assert skill.catalog_metadata.model_selectable
    assert set(skill.catalog_metadata.allowed_tool_names) == {'get_conversation_context', 'plan_ecommerce_images', 'generate_image'}
    assert not skill.resources.assets


@pytest.fixture
def global_skill(environment):
    env = environment
    with env.pool.connection(privileged=True) as c:
        c.execute('GRANT CREATE ON SCHEMA public TO everydayai')
        c.execute('ALTER TABLE skill_assignments OWNER TO everydayai')
        c.execute('ALTER FUNCTION skill_binding_guard() OWNER TO everydayai')
        c.execute((MIGRATIONS / '280_skill_platform_assignments.sql').read_text())
    author = env.service(user=env.super_admin, owner_scope='platform')
    pid = author.create(CreateSkill(skill_key='main-images', content=DraftContent(
        description='主图策划', body='调用策划工具，再按方案生成。',
        catalog_metadata={'model_selectable': True, 'allowed_tool_names': ['plan_ecommerce_images', 'generate_image']},
    )))['package_id']
    for action in ('submit', 'approve', 'publish'):
        author.transition(pid, TransitionDraft(expected_version=author.detail(pid)['draft']['version'], action=action))
    with env.pool.connection() as c:
        rid = c.execute('SELECT id FROM skill_revisions WHERE package_id=%s', (pid,)).fetchone()[0]
    env.pid, env.rid, env.author = pid, rid, author
    return env


def reader(env, *, org=None, actor=None, admin=False, platform=False):
    return SkillBindingRepository(env.pool, DatabaseScope(
        str(actor or env.actor), str(org) if org else None,
        DatabaseAccessKind.RUNTIME_ADMIN if admin else DatabaseAccessKind.PROJECTION,
        request_id='global-skill-test'), owner_scope='platform' if platform else None)


def resolve(env, repository):
    ctx = SkillResolutionContext(actor_user_id=repository.scope.actor_user_id, org_id=repository.scope.org_id,
        conversation_scope='user', agent_domain='general', execution_mode='interactive',
        enabled_feature_flags={'skill_catalog_enabled'})
    return SkillResolver().select(ctx, repository.catalog_candidates())


def test_global_opt_in_personal_and_org_discovery_load_and_revocation(global_skill):
    env = global_skill
    assert not resolve(env, reader(env))
    assert not resolve(env, reader(env, org=env.org))
    env.author.repository.set_platform_assignment(env.pid, env.rid, enabled=True)
    for repository in (reader(env), reader(env, org=env.org), reader(env, actor=env.other)):
        candidates = resolve(env, repository)
        assert len(candidates) == 1 and candidates[0].global_assignment
        assert candidates[0].assignment_org_id is None
        assert repository.assigned_revision(env.pid, candidates[0].revision).id == env.rid
        assert repository.pinned_candidates('main-images', candidates[0].revision)[0].global_assignment
    assert env.author.detail(env.pid)['available_revision']
    env.author.repository.set_platform_assignment(env.pid, env.rid, enabled=False)
    assert not resolve(env, reader(env))
    with pytest.raises(SkillError, match='PINNED_REVISION_UNAVAILABLE'):
        reader(env).assigned_revision(env.pid, env.author.detail(env.pid)['revisions'][0]['revision'])


def test_org_explicit_disable_overrides_global_without_hiding_from_other_users(global_skill):
    env = global_skill
    env.author.repository.set_platform_assignment(env.pid, env.rid, enabled=True)
    org = reader(env, org=env.org, admin=True)
    org.set_assignment(env.pid, env.rid, enabled=False)
    assert not resolve(env, org)
    assert resolve(env, reader(env))
    with pytest.raises(SkillError, match='PINNED_REVISION_UNAVAILABLE'):
        org.assigned_revision(env.pid, env.author.detail(env.pid)['revisions'][0]['revision'])
    org.set_assignment(env.pid, env.rid, enabled=True)
    assert not resolve(env, org)[0].global_assignment
    assert resolve(env, org)[0].assignment_org_id == env.org


def test_global_binding_for_personal_and_org_rechecks_live_grant(global_skill):
    env = global_skill
    env.author.repository.set_platform_assignment(env.pid, env.rid, enabled=True)
    org = reader(env, org=env.org, admin=True)
    binding = org.add_binding(env.conversation, resolve(env, org)[0])
    assert org.bindings(env.conversation)[0]['available']
    org.remove_binding(env.conversation, binding)
    with env.pool.connection(privileged=True) as c:
        c.execute('UPDATE conversations SET org_id=NULL WHERE id=%s', (env.conversation,))
    personal = reader(env, admin=True)
    personal.add_binding(env.conversation, resolve(env, personal)[0])
    row = personal.bindings(env.conversation)[0]
    assert row['available'] and personal.candidate(row).global_assignment
    env.author.repository.set_platform_assignment(env.pid, env.rid, enabled=False)
    assert personal.bindings(env.conversation)[0]['available'] is False


@pytest.mark.parametrize('mode', ['org_admin', 'personal_user', 'projection', 'null_actor_untracked'])
def test_untrusted_global_writers_are_rejected_by_service_and_rls(global_skill, mode):
    env = global_skill
    scopes = {
        'org_admin': DatabaseScope(str(env.other), str(env.org), DatabaseAccessKind.RUNTIME_ADMIN, 'reject'),
        'personal_user': DatabaseScope(str(env.actor), None, DatabaseAccessKind.RUNTIME_ADMIN, 'reject'),
        'projection': DatabaseScope(str(env.super_admin), None, DatabaseAccessKind.PROJECTION, 'reject'),
        'null_actor_untracked': DatabaseScope(None, None, DatabaseAccessKind.RUNTIME_ADMIN),
    }
    scope = scopes[mode]
    repo = SkillRepository(env.pool, scope, owner_scope='platform')
    with pytest.raises(SkillError):
        repo.set_platform_assignment(env.pid, env.rid, enabled=True)
    with env.pool.connection() as c:
        c.execute(SET_DATABASE_SCOPE_SQL, scope.settings)
        c.execute("SELECT set_config('app.skill_action', 'platform_assign', true)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute('INSERT INTO skill_assignments(org_id,package_id,revision_id,enabled) VALUES(NULL,%s,%s,true)',
                      (env.pid, env.rid))


def test_global_requires_platform_package_and_audits_operator(global_skill):
    env = global_skill
    org = env.service(org_id=env.org, owner_scope='org')
    private = org.create(CreateSkill(skill_key='org-only', content=DraftContent(description='私有', body='仅组织。')))['package_id']
    for action in ('submit', 'approve', 'publish'):
        org.transition(private, TransitionDraft(expected_version=org.detail(private)['draft']['version'], action=action))
    with env.pool.connection(privileged=True) as c:
        rid = c.execute('SELECT id FROM skill_revisions WHERE package_id=%s', (private,)).fetchone()[0]
        c.execute(SET_DATABASE_SCOPE_SQL, DatabaseScope(None, None, DatabaseAccessKind.RUNTIME_ADMIN, 'operator').settings)
        c.execute("SELECT set_config('app.skill_action', 'platform_assign', true)")
        with pytest.raises(psycopg.errors.CheckViolation, match='GLOBAL_ASSIGNMENT_REQUIRES_PLATFORM'):
            c.execute('INSERT INTO skill_assignments(org_id,package_id,revision_id) VALUES(NULL,%s,%s)', (private, rid))
    operator = SkillRepository(env.pool, DatabaseScope(None, None, DatabaseAccessKind.RUNTIME_ADMIN, 'operator'), owner_scope='platform')
    operator.set_platform_assignment(env.pid, env.rid, enabled=True)
    with env.pool.connection(privileged=True) as c:
        assert c.execute("SELECT count(*) FROM skill_change_audits WHERE package_id=%s AND request_id='operator' AND actor_user_id IS NULL AND action='platform_assign'", (env.pid,)).fetchone()[0] == 1


async def test_global_activation_and_checkpoint_restore_keep_exact_body_and_tool_ceiling(global_skill):
    env = global_skill
    env.author.repository.set_platform_assignment(env.pid, env.rid, enabled=True)
    context = SkillResolutionContext(actor_user_id=env.actor, org_id=None, conversation_scope='user',
        agent_domain='general', execution_mode='interactive', enabled_feature_flags={'skill_catalog_enabled'})
    source = ActorSkillSource(SimpleNamespace(db=SimpleNamespace(pool=env.pool)), SimpleNamespace(
        actor_user_id=str(env.actor), org_id=None), env.config)
    source.session_bindings = AsyncMock(return_value=[])
    source._resolution_context = AsyncMock(return_value=context)
    runtime = state(source, platform_tool_names={'plan_ecommerce_images', 'generate_image', 'file_delete'})
    await runtime.initialize()
    assert (await runtime.activate(activate('main-images')))['ok']
    saved = runtime.checkpoint()
    assert saved['directory'][0]['global_assignment']
    assert runtime.effective_allowed_tool_names == {'plan_ecommerce_images', 'generate_image'}
    restored = state(source, platform_tool_names={'plan_ecommerce_images', 'generate_image', 'file_delete'})
    await restored.initialize(saved)
    assert restored.messages() == runtime.messages()
    assert restored.effective_allowed_tool_names == runtime.effective_allowed_tool_names
