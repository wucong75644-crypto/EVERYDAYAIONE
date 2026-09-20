"""Organization admin control plane. Platform ownership cannot be requested here."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import Error as DatabaseError

from api.deps import CurrentUser, CurrentUserId, Database
from core.config import get_settings
from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.skills.authoring import SkillAuthoring
from services.skills.authoring_contracts import CreateSkill, SaveDraft, TransitionDraft
from services.skills.contracts import SkillError
from services.skills.repository import SkillRepository

router = APIRouter(prefix='/skills/admin/orgs/{org_id}', tags=['Skill 管理'])


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


def run(operation, *args):
    try:
        return operation(*args)
    except SkillError as error:
        code = str(error)
        if code in ('SKILL_OWNER_SCOPE_MISMATCH', 'SKILL_CONTROL_ACCESS_REQUIRED'):
            raise HTTPException(403, '不能修改平台或其他组织的 Skill') from None
        if code in ('SKILL_PACKAGE_UNAVAILABLE', 'SKILL_REVISION_UNAVAILABLE'):
            raise HTTPException(404, 'Skill 或版本不存在') from None
        if code in ('SKILL_VERSION_CONFLICT', 'SKILL_KEY_EXISTS'):
            raise HTTPException(409, code) from None
        if code.startswith('SKILL_STORAGE_') or 'HASH_MISMATCH' in code:
            raise HTTPException(503, 'SKILL_STORAGE_UNAVAILABLE') from None
        raise HTTPException(422, code) from None
    except DatabaseError:
        raise HTTPException(503, 'SKILL_DATABASE_UNAVAILABLE') from None


@router.get('')
def list_skills(admin: Admin):
    return run(admin.list)


@router.post('', status_code=201)
def create_skill(data: CreateSkill, admin: Admin):
    return run(admin.create, data)


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
