"""Edits to an uncommitted Skill proposal; all writes remain candidates only."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import CurrentUser, CurrentUserId, Database, OrgCtx, ScopedDB
from core.config import get_settings
from core.db_scope import DatabaseAccessKind, DatabaseScope
from core.limiter import RATE_LIMITS, limiter
from services.changeset.repository import ChangeSetConcurrencyError, ChangeSetRepository
from services.skills.chat_creation import replace_candidate, _org_admin
from services.skills.authoring import SkillAuthoring
from services.skills.creation_policy import for_organization
from services.skills.repository import SkillRepository
from services.handlers.chat_image_request import chat_image_acceptance_allowed
from services.skills.trials import (
    SkillTrialRepository,
    TrialConflict,
    TrialInProgress,
    candidate_is_expired,
    estimate_image_trial,
    list_trial_runs,
    list_conversation_images,
    run_trial,
    save_trial_feedback,
    validate_reference_images,
)


router = APIRouter(prefix='/skills/authoring/proposals', tags=['Skill 对话创建'])


def _require_chat_creation_enabled():
    settings = get_settings()
    if (settings.skill_catalog_enabled is not True
            or settings.skill_chat_creation_enabled is not True):
        raise HTTPException(404, 'Skill 对话创建尚未启用')
    return settings


class CandidateRevision(BaseModel):
    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=2000)
    body: str = Field(min_length=1, max_length=50000)
    task_modes: tuple[str, ...] = Field(default=('smart',), min_length=1, max_length=5)
    triggers: tuple[str, ...] = Field(default=(), max_length=12)
    input_requirements: tuple[str, ...] = Field(default=(), max_length=12)
    open_questions: tuple[str, ...] = Field(default=(), max_length=16)


class ConfirmChatProposal(BaseModel):
    expected_version: int = Field(ge=1)
    content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    target_scope: Literal['personal', 'org', 'platform']


class ChatProposalFeedback(BaseModel):
    rating: Literal['helpful', 'not_helpful']
    feedback_text: str = Field(default='', max_length=1000)


def _chat_proposal_authoring(db, actor_id: str, org_id: str | None = None, owner_scope='personal'):
    scope = DatabaseScope(actor_user_id=actor_id, org_id=org_id,
        access_kind=DatabaseAccessKind.RUNTIME_ADMIN, request_id='skill-chat-proposal')
    return SkillAuthoring(SkillRepository(db.pool, scope, owner_scope=owner_scope), get_settings())


@router.get('/chat/{proposal_id}')
def get_chat_proposal(proposal_id: UUID, actor_id: CurrentUserId, user: CurrentUser, db: Database):
    _require_chat_creation_enabled()
    if user.get('status') != 'active':
        raise HTTPException(403, '当前账号不可访问 Skill 候选')
    authoring = _chat_proposal_authoring(db, str(actor_id))
    repo = authoring.repository
    with repo._cursor() as cursor:
        cursor.execute('''SELECT id, conversation_id, org_id, skill_key, content, content_sha256,
            version, status, operation, target_package_id, target_revision, target_scope,
            target_org_id, result, source_message_refs, source_scope,
            feedback_rating, feedback_text, feedback_at, scope_confirmed_by, scope_selected_at,
            decision_by, decision_at, decision_reason, expires_at, created_at
            FROM public.skill_chat_proposals WHERE id = %s::uuid AND actor_user_id = %s::uuid''',
            (proposal_id, actor_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, 'Skill 候选不存在')
        can_request_org = False
        if row['org_id']:
            cursor.execute('''SELECT 1 FROM public.organizations o JOIN public.org_members m ON m.org_id = o.id
                WHERE o.id = %s::uuid AND o.status = 'active' AND m.user_id = %s::uuid AND m.status = 'active' ''',
                (row['org_id'], actor_id))
            can_request_org = bool(cursor.fetchone())
    target_policy = for_organization(db, str(row['org_id']) if row['org_id'] else None)
    return {'success': True, 'data': {
        **{key: str(row[key]) if key in ('id', 'conversation_id', 'org_id', 'target_org_id', 'target_package_id') and row[key] else row[key]
           for key in ('id', 'conversation_id', 'org_id', 'skill_key', 'content', 'content_sha256', 'version',
                       'status', 'operation', 'target_package_id', 'target_revision', 'target_scope',
                       'target_org_id', 'result', 'source_message_refs',
                       'source_scope', 'feedback_rating', 'feedback_text', 'feedback_at',
                       'scope_confirmed_by', 'scope_selected_at', 'decision_by', 'decision_at',
                       'decision_reason', 'expires_at', 'created_at')},
        'available_targets': ({'personal': True, 'org': False, 'platform': False}
                              if row.get('operation') == 'update'
                              else target_policy.as_targets(has_org_membership=can_request_org)),
    }}


@router.post('/chat/{proposal_id}/confirm')
def confirm_chat_proposal(proposal_id: UUID, data: ConfirmChatProposal,
                          actor_id: CurrentUserId, user: CurrentUser, db: Database):
    _require_chat_creation_enabled()
    if user.get('status') != 'active':
        raise HTTPException(403, '当前账号不可创建 Skill')
    scope = DatabaseScope(actor_user_id=str(actor_id), org_id=None,
        access_kind=DatabaseAccessKind.RUNTIME_ADMIN, request_id='skill-chat-confirm')
    lookup = SkillRepository(db.pool, scope, owner_scope='personal')
    with lookup._cursor() as cursor:
        cursor.execute('''SELECT org_id FROM public.skill_chat_proposals
            WHERE id = %s::uuid AND actor_user_id = %s::uuid''', (proposal_id, actor_id))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(404, 'Skill 候选不存在')
    owner_scope = 'org' if data.target_scope == 'org' else 'personal'
    conversation_org_id = str(row['org_id']) if row['org_id'] else None
    authoring = _chat_proposal_authoring(db, str(actor_id), conversation_org_id, owner_scope)
    try:
        result = authoring.commit_chat_proposal(proposal_id=proposal_id,
            expected_version=data.expected_version, content_sha256=data.content_sha256,
            target_scope=data.target_scope)
    except Exception as error:
        code = str(error)
        if code in ('SKILL_PROPOSAL_UNAVAILABLE', 'SKILL_CONVERSATION_UNAVAILABLE'):
            raise HTTPException(404, 'Skill 候选或会话不可用') from None
        if code in ('SKILL_PROPOSAL_STALE', 'SKILL_PROPOSAL_SCOPE_CONFLICT',
                    'SKILL_EDIT_DRAFT_CONFLICT', 'SKILL_KEY_EXISTS'):
            raise HTTPException(409, code) from None
        if code in ('SKILL_ORG_MEMBERSHIP_REQUIRED', 'SKILL_ACTOR_UNAVAILABLE',
                    'SKILL_CHAT_CREATION_DISABLED', 'SKILL_ORG_SUBMISSION_DISABLED',
                    'SKILL_PLATFORM_SUBMISSION_DISABLED'):
            raise HTTPException(403, code) from None
        if code.startswith('SKILL_'):
            raise HTTPException(422, code) from None
        raise
    return {'success': True, 'data': result}


@router.post('/chat/{proposal_id}/cancel')
def cancel_chat_proposal(proposal_id: UUID, actor_id: CurrentUserId, user: CurrentUser, db: Database):
    _require_chat_creation_enabled()
    if user.get('status') != 'active':
        raise HTTPException(403, '当前账号不可访问 Skill 候选')
    repository = SkillRepository(db.pool, DatabaseScope(actor_user_id=str(actor_id), org_id=None,
        access_kind=DatabaseAccessKind.RUNTIME_ADMIN, request_id='skill-chat-cancel'), owner_scope='personal')
    with repository._cursor() as cursor:
        cursor.execute('''UPDATE public.skill_chat_proposals SET status = 'cancelled', updated_at = now()
            WHERE id = %s::uuid AND actor_user_id = %s::uuid AND status = 'awaiting_confirmation'
            RETURNING id''', (proposal_id, actor_id))
        cancelled = bool(cursor.fetchone())
    return {'success': True, 'data': {'proposal_id': str(proposal_id), 'cancelled': cancelled}}


@router.put('/chat/{proposal_id}/feedback')
def feedback_chat_proposal(proposal_id: UUID, data: ChatProposalFeedback,
                           actor_id: CurrentUserId, user: CurrentUser, db: Database):
    _require_chat_creation_enabled()
    if user.get('status') != 'active':
        raise HTTPException(403, '当前账号不可访问 Skill 候选')
    repository = SkillRepository(db.pool, DatabaseScope(actor_user_id=str(actor_id), org_id=None,
        access_kind=DatabaseAccessKind.RUNTIME_ADMIN, request_id='skill-chat-feedback'), owner_scope='personal')
    with repository._cursor() as cursor:
        cursor.execute('''UPDATE public.skill_chat_proposals SET feedback_rating = %s, feedback_text = %s,
            feedback_at = now(), updated_at = now() WHERE id = %s::uuid AND actor_user_id = %s::uuid
            RETURNING id''', (data.rating, data.feedback_text, proposal_id, actor_id))
        if not cursor.fetchone():
            raise HTTPException(404, 'Skill 候选不存在')
    return {'success': True, 'data': {'saved': True}}


@router.put('/{change_set_id}/revision')
def revise_candidate(
    change_set_id: UUID,
    data: CandidateRevision,
    actor_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
):
    _require_chat_creation_enabled()
    if not org_ctx.org_id:
        raise HTTPException(400, 'Skill 对话创建需要组织上下文')
    org_id = str(org_ctx.org_id)
    try:
        _org_admin(db, str(actor_id), org_id)
    except PermissionError:
        raise HTTPException(403, '仅组织管理员可编辑 Skill 候选') from None
    repository = ChangeSetRepository(scoped_db)
    try:
        row = repository.get(str(change_set_id), org_id)
    except Exception:
        raise HTTPException(404, 'Skill 候选不存在') from None
    if str(row.get('created_by')) != str(actor_id) or row.get('resource_type') != 'skill_draft':
        raise HTTPException(403, '无权编辑此 Skill 候选')
    if candidate_is_expired(row):
        raise HTTPException(409, 'Skill 候选已过期，请重新整理')
    arguments: dict[str, Any] = {
        'name': data.name, 'description': data.description, 'body': data.body,
        'task_modes': data.task_modes, 'triggers': data.triggers,
        'input_requirements': data.input_requirements,
        'open_questions': data.open_questions,
    }
    try:
        updated = replace_candidate(
            db, get_settings(), actor_id=str(actor_id), org_id=org_id,
            change_set=row, arguments=arguments, expected_revision=data.expected_revision,
        )
    except ChangeSetConcurrencyError:
        raise HTTPException(409, '候选已更新，请刷新后重新编辑') from None
    except PermissionError:
        raise HTTPException(403, '仅组织管理员可编辑 Skill 候选') from None
    except Exception as error:
        code = str(error)
        raise HTTPException(422, code if code.startswith('SKILL_') else 'SKILL_CANDIDATE_INVALID') from None
    return {'success': True, 'data': updated}


class TrialRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    mode: Literal['text', 'image']
    input_text: str = Field(min_length=1, max_length=8000)
    idempotency_key: UUID
    aspect_ratio: Literal['1:1', '3:2', '2:3', '4:3', '3:4', '16:9', '9:16'] = '1:1'
    reference_image_urls: list[str] = Field(default_factory=list, max_length=8)


class TrialFeedback(BaseModel):
    rating: Literal['helpful', 'not_helpful']
    feedback_text: str = Field(default='', max_length=1000)


def _get_trial_candidate(change_set_id: UUID, actor_id: str, org_id: str, scoped_db, *, allow_expired=False):
    repository = ChangeSetRepository(scoped_db)
    try:
        row = repository.get(str(change_set_id), org_id)
    except Exception:
        raise HTTPException(404, 'Skill 候选不存在') from None
    if str(row.get('created_by')) != actor_id or row.get('resource_type') != 'skill_draft':
        raise HTTPException(403, '无权试用此 Skill 候选')
    if not allow_expired and candidate_is_expired(row):
        raise HTTPException(409, 'Skill 候选已过期，不能继续试用')
    return row


@router.get('/{change_set_id}/trials')
def get_trials(
    change_set_id: UUID,
    actor_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
):
    settings = _require_chat_creation_enabled()
    if not org_ctx.org_id:
        raise HTTPException(400, 'Skill 试用记录需要组织上下文')
    actor, org_id = str(actor_id), str(org_ctx.org_id)
    try:
        _org_admin(db, actor, org_id)
    except PermissionError:
        raise HTTPException(403, '仅活跃组织管理员可读取 Skill 试用记录') from None
    _get_trial_candidate(change_set_id, actor, org_id, scoped_db, allow_expired=True)
    if settings.skill_draft_trial_enabled is not True:
        return {'success': True, 'data': [], 'trial_enabled': False}
    runs = list_trial_runs(
        db, org_id=org_id, change_set_id=str(change_set_id), actor_id=actor,
        trial_repository=SkillTrialRepository(db.pool, actor_id=actor, org_id=org_id),
    )
    return {'success': True, 'data': runs, 'trial_enabled': True}


def _require_trial_access(actor_id: str, org_id: str, db):
    settings = get_settings()
    if (settings.skill_catalog_enabled is not True
            or settings.skill_chat_creation_enabled is not True
            or settings.skill_draft_trial_enabled is not True):
        raise HTTPException(404, 'Skill 草稿试用尚未启用')
    try:
        _org_admin(db, actor_id, org_id)
    except PermissionError:
        raise HTTPException(403, '仅活跃组织管理员可试用 Skill 候选') from None
    return settings


@router.get('/{change_set_id}/trials/estimate')
def get_trial_estimate(
    change_set_id: UUID,
    actor_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
):
    if not org_ctx.org_id:
        raise HTTPException(400, 'Skill 草稿试用需要组织上下文')
    actor, org_id = str(actor_id), str(org_ctx.org_id)
    settings = _require_trial_access(actor, org_id, db)
    row = _get_trial_candidate(change_set_id, actor, org_id, scoped_db)
    conversation_id = str((row.get('audit_subject') or {}).get('conversation_id') or '')
    try:
        reference_images = list_conversation_images(
            db, conversation_id=conversation_id, actor_id=actor, org_id=org_id,
        )
        text_to_image = estimate_image_trial([])
        image_to_image = estimate_image_trial(['selected-reference'])
    except PermissionError:
        raise HTTPException(403, '当前会话或参考图已不可访问') from None
    except Exception:
        raise HTTPException(503, '暂时无法读取试用所需信息') from None
    return {'success': True, 'data': {
        'text_to_image': text_to_image,
        'image_to_image': image_to_image,
        'reference_images': reference_images,
        'trial_enabled': settings.skill_draft_trial_enabled is True,
        'image_trial_enabled': chat_image_acceptance_allowed(settings, actor),
    }}


@router.post('/{change_set_id}/trials')
@limiter.limit(RATE_LIMITS['message_stream'])
async def create_trial(
    request: Request,
    change_set_id: UUID,
    data: TrialRequest,
    actor_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
):
    if not org_ctx.org_id:
        raise HTTPException(400, 'Skill 草稿试用需要组织上下文')
    actor, org_id = str(actor_id), str(org_ctx.org_id)
    settings = _require_trial_access(actor, org_id, db)
    row = _get_trial_candidate(change_set_id, actor, org_id, scoped_db)
    conversation_id = str((row.get('audit_subject') or {}).get('conversation_id') or '')
    try:
        available = list_conversation_images(
            db, conversation_id=conversation_id, actor_id=actor, org_id=org_id,
        )
        references = validate_reference_images(data.reference_image_urls, available)
        result = await run_trial(
            db, settings, actor_id=actor, org_id=org_id, change_set=row,
            expected_revision=data.expected_revision, content_sha256=data.content_sha256,
            mode=data.mode, user_input=data.input_text, idempotency_key=data.idempotency_key,
            aspect_ratio=data.aspect_ratio, reference_images=references, reference_sources=available,
            trial_repository=SkillTrialRepository(db.pool, actor_id=actor, org_id=org_id),
        )
    except TrialInProgress:
        raise HTTPException(409, '试用仍在处理中，请稍后刷新卡片查看状态') from None
    except TrialConflict as error:
        raise HTTPException(409, str(error)) from None
    except PermissionError as error:
        raise HTTPException(403, str(error)) from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except Exception:
        raise HTTPException(502, '试用未完成，请使用新的试用请求重试') from None
    return {'success': True, 'data': result}


@router.put('/trials/{trial_id}/feedback')
def update_trial_feedback(
    trial_id: UUID,
    data: TrialFeedback,
    actor_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
):
    if not org_ctx.org_id:
        raise HTTPException(400, '试用反馈需要组织上下文')
    actor, org_id = str(actor_id), str(org_ctx.org_id)
    _require_trial_access(actor, org_id, db)
    try:
        result = save_trial_feedback(
            db, actor_id=actor, org_id=org_id, trial_id=trial_id,
            rating=data.rating, feedback_text=data.feedback_text,
            trial_repository=SkillTrialRepository(db.pool, actor_id=actor, org_id=org_id),
        )
    except PermissionError:
        raise HTTPException(404, '试用记录不存在') from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    return {'success': True, 'data': result}
