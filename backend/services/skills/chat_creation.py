"""Explicit chat proposals for private, organization, or platform Skills."""

from __future__ import annotations

import hashlib
import json
import re
from types import SimpleNamespace
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field
from psycopg.types.json import Jsonb

from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient
from services.agent.agent_result import AgentResult
from services.changeset.repository import ChangeSetRepository
from services.changeset.service import ChangeSetService
from services.skills.authoring import SkillAuthoring
from services.skills.authoring_contracts import DraftContent, new_draft_content
from services.skills.creation_policy import for_organization
from services.skills.repository import SkillRepository
from services.tools.dispatcher import current_dispatch_call_id


class ChatSkillCandidate(DraftContent):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    body: str = Field(min_length=1, max_length=50_000)
    task_modes: tuple[Literal['smart', 'image-i2i', 'image-t2i', 'image-ecom', 'video'], ...] = Field(
        default=('smart',), min_length=1, max_length=5,
    )
    triggers: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = Field(default=(), max_length=12)
    source_message_ids: tuple[UUID, ...] = Field(default=(), max_length=40)
    input_requirements: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = Field(default=(), max_length=12)
    open_questions: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = Field(default=(), max_length=16)
    target_skill_name: str | None = Field(default=None, min_length=1, max_length=200)
    supersedes_change_set_id: UUID | None = None

    def draft_content(self) -> DraftContent:
        if not self.description.strip():
            raise ValueError('SKILL_CANDIDATE_DESCRIPTION_REQUIRED')
        if not self.body.strip():
            raise ValueError('SKILL_CANDIDATE_BODY_REQUIRED')
        metadata = self.catalog_metadata.model_copy(update={
            'name': self.name,
            'triggers': self.triggers,
            'task_modes': self.task_modes,
            'model_selectable': False,
            'execution_modes': ('interactive',),
            'agent_domains': ('general',),
            'conversation_scopes': ('user',),
            'tool_policy': 'platform',
            'allowed_tool_names': (),
            'required_permissions': (),
            'required_feature_flags': (),
            'actor_user_ids': (),
            'recommended_file_types': (),
        })
        return new_draft_content(DraftContent(
            description=self.description,
            body=self.body,
            catalog_metadata=metadata,
            assets=(),
            template_variables={},
        ))


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def content_digest(content: DraftContent) -> str:
    return hashlib.sha256(_canonical(content.model_dump(mode='json'))).hexdigest()


def _org_admin(db, actor_id: str, org_id: str) -> None:
    org = db.table('organizations').select('status').eq('id', org_id).maybe_single().execute()
    member = db.table('org_members').select('status,role').eq('org_id', org_id).eq(
        'user_id', actor_id,
    ).maybe_single().execute()
    actor = db.table('users').select('status').eq('id', actor_id).maybe_single().execute()
    if (not org or not org.data or org.data.get('status') != 'active'
            or not member or not member.data or member.data.get('status') != 'active'
            or member.data.get('role') not in ('owner', 'admin')
            or not actor or not actor.data or actor.data.get('status') != 'active'):
        raise PermissionError('SKILL_ORG_ADMIN_REQUIRED')


def _new_key() -> str:
    return 'chat-skill-' + uuid4().hex[:12]


def _source_message_refs(db, *, actor_id: str, org_id: str | None, conversation_id: str,
                         requested_message_ids: tuple[UUID, ...] = ()) -> list[dict[str, str]]:
    conversation = db.table('conversations').select(
        'id,user_id,org_id,scope_type',
    ).eq('id', conversation_id).maybe_single().execute()
    row = conversation.data if conversation else None
    if (not isinstance(row, dict) or str(row.get('user_id')) != actor_id
            or str(row.get('org_id') or '') != str(org_id or '')
            or row.get('scope_type', 'user') != 'user'):
        raise PermissionError('SKILL_ORG_CONVERSATION_REQUIRED')
    query = db.table('messages').select('id,role,content,turn_id,reply_to_message_id').eq(
        'conversation_id', conversation_id,
    )
    if requested_message_ids:
        if len(set(requested_message_ids)) != len(requested_message_ids):
            raise ValueError('SKILL_SOURCE_MESSAGE_IDS_INVALID')
        requested = query.in_('id', [str(value) for value in requested_message_ids]).execute()
        requested_rows = list(requested.data or [])
        by_id = {str(item.get('id')): item for item in requested_rows}
        expected = {str(value) for value in requested_message_ids}
        if set(by_id) != expected or any(item.get('role') != 'assistant' for item in by_id.values()):
            raise PermissionError('SKILL_SOURCE_MESSAGE_UNAVAILABLE')
        turn_ids = sorted({str(item['turn_id']) for item in by_id.values() if item.get('turn_id')})
        related_ids = sorted({
            str(item['reply_to_message_id']) for item in by_id.values()
            if item.get('reply_to_message_id')
        })
        related_rows = []
        if turn_ids:
            related_rows.extend(db.table('messages').select(
                'id,role,content,turn_id,reply_to_message_id',
            ).eq('conversation_id', conversation_id).in_(
                'role', ['user', 'assistant'],
            ).in_('turn_id', turn_ids).execute().data or [])
        if related_ids:
            related_rows.extend(db.table('messages').select(
                'id,role,content,turn_id,reply_to_message_id',
            ).eq('conversation_id', conversation_id).in_(
                'role', ['user', 'assistant'],
            ).in_('id', related_ids).execute().data or [])
        deduped = {str(item.get('id')): item for item in [*requested_rows, *related_rows]}
        messages = SimpleNamespace(data=list(deduped.values()))
    else:
        messages = query.in_('role', ['user', 'assistant']).order(
            'created_at', desc=True,
        ).limit(40).execute()
    refs = []
    for message in reversed(messages.data or []):
        content = message.get('content')
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                content = content[:100_000]
        refs.append({
            'message_id': str(message.get('id') or ''),
            'role': str(message.get('role') or ''),
            'content_sha256': hashlib.sha256(_canonical(content).replace(b'\\/', b'/')).hexdigest(),
        })
    return refs


def replace_candidate(db, settings, *, actor_id: str, org_id: str,
                      change_set: dict, arguments: dict, expected_revision: int):
    _org_admin(db, actor_id, org_id)
    if (change_set.get('resource_type') != 'skill_draft'
            or str(change_set.get('created_by')) != actor_id
            or change_set.get('status') != 'awaiting_approval'):
        raise PermissionError('SKILL_PROPOSAL_UNAVAILABLE')
    candidate = ChatSkillCandidate.model_validate(arguments)
    content = candidate.draft_content()
    digest = content_digest(content)
    proposal = {
        'skill_key': (change_set.get('proposed_snapshot') or {}).get('skill_key'),
        'target_skill': (change_set.get('proposed_snapshot') or {}).get('target_skill'),
        'name': candidate.name,
        'description': content.description,
        'task_modes': list(content.catalog_metadata.task_modes),
        'triggers': list(content.catalog_metadata.triggers),
        'input_requirements': list(candidate.input_requirements),
        'open_questions': list(candidate.open_questions),
        'content': content.model_dump(mode='json'),
        'content_sha256': digest,
    }
    scoped_db = ScopedDatabaseClient(db, DatabaseScope(
        actor_user_id=actor_id, org_id=org_id,
        access_kind=DatabaseAccessKind.RUNTIME_ADMIN, request_id=str(uuid4()),
    ))
    repository = ChangeSetRepository(scoped_db)
    if settings is None:
        from core.config import get_settings
        settings = get_settings()
    return repository.replace_skill_proposal(
        change_set_id=str(change_set['id']), org_id=org_id, actor_id=actor_id,
        expected_revision=expected_revision, proposal=proposal, content_sha256=digest,
    )


def create_proposal(db, settings, *, actor_id: str, org_id: str | None,
                    conversation_id: str | None, arguments: dict) -> AgentResult:
    """Create a private preview candidate; never saves or publishes a Skill."""
    if (settings.skill_catalog_enabled is not True
            or getattr(settings, 'skill_chat_creation_enabled', False) is not True):
        raise PermissionError('SKILL_CHAT_CREATION_DISABLED')
    if not conversation_id:
        raise PermissionError('SKILL_CONVERSATION_REQUIRED')
    actor = db.table('users').select('status').eq('id', actor_id).maybe_single().execute()
    if not actor or not actor.data or actor.data.get('status') != 'active':
        raise PermissionError('SKILL_ACTOR_UNAVAILABLE')
    policy = for_organization(db, org_id)
    if not policy.chat_creation_enabled:
        raise PermissionError('SKILL_CHAT_CREATION_DISABLED')
    if org_id:
        member = db.table('org_members').select('status').eq('org_id', org_id).eq(
            'user_id', actor_id,
        ).maybe_single().execute()
        if not member or not member.data or member.data.get('status') != 'active':
            raise PermissionError('SKILL_ORG_MEMBERSHIP_REQUIRED')
    candidate = ChatSkillCandidate.model_validate(arguments)
    source_message_refs = _source_message_refs(
        db, actor_id=actor_id, org_id=org_id, conversation_id=conversation_id,
        requested_message_ids=candidate.source_message_ids,
    )
    content = candidate.draft_content()
    digest = content_digest(content)
    if not candidate.target_skill_name:
        call_id = current_dispatch_call_id() or str(uuid4())
        idempotency_key = f'skill-authoring:{conversation_id}:{call_id}'
        repository = SkillRepository(db.pool, DatabaseScope(
            actor_user_id=actor_id, org_id=org_id,
            access_kind=DatabaseAccessKind.RUNTIME_ADMIN, request_id=str(uuid4()),
        ), owner_scope='personal')
        with repository._cursor() as cursor:
            cursor.execute('''INSERT INTO public.skill_chat_proposals
                (actor_user_id, conversation_id, org_id, idempotency_key, skill_key, content,
                 content_sha256, source_message_refs, source_scope)
                VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (actor_user_id, conversation_id, idempotency_key) DO NOTHING
                RETURNING id, version, status''',
                (actor_id, conversation_id, org_id, idempotency_key, _new_key(),
                 Jsonb(content.model_dump(mode='json')), digest, Jsonb(source_message_refs),
                 'selected_assistant_turn' if candidate.source_message_ids else 'recent_40_messages'))
            proposal = cursor.fetchone()
            if not proposal:
                cursor.execute('''SELECT id, version, status, content_sha256 FROM public.skill_chat_proposals
                    WHERE actor_user_id = %s::uuid AND conversation_id = %s::uuid AND idempotency_key = %s''',
                    (actor_id, conversation_id, idempotency_key))
                proposal = cursor.fetchone()
                if not proposal or proposal['content_sha256'] != digest:
                    raise ValueError('SKILL_PROPOSAL_IDEMPOTENCY_CONFLICT')
        return AgentResult(
            summary='Skill 候选已准备好。请核对正文并选择保存范围；确认前不会创建或发布 Skill。',
            status='success', metadata={'skill_chat_proposal': {
                'id': str(proposal['id']), 'name': candidate.name,
            }},
        )
    if not org_id:
        raise PermissionError('SKILL_ORG_CONVERSATION_REQUIRED')
    _org_admin(db, actor_id, org_id)
    operation = 'update' if candidate.target_skill_name else 'create'
    target_id = None
    expected_version = None
    target_skill = None
    if operation == 'create':
        package_id = str(uuid4())
        skill_key = _new_key()
        base_revision = '0'
    else:
        repo = SkillRepository(db.pool, DatabaseScope(
            actor_user_id=actor_id, org_id=org_id, access_kind=DatabaseAccessKind.RUNTIME_ADMIN,
            request_id=str(uuid4()),
        ))
        authoring = SkillAuthoring(repo, settings)
        target_name = candidate.target_skill_name.strip()
        matches = [item for item in authoring.list()
                   if item.get('name') == target_name
                   and item.get('scope_kind') == 'org'
                   and item.get('status') == 'draft']
        if len(matches) != 1:
            raise ValueError('SKILL_TARGET_AMBIGUOUS' if matches else 'SKILL_DRAFT_UPDATE_REQUIRED')
        target = matches[0]
        target_id = UUID(str(target['package_id']))
        expected_version = target.get('version')
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError('SKILL_EXPECTED_VERSION_REQUIRED')
        package_id = str(target_id)
        detail = authoring.detail(UUID(package_id))
        if not detail.get('editable') or not detail.get('draft'):
            raise ValueError('SKILL_DRAFT_UPDATE_REQUIRED')
        if detail['draft']['version'] != expected_version or detail['draft']['status'] != 'draft':
            raise ValueError('SKILL_VERSION_CONFLICT')
        skill_key = detail['skill_key']
        base_revision = str(expected_version)
        saved_content = detail['draft'].get('content') or {}
        saved_metadata = saved_content.get('catalog_metadata') or {}
        target_skill = {
            'package_id': package_id,
            'skill_key': skill_key,
            'name': saved_metadata.get('name') or target.get('name') or skill_key,
            'expected_version': expected_version,
        }

    proposal = {
        'skill_key': skill_key,
        'name': candidate.name,
        'description': content.description,
        'task_modes': list(content.catalog_metadata.task_modes),
        'triggers': list(content.catalog_metadata.triggers),
        'target_skill': target_skill,
        'input_requirements': list(candidate.input_requirements),
        'open_questions': list(candidate.open_questions),
        'content': content.model_dump(mode='json'),
        'content_sha256': digest,
    }
    call_id = current_dispatch_call_id() or str(uuid4())
    payload = {
        'id': str(uuid4()), 'org_id': org_id, 'resource_type': 'skill_draft',
        'resource_id': package_id, 'operation': operation, 'base_revision': base_revision,
        'base_snapshot': {}, 'proposed_snapshot': proposal, 'patch': [],
        'diff': {'items': [{'label': 'Skill 内容', 'value': candidate.name}]},
        'risk_level': 'low', 'policy_snapshot': {
            'requires_approval': True, 'submission': {'mode': 'explicit_skill_draft_confirmation'},
        },
        'plan_snapshot': None, 'tool_policy_snapshot': None,
        'check_summary': {'validation': 'passed', 'execution': 'not_run'},
        'idempotency_key': f'skill-authoring:{actor_id}:{conversation_id}:{call_id}',
        'actor_id': actor_id, 'actor_type': 'user',
        'audit_subject': {'conversation_id': conversation_id,
                          'source_message_refs': source_message_refs,
                          'source_scope': ('selected_assistant_turn' if candidate.source_message_ids
                                           else 'recent_40_messages'),
                          'candidate_sha256': digest,
                          'candidate_operation': operation,
                          'target_package_id': package_id if operation == 'update' else None,
                          'target_version': expected_version if operation == 'update' else None,
                          'source_kind': 'chat_model_candidate'},
    }
    repository = ChangeSetRepository(db)
    existing = repository.get_by_idempotency_key(org_id=org_id, idempotency_key=payload['idempotency_key'])
    if existing:
        existing_audit = existing.get('audit_subject') or {}
        if (existing.get('resource_type') != 'skill_draft'
                or str(existing.get('created_by')) != actor_id
                or str(existing.get('org_id')) != org_id
                or existing_audit.get('conversation_id') != conversation_id
                or existing_audit.get('candidate_sha256') != digest
                or existing_audit.get('candidate_operation') != operation
                or existing_audit.get('target_package_id') != (package_id if operation == 'update' else None)
                or existing_audit.get('target_version') != (expected_version if operation == 'update' else None)):
            raise ValueError('SKILL_PROPOSAL_IDEMPOTENCY_CONFLICT')
        return AgentResult(
            summary='Skill 候选已准备好，请在卡片中核对后确认创建草稿。',
            status='success', metadata={'change_set': existing},
        )
    else:
        previous = None
        if candidate.supersedes_change_set_id:
            try:
                previous = repository.get(str(candidate.supersedes_change_set_id), org_id)
            except Exception:
                raise PermissionError('SKILL_PROPOSAL_UNAVAILABLE') from None
            if (previous.get('resource_type') != 'skill_draft'
                    or str(previous.get('created_by')) != actor_id
                    or previous.get('status') != 'awaiting_approval'):
                raise PermissionError('SKILL_PROPOSAL_UNAVAILABLE')
        row = repository.create(payload)
        if previous:
            ChangeSetService(repository).cancel(
                change_set_id=str(previous['id']), org_id=org_id, actor_id=actor_id,
                reason='已生成新的 Skill 候选版本',
            )
    service = ChangeSetService(repository)
    status = row['status']
    steps = {
        'draft': ('resolving', 'proposed', 'validating', 'preflighting', 'awaiting_approval'),
        'resolving': ('proposed', 'validating', 'preflighting', 'awaiting_approval'),
        'proposed': ('validating', 'preflighting', 'awaiting_approval'),
        'validating': ('preflighting', 'awaiting_approval'),
        'preflighting': ('awaiting_approval',),
        'awaiting_approval': (),
    }
    for next_status in steps.get(status, ()):
        if status == 'draft': event = 'proposal_started'
        elif status == 'resolving': event = 'proposal_ready'
        elif status == 'proposed': event = 'candidate_validated'
        elif status == 'validating': event = 'non_execution_preflight'
        else: event = 'awaiting_user_confirmation'
        row = repository.transition(
            change_set_id=row['id'], org_id=org_id, expected_status=status,
            next_status=next_status, actor_id=actor_id, actor_type='user', event_type=event,
        )
        status = next_status
    if row['status'] == 'awaiting_approval' and not repository.list_checks(row['id'], org_id):
        repository.record_check(
            change_set_id=row['id'], org_id=org_id, check_type='validation',
            check_key='skill_candidate_contract', status='passed', input_data={'sha256': digest},
            result={'valid': True, 'execution': 'not_run'}, actor_id=actor_id, actor_type='user',
        )
        repository.record_check(
            change_set_id=row['id'], org_id=org_id, check_type='preflight',
            check_key='skill_candidate_no_side_effects', status='passed',
            input_data={'resource_type': 'skill_draft'},
            result={'business_write': False, 'publish': False, 'activation': False},
            actor_id=actor_id, actor_type='user',
        )
    return AgentResult(summary='Skill 候选已准备好，请在卡片中核对后确认创建草稿。',
                       status='success', metadata={'change_set': row})


def commit_draft(repository: SkillRepository, settings, *, change_set: dict,
                 actor_id: str, org_id: str):
    """Write the draft and its recovery receipt in the same transaction."""
    _org_admin_from_repository(repository)
    authoring = SkillAuthoring(repository, settings)
    proposal = change_set.get('proposed_snapshot') or {}
    content = DraftContent.model_validate(proposal.get('content') or {})
    digest = content_digest(content)
    if digest != proposal.get('content_sha256') or digest != change_set.get('audit_subject', {}).get('candidate_sha256'):
        raise ValueError('SKILL_CANDIDATE_HASH_MISMATCH')
    package_id = UUID(str(change_set['resource_id']))
    return authoring.commit_chat_draft(
        change_set_id=change_set['id'], package_id=package_id,
        skill_key=proposal['skill_key'], content=content,
        operation=change_set['operation'], expected_version=int(change_set['base_revision']),
        content_sha256=digest,
    )


def _org_admin_from_repository(repository: SkillRepository) -> None:
    repository._require_admin()
    repository._require_org()
    if not repository.scope.actor_user_id:
        raise PermissionError('SKILL_ORG_ADMIN_REQUIRED')
