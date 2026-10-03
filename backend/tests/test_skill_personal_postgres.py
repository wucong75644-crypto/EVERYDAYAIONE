"""Personal ownership and chat-scope publication exercise real RLS migrations."""
from contextlib import contextmanager
import getpass
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.skills.authoring import SkillAuthoring
from services.skills.authoring_contracts import CreateSkill, DraftContent, TransitionDraft
from services.skills.binding_repository import SkillBindingRepository
from services.skills.chat_creation import ChatSkillCandidate, content_digest
from services.skills.contracts import PackageCreate, revision_path
from services.skills.repository import SkillRepository
from services.skills.resolver import SkillResolutionContext, SkillResolver
from tests.test_scheduled_task_draft_delete_integration import postgres_socket  # noqa: F401
from tests.test_skill_catalog import settings

MIGRATIONS = Path(__file__).parents[1] / 'migrations'


class Pool:
    def __init__(self, socket, database): self.socket, self.database = socket, database
    @contextmanager
    def connection(self, *, privileged=False):
        with psycopg.connect(host=self.socket, dbname=self.database, user=getpass.getuser()) as conn:
            if not privileged: conn.execute('SET ROLE everydayai')
            yield conn


@pytest.fixture
def environment(postgres_socket, tmp_path):
    name = 'skill_personal_' + uuid4().hex
    with psycopg.connect(host=postgres_socket, dbname='postgres', user=getpass.getuser(), autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'everydayai'").fetchone():
            conn.execute('CREATE ROLE everydayai')
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    pool = Pool(postgres_socket, name)
    org, actor, other, super_admin = (uuid4() for _ in range(4))
    conversation = uuid4()
    with pool.connection(privileged=True) as conn:
        conn.execute('CREATE TABLE organizations(id UUID PRIMARY KEY, status TEXT NOT NULL DEFAULT \'active\', features JSONB NOT NULL DEFAULT \'{}\')')
        conn.execute('CREATE TABLE users(id UUID PRIMARY KEY, status TEXT NOT NULL, role TEXT NOT NULL)')
        conn.execute('''CREATE TABLE org_members(org_id UUID REFERENCES organizations(id), user_id UUID REFERENCES users(id),
            status TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY(org_id,user_id))''')
        conn.execute('''CREATE TABLE conversations(id UUID PRIMARY KEY, user_id UUID REFERENCES users(id),
            org_id UUID REFERENCES organizations(id), scope_type TEXT NOT NULL, scope_id TEXT NOT NULL)''')
        conn.execute('GRANT SELECT, UPDATE ON organizations, users, org_members, conversations TO everydayai')
        conn.execute('INSERT INTO organizations(id) VALUES (%s)', (org,))
        conn.execute('INSERT INTO users VALUES (%s,\'active\',\'user\'),(%s,\'active\',\'user\'),(%s,\'active\',\'super_admin\')',
                     (actor, other, super_admin))
        conn.execute('INSERT INTO org_members VALUES (%s,%s,\'active\',\'member\')', (org, actor))
        conn.execute('INSERT INTO org_members VALUES (%s,%s,\'active\',\'admin\')', (org, other))
        conn.execute('INSERT INTO conversations VALUES (%s,%s,%s,\'user\',%s)',
                     (conversation, actor, org, str(actor)))
        for migration in ('256_skill_catalog.sql', '257_skill_catalog_metadata.sql', '259_skill_authoring.sql',
                          '260_skill_reenable.sql', '261_skill_safe_removal.sql', '263_conversation_skill_bindings.sql',
                          '265_skill_recommendations.sql', '267_skill_personal_ownership.sql',
                          '268_skill_chat_edit.sql'):
            conn.execute((MIGRATIONS / migration).read_text())
    root = tmp_path / 'nas'; root.mkdir()
    config = settings(skill_catalog_enabled=True, skill_chat_creation_enabled=True,
        skill_storage_root=str(root), file_workspace_root=str(tmp_path / 'workspace'))

    def service(user=actor, org_id=None, owner_scope='personal'):
        scope = DatabaseScope(str(user), str(org_id) if org_id else None,
            DatabaseAccessKind.RUNTIME_ADMIN, request_id='personal-test')
        return SkillAuthoring(SkillRepository(pool, scope, owner_scope=owner_scope), config)

    yield SimpleNamespace(pool=pool, org=org, actor=actor, other=other, super_admin=super_admin,
        conversation=conversation, service=service, config=config)
    with psycopg.connect(host=postgres_socket, dbname='postgres', user=getpass.getuser(), autocommit=True) as conn:
        conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def proposal(environment, *, actor=None, scope_org='conversation', name='个人步骤'):
    actor = actor or environment.actor
    if scope_org == 'conversation': scope_org = environment.org
    content = ChatSkillCandidate(name=name, description='用户确认的方法说明。', body='按用户确认的步骤执行。').draft_content()
    proposal_id = uuid4()
    with environment.pool.connection(privileged=True) as conn:
        conn.execute('''INSERT INTO skill_chat_proposals
            (id,actor_user_id,conversation_id,org_id,idempotency_key,skill_key,content,content_sha256)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
            (proposal_id, actor, environment.conversation, scope_org, 'test:' + str(proposal_id),
             'chat-' + proposal_id.hex[:12], Jsonb(content.model_dump(mode='json')), content_digest(content)))
    return proposal_id, content


def test_personal_skill_is_private_and_resolvable_only_by_its_owner(environment):
    authoring = environment.service()
    package_id = authoring.create(CreateSkill(skill_key='private-process', content=DraftContent(
        description='本人流程', body='只为本人复用。')))['package_id']
    detail = authoring.transition(package_id, TransitionDraft(expected_version=1, action='publish_private'))
    assert detail['scope_kind'] == 'personal' and detail['draft']['status'] == 'published'
    assert detail['revisions'][0]['revision'] == detail['available_revision']

    owner_candidates = authoring.repository.catalog_candidates()
    context = SkillResolutionContext(actor_user_id=environment.actor, org_id=None,
        conversation_scope='user', agent_domain='general', execution_mode='interactive',
        enabled_feature_flags=frozenset({'skill_catalog_enabled'}))
    assert [item.skill_key for item in SkillResolver().select(context, owner_candidates)] == ['private-process']
    other = environment.service(user=environment.other).repository.catalog_candidates()
    assert other == []
    with pytest.raises(Exception):
        environment.service(user=environment.other).detail(package_id)

    repository = SkillBindingRepository(environment.pool, DatabaseScope(
        str(environment.actor), str(environment.org), DatabaseAccessKind.RUNTIME_ADMIN,
        request_id='personal-binding-test'))
    candidate = next(c for c in repository.catalog_candidates() if c.skill_key == 'private-process')
    binding = repository.add_binding(environment.conversation, candidate)
    row = repository.bindings(environment.conversation)[0]
    assert row['id'] == binding and row['available'] is True
    repository.remove_binding(environment.conversation, binding)
    assert repository.bindings(environment.conversation) == []
    with environment.pool.connection(privileged=True) as conn:
        conn.execute('UPDATE conversations SET org_id = NULL WHERE id = %s', (environment.conversation,))
    standalone = SkillBindingRepository(environment.pool, DatabaseScope(
        str(environment.actor), None, DatabaseAccessKind.RUNTIME_ADMIN,
        request_id='standalone-personal-binding-test'))
    personal = next(c for c in standalone.catalog_candidates() if c.skill_key == 'private-process')
    standalone_binding = standalone.add_binding(environment.conversation, personal)
    assert standalone.bindings(environment.conversation)[0]['id'] == standalone_binding
    standalone.remove_binding(environment.conversation, standalone_binding)


def test_chat_personal_confirmation_publishes_only_after_hash_and_owner_checks(environment):
    proposal_id, content = proposal(environment)
    result = environment.service(org_id=environment.org).commit_chat_proposal(proposal_id=proposal_id, expected_version=1,
        content_sha256=content_digest(content), target_scope='personal')
    assert result['status'] == 'committed' and result['result']['status'] == 'published'
    with environment.pool.connection(privileged=True) as conn:
        package_row = conn.execute('''SELECT skill_key, owner_user_id FROM skill_packages WHERE id = %s''',
                                   (result['package_id'],)).fetchone()
        revision = conn.execute('''SELECT revision FROM skill_revisions
            WHERE package_id = %s AND status = 'published' ''', (result['package_id'],)).fetchone()['revision']
    package = PackageCreate(skill_key=package_row['skill_key'], source='chat', scope_kind='personal',
                            owner_user_id=package_row['owner_user_id'])
    stored = Path(environment.config.skill_storage_root) / revision_path(package, revision)
    assert stored.is_file() and stored.read_text().endswith(content.body)
    replay = environment.service(org_id=environment.org).commit_chat_proposal(proposal_id=proposal_id, expected_version=1,
        content_sha256=content_digest(content), target_scope='personal')
    assert replay['replayed'] is True and replay['package_id'] == result['package_id']

    stale_id, stale_content = proposal(environment, name='过期正文')
    with pytest.raises(ValueError, match='SKILL_PROPOSAL_STALE'):
        environment.service(org_id=environment.org).commit_chat_proposal(proposal_id=stale_id, expected_version=2,
            content_sha256=content_digest(stale_content), target_scope='personal')


def test_chat_edit_confirmation_publishes_new_revision_of_owned_personal_skill(environment):
    service = environment.service()
    package_id = service.create(CreateSkill(skill_key='chat-edit-target', content=DraftContent(
        description='旧用途', body='旧规则。', catalog_metadata={
            'name': '聊天编辑目标', 'triggers': ['原触发词'], 'recommended_file_types': ['pdf'],
        }, template_variables={'locale': {'type': 'string', 'source': 'execution_mode'}},
    )))['package_id']
    published = service.transition(package_id, TransitionDraft(expected_version=1, action='publish_private'))
    old_revision = published['revisions'][0]['revision']

    edited = ChatSkillCandidate(
        name='聊天编辑目标', description='新用途', body='保留原流程并补充新规则。',
        triggers=['新触发词'], target_package_id=package_id,
        expected_target_revision=old_revision, expected_target_draft_version=published['draft']['version'],
    ).draft_content()
    proposal_id = uuid4()
    with environment.pool.connection(privileged=True) as conn:
        conn.execute('''INSERT INTO skill_chat_proposals
            (id, actor_user_id, conversation_id, org_id, idempotency_key, skill_key, content,
             content_sha256, operation, target_scope, target_package_id, target_revision, target_draft_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'update', 'personal', %s, %s, %s)''',
            (proposal_id, environment.actor, environment.conversation, environment.org,
             'chat-edit:' + str(proposal_id), 'chat-edit-target',
             Jsonb(edited.model_dump(mode='json')), content_digest(edited), package_id,
             old_revision, published['draft']['version']))

    result = environment.service(org_id=environment.org).commit_chat_proposal(
        proposal_id=proposal_id, expected_version=1, content_sha256=content_digest(edited),
        target_scope='personal',
    )

    assert result['status'] == 'committed' and result['operation'] == 'update'
    assert result['package_id'] == str(package_id)
    detail = service.detail(package_id)
    assert detail['scope_kind'] == 'personal'
    assert len(detail['revisions']) == 2
    assert {revision['revision'] for revision in detail['revisions']} >= {old_revision}
    assert detail['available_revision'] != old_revision
    updated = service.read_revision(package_id, detail['available_revision'])
    assert updated.body == '保留原流程并补充新规则。'
    assert updated.catalog_metadata.triggers == ('新触发词',)
    assert updated.catalog_metadata.recommended_file_types == ('pdf',)
    assert updated.template_variables == {'locale': {'type': 'string', 'source': 'execution_mode'}}


def test_organization_can_close_ai_creation_and_publication_scopes(environment):
    with environment.pool.connection(privileged=True) as conn:
        conn.execute("UPDATE organizations SET features = %s WHERE id = %s",
                     (Jsonb({'skill_chat_creation_enabled': False}), environment.org))
    proposal_id, content = proposal(environment, scope_org=environment.org)
    with pytest.raises(ValueError, match='SKILL_CHAT_CREATION_DISABLED'):
        environment.service(org_id=environment.org).commit_chat_proposal(
            proposal_id=proposal_id, expected_version=1,
            content_sha256=content_digest(content), target_scope='personal')


def test_org_admin_binding_access_does_not_expose_another_users_personal_skill(environment):
    admin = environment.service(user=environment.other, org_id=environment.org, owner_scope='org')
    package_id = admin.create(CreateSkill(skill_key='org-method', content=DraftContent(
        description='组织流程', body='按组织审核内容执行。')))['package_id']
    admin.transition(package_id, TransitionDraft(expected_version=1, action='submit'))
    admin.transition(package_id, TransitionDraft(expected_version=2, action='approve'))
    admin.transition(package_id, TransitionDraft(expected_version=3, action='publish'))
    repository = SkillBindingRepository(environment.pool, DatabaseScope(
        str(environment.other), str(environment.org), DatabaseAccessKind.RUNTIME_ADMIN,
        request_id='org-admin-binding-test'))
    candidate = next(c for c in repository.catalog_candidates() if c.skill_key == 'org-method')
    binding = repository.add_binding(environment.conversation, candidate)
    assert repository.bindings(environment.conversation)[0]['id'] == binding
    repository.remove_binding(environment.conversation, binding)

    personal = environment.service().create(CreateSkill(skill_key='another-private', content=DraftContent(
        description='私人流程', body='仅创建者使用。')))['package_id']
    environment.service().transition(personal, TransitionDraft(expected_version=1, action='publish_private'))
    with pytest.raises(Exception):
        environment.service(user=environment.other).detail(personal)


def test_org_confirmation_creates_review_request_and_platform_confirmation_waits_for_super_admin(environment):
    org_proposal, org_content = proposal(environment, scope_org=environment.org, name='组织流程')
    org_result = environment.service(org_id=environment.org, owner_scope='org').commit_chat_proposal(
        proposal_id=org_proposal, expected_version=1, content_sha256=content_digest(org_content), target_scope='org')
    assert org_result['status'] == 'committed' and org_result['result']['status'] == 'in_review'

    platform_proposal, platform_content = proposal(environment, scope_org=environment.org, name='平台流程')
    request_result = environment.service(org_id=environment.org).commit_chat_proposal(
        proposal_id=platform_proposal, expected_version=1,
        content_sha256=content_digest(platform_content), target_scope='platform')
    assert request_result['status'] == 'awaiting_review'
    admin = environment.service(user=environment.super_admin, owner_scope='platform')
    assert len(admin.list_platform_chat_proposals()) == 1
    published = admin.decide_platform_chat_proposal(platform_proposal, approve=True)
    assert published['status'] == 'committed' and published['result']['status'] == 'published'
