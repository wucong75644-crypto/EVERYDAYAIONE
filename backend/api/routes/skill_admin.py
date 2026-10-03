"""Organization admin control plane. Platform ownership cannot be requested here."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from psycopg import Error as DatabaseError

from api.deps import CurrentUser, CurrentUserId, Database
from core.config import get_settings
from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.skills.authoring import SkillAuthoring
from services.skills.authoring_contracts import CreateSkill, ExpectedVersion, SaveDraft, TransitionDraft
from services.skills.contracts import SkillError
from services.skills.assets import MAX_SOURCE_BYTES
from services.skills.imports import import_attachment
from services.skills.repository import SkillRepository

router = APIRouter(prefix='/skills/admin/orgs/{org_id}', tags=['Skill 管理'])
scope_router = APIRouter(prefix='/skills/admin', tags=['Skill 管理'])


def get_authoring(org_id: UUID, user_id: CurrentUserId, user: CurrentUser, db: Database,
                  response: Response, settings=Depends(get_settings)):
    response.headers['Cache-Control'] = 'no-store'
    request_id = str(uuid4())
    response.headers['X-Request-Id'] = request_id
    organization = db.table('organizations').select('status').eq('id', str(org_id)).maybe_single().execute()
    membership = db.table('org_members').select('status,role').eq('org_id', str(org_id)).eq(
        'user_id', user_id).maybe_single().execute()
    if (user.get('status') != 'active' or not organization or not organization.data
            or organization.data.get('status') != 'active' or not membership or not membership.data
            or membership.data.get('status') != 'active' or membership.data.get('role') not in ('owner', 'admin')):
        raise HTTPException(403, '仅活跃组织的管理员可管理 Skill')
    if settings.skill_catalog_enabled is not True:
        raise HTTPException(503, 'Skill 管理尚未启用')
    return SkillAuthoring(SkillRepository(db.pool, DatabaseScope(
        actor_user_id=user_id, org_id=str(org_id), access_kind=DatabaseAccessKind.RUNTIME_ADMIN, request_id=request_id,
    )), settings)


Admin = Annotated[SkillAuthoring, Depends(get_authoring)]


class PlatformProposalDecision(BaseModel):
    action: str = Field(pattern=r'^(approve|reject)$')
    reason: str = Field(default='', max_length=1000)


def run(operation, *args):
    try:
        return operation(*args)
    except SkillError as error:
        code = str(error)
        if code in ('SKILL_OWNER_SCOPE_MISMATCH', 'SKILL_CONTROL_ACCESS_REQUIRED'):
            raise HTTPException(403, '不能修改平台或其他组织的 Skill') from None
        if code in ('SKILL_PACKAGE_UNAVAILABLE', 'SKILL_REVISION_UNAVAILABLE'):
            raise HTTPException(404, 'Skill 或版本不存在') from None
        if code in ('SKILL_VERSION_CONFLICT', 'SKILL_KEY_EXISTS', 'SKILL_DELETE_IN_USE', 'SKILL_DELETE_CHECK_UNCERTAIN'):
            raise HTTPException(409, code) from None
        if code.startswith('SKILL_STORAGE_') or 'HASH_MISMATCH' in code:
            raise HTTPException(503, 'SKILL_STORAGE_UNAVAILABLE') from None
        # Keep the existing detail while exposing the stable code to the shared
        # frontend error decoder. Never include draft content or storage paths.
        return JSONResponse(status_code=422, headers={'Cache-Control': 'no-store'}, content={
            'detail': code,
            'error': {'code': code, 'message': 'Skill 内容或状态校验失败'},
        })
    except DatabaseError:
        raise HTTPException(503, 'SKILL_DATABASE_UNAVAILABLE') from None


@router.get('')
def list_skills(admin: Admin):
    return run(admin.list)


@router.post('', status_code=201)
def create_skill(data: CreateSkill, admin: Admin):
    return run(admin.create, data)


@router.post('/attachments/import')
def upload_attachment(file: UploadFile, admin: Admin):
    # Same fresh organization-admin authorization as draft editing. Files are
    # returned as draft data, never written to workspace/OSS/public URLs.
    try:
        return run(import_attachment, file.filename or '', file.file.read(MAX_SOURCE_BYTES + 1))
    finally:
        file.file.close()


@router.get('/{package_id}')
def get_skill(package_id: UUID, admin: Admin):
    return run(admin.detail, package_id)


@router.put('/{package_id}/draft')
def save_draft(package_id: UUID, data: SaveDraft, admin: Admin):
    return run(admin.save, package_id, data)


@router.post('/{package_id}/transitions')
def transition(package_id: UUID, data: TransitionDraft, admin: Admin):
    return run(admin.transition, package_id, data)


@router.get('/{package_id}/revisions/{revision}')
def read_revision(package_id: UUID, revision: str, admin: Admin):
    return run(admin.read_revision, package_id, revision)


@router.get('/{package_id}/deletion-check')
def deletion_check(package_id: UUID, admin: Admin):
    return run(admin.deletion_check, package_id)


@router.delete('/{package_id}')
def delete_skill(package_id: UUID, data: ExpectedVersion, admin: Admin):
    return run(admin.delete, package_id, data)


def get_personal_authoring(user_id: CurrentUserId, user: CurrentUser, db: Database,
                           response: Response, settings=Depends(get_settings)):
    response.headers['Cache-Control'] = 'no-store'
    request_id = str(uuid4())
    response.headers['X-Request-Id'] = request_id
    if user.get('status') != 'active':
        raise HTTPException(403, '当前账号不可管理个人 Skill')
    if settings.skill_catalog_enabled is not True:
        raise HTTPException(503, 'Skill 管理尚未启用')
    repository = SkillRepository(db.pool, DatabaseScope(
        actor_user_id=user_id, org_id=None, access_kind=DatabaseAccessKind.RUNTIME_ADMIN,
        request_id=request_id,
    ), owner_scope='personal')
    return SkillAuthoring(repository, settings)


PersonalAdmin = Annotated[SkillAuthoring, Depends(get_personal_authoring)]


@scope_router.get('/personal')
def list_personal_skills(admin: PersonalAdmin):
    return run(admin.list)


@scope_router.post('/personal', status_code=201)
def create_personal_skill(data: CreateSkill, admin: PersonalAdmin):
    return run(admin.create, data)


@scope_router.post('/personal/attachments/import')
def upload_personal_attachment(file: UploadFile, admin: PersonalAdmin):
    try:
        return run(import_attachment, file.filename or '', file.file.read(MAX_SOURCE_BYTES + 1))
    finally:
        file.file.close()


@scope_router.get('/personal/{package_id}')
def get_personal_skill(package_id: UUID, admin: PersonalAdmin):
    return run(admin.detail, package_id)


@scope_router.put('/personal/{package_id}/draft')
def save_personal_draft(package_id: UUID, data: SaveDraft, admin: PersonalAdmin):
    return run(admin.save, package_id, data)


@scope_router.post('/personal/{package_id}/transitions')
def transition_personal_skill(package_id: UUID, data: TransitionDraft, admin: PersonalAdmin):
    return run(admin.transition, package_id, data)


@scope_router.get('/personal/{package_id}/revisions/{revision}')
def read_personal_revision(package_id: UUID, revision: str, admin: PersonalAdmin):
    return run(admin.read_revision, package_id, revision)


@scope_router.get('/personal/{package_id}/deletion-check')
def personal_deletion_check(package_id: UUID, admin: PersonalAdmin):
    return run(admin.deletion_check, package_id)


@scope_router.delete('/personal/{package_id}')
def delete_personal_skill(package_id: UUID, data: ExpectedVersion, admin: PersonalAdmin):
    return run(admin.delete, package_id, data)


def get_platform_authoring(user: CurrentUser, user_id: CurrentUserId, db: Database,
                           response: Response, settings=Depends(get_settings)):
    response.headers['Cache-Control'] = 'no-store'
    request_id = str(uuid4())
    response.headers['X-Request-Id'] = request_id
    if user.get('status') != 'active' or user.get('role') != 'super_admin':
        raise HTTPException(403, '仅平台管理员可管理平台 Skill')
    if settings.skill_catalog_enabled is not True:
        raise HTTPException(503, 'Skill 管理尚未启用')
    repository = SkillRepository(db.pool, DatabaseScope(
        actor_user_id=user_id, org_id=None, access_kind=DatabaseAccessKind.RUNTIME_ADMIN,
        request_id=request_id,
    ), owner_scope='platform')
    return SkillAuthoring(repository, settings)


PlatformAdmin = Annotated[SkillAuthoring, Depends(get_platform_authoring)]


@scope_router.get('/platform')
def list_platform_skills(admin: PlatformAdmin):
    return run(admin.list)


@scope_router.get('/platform/chat-proposals')
def list_platform_chat_proposals(admin: PlatformAdmin, settings=Depends(get_settings)):
    if settings.skill_chat_creation_enabled is not True:
        raise HTTPException(503, 'Skill 对话创建尚未启用')
    return run(admin.list_platform_chat_proposals)


@scope_router.post('/platform/chat-proposals/{proposal_id}/decision')
def decide_platform_chat_proposal(proposal_id: UUID, data: PlatformProposalDecision,
                                 admin: PlatformAdmin, settings=Depends(get_settings)):
    if settings.skill_chat_creation_enabled is not True:
        raise HTTPException(503, 'Skill 对话创建尚未启用')
    return run(admin.decide_platform_chat_proposal, proposal_id,
               approve=data.action == 'approve', reason=data.reason)


@scope_router.post('/platform', status_code=201)
def create_platform_skill(data: CreateSkill, admin: PlatformAdmin):
    return run(admin.create, data)


@scope_router.post('/platform/attachments/import')
def upload_platform_attachment(file: UploadFile, admin: PlatformAdmin):
    try:
        return run(import_attachment, file.filename or '', file.file.read(MAX_SOURCE_BYTES + 1))
    finally:
        file.file.close()


@scope_router.get('/platform/{package_id}')
def get_platform_skill(package_id: UUID, admin: PlatformAdmin):
    return run(admin.detail, package_id)


@scope_router.put('/platform/{package_id}/draft')
def save_platform_draft(package_id: UUID, data: SaveDraft, admin: PlatformAdmin):
    return run(admin.save, package_id, data)


@scope_router.post('/platform/{package_id}/transitions')
def transition_platform_skill(package_id: UUID, data: TransitionDraft, admin: PlatformAdmin):
    return run(admin.transition, package_id, data)


@scope_router.get('/platform/{package_id}/revisions/{revision}')
def read_platform_revision(package_id: UUID, revision: str, admin: PlatformAdmin):
    return run(admin.read_revision, package_id, revision)


@scope_router.get('/platform/{package_id}/deletion-check')
def platform_deletion_check(package_id: UUID, admin: PlatformAdmin):
    return run(admin.deletion_check, package_id)


@scope_router.delete('/platform/{package_id}')
def delete_platform_skill(package_id: UUID, data: ExpectedVersion, admin: PlatformAdmin):
    return run(admin.delete, package_id, data)
