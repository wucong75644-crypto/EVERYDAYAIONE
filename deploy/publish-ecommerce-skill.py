"""Explicit, release-lock-owned publication of the single main-image entry Skill.

Run with the production backend Python. No credentials, alternate pool, browser
session or database superuser is used. The three planner prompts are code assets.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

APP = Path('/var/www/everydayai')
KEY = 'ecommerce-main-images'
REVISION = 'v1'
CONTENT_HASH = '8689f60335ab9152e7c6e2032adb107e3ddb551bd05886a13a13496ed913ae3e'


def install(raw, root, alias, relative):
    """The operator writes outside service namespaces; runtime storage stays RO."""
    if not alias.is_dir() or alias.is_symlink():
        raise ValueError('SKILL_OPERATOR_ALIAS_INVALID')
    target = alias / relative
    for directory in (target.parent.parent.parent, target.parent.parent, target.parent):
        directory.mkdir(mode=0o750, exist_ok=True)
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise ValueError('SKILL_OPERATOR_ALIAS_INVALID')
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o440)
    except FileExistsError:
        if target.is_symlink() or target.read_bytes() != raw:
            raise ValueError('SKILL_IMMUTABLE_REVISION_CONFLICT') from None
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    # Canonical readback also proves the writable operator alias reaches the
    # configured read-only namespace; no mount or service permissions change.
    if (root / relative).read_bytes() != raw:
        raise ValueError('SKILL_CANONICAL_READBACK_FAILED')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--lock-token')
    args = parser.parse_args()
    os.chdir(APP / 'backend')
    sys.path.insert(0, str(APP / 'backend'))
    from core.config import get_settings
    from core.database import get_db
    from core.db_scope import DatabaseAccessKind, DatabaseScope, SET_DATABASE_SCOPE_SQL
    from services.skills.catalog import SkillCatalog
    from services.skills.contracts import PackageCreate, PublishRevision
    from services.skills.repository import SkillRepository
    from services.skills.resolver import SkillResolutionContext, SkillResolver
    from services.skills.storage import SkillStorage

    raw = Path(args.source).read_bytes()
    if hashlib.sha256(raw).hexdigest() != CONTENT_HASH:
        raise ValueError('SKILL_SOURCE_HASH_MISMATCH')
    body = raw.split(b'---\n', 2)[2]
    publication = PublishRevision(revision=REVISION, content_sha256=CONTENT_HASH,
        body_sha256=hashlib.sha256(body).hexdigest())
    package_input = PackageCreate(skill_key=KEY, source='approved-ecommerce-main-image-entry', scope_kind='platform')
    validated = SkillStorage.validate_bytes(package_input, publication, raw)
    settings = get_settings()
    if not all((settings.skill_catalog_enabled, settings.skill_runtime_enabled,
                settings.ecom_image_planning_enabled, settings.chat_image_async_enabled)):
        raise ValueError('SKILL_REQUIRED_FEATURE_DISABLED')
    storage = SkillStorage(settings.skill_storage_root, workspace_root=settings.file_workspace_root)
    if not validated.catalog_metadata.model_selectable:
        raise ValueError('SKILL_AUTO_SELECTION_REQUIRED')
    request_id = 'ecommerce-skill-publication:' + (args.lock_token or 'check')
    scope = DatabaseScope(None, None, DatabaseAccessKind.RUNTIME_ADMIN, request_id)
    db = get_db()
    try:
        with db.pool.connection() as connection, connection.transaction():
            if not args.apply:
                connection.execute('SET TRANSACTION READ ONLY')
            connection.execute(SET_DATABASE_SCOPE_SQL, scope.settings)
            role = connection.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
            if role['rolsuper'] or role['rolbypassrls']:
                raise ValueError('SKILL_APPLICATION_ROLE_REQUIRED')
            if not connection.execute("SELECT to_regclass('public.skill_assignments_platform_key') AS idx").fetchone()['idx']:
                raise ValueError('SKILL_PLATFORM_ASSIGNMENT_MIGRATION_REQUIRED')

            class PinnedPool:
                @contextmanager
                def connection(self):
                    yield connection

            repository = SkillRepository(PinnedPool(), scope, owner_scope='platform')
            catalog = SkillCatalog(repository, settings)
            packages = [p for p in catalog.list_packages() if p.skill_key == KEY and p.scope_kind == 'platform']
            if args.apply:
                token = args.lock_token or ''
                if not re.fullmatch(r'[a-zA-Z0-9-]+', token):
                    raise ValueError('SKILL_RELEASE_LOCK_REQUIRED')
                if Path(str(APP) + '.release-lock/owner').read_text().strip() != token:
                    raise ValueError('SKILL_RELEASE_LOCK_REQUIRED')
                if (APP / '.release-provenance').exists():
                    raise ValueError('SKILL_RELEASE_CANDIDATE_MUST_BE_INVALIDATED')
                backup = Path('/root') / ('everydayai-ecommerce-skill-' + token)
                backup.mkdir(mode=0o700, exist_ok=True)
                previous = connection.execute('''SELECT a.* FROM skill_assignments a JOIN skill_packages p ON p.id=a.package_id
                    WHERE p.skill_key=%s AND p.scope_kind='platform' AND a.org_id IS NULL''', (KEY,)).fetchone()
                before = backup / 'before.json'
                if not before.exists():
                    before.write_text(json.dumps(dict(previous) if previous else None, default=str) + '\n')
                install(raw, storage.root, Path(settings.file_workspace_root) / '.platform-skills', validated.nas_path)
                storage.validate(package_input, publication)
                connection.execute("SELECT set_config('app.skill_action', 'platform_publish', true)")
                package = packages[0] if packages else catalog.create_package(package_input)
                existing = connection.execute('SELECT * FROM skill_revisions WHERE package_id=%s AND revision=%s',
                    (package.id, REVISION)).fetchone()
                if existing:
                    if (existing['content_sha256'] != CONTENT_HASH or existing['body_sha256'] != publication.body_sha256
                            or existing['status'] != 'published'):
                        raise ValueError('SKILL_IMMUTABLE_REVISION_CONFLICT')
                    revision_id = existing['id']
                else:
                    revision_id = catalog.publish_revision(package.id, publication).id
                catalog.set_platform_assignment(package.id, revision_id, enabled=True)
                packages = [package]

            available = False
            if packages:
                package = packages[0]
                flags = frozenset(name for name in type(settings).model_fields if getattr(settings, name) is True)
                # Real active identities, checked read-only in every actual org
                # context and standalone personal context. Never sends chat.
                rows = connection.execute('''SELECT u.id AS actor,m.org_id FROM users u
                    LEFT JOIN org_members m ON m.user_id=u.id AND m.status='active'
                    LEFT JOIN organizations o ON o.id=m.org_id
                    WHERE u.status='active' AND (m.org_id IS NULL OR o.status='active')''').fetchall()
                rows += [{'actor': r['actor'], 'org_id': None} for r in rows]
                contexts = {(str(row['actor']), str(row['org_id']) if row['org_id'] else None) for row in rows}
                for actor, org in contexts:
                    reader = SkillRepository(PinnedPool(), DatabaseScope(actor, org, DatabaseAccessKind.PROJECTION))
                    context = SkillResolutionContext(actor_user_id=actor, org_id=org, conversation_scope='user',
                        agent_domain='general', execution_mode='interactive', enabled_feature_flags=flags)
                    matches = [c for c in SkillResolver().select(context, reader.catalog_candidates()) if c.package_id == package.id]
                    if len(matches) != 1 or not matches[0].catalog_metadata.model_selectable:
                        raise ValueError('SKILL_PRODUCTION_DISCOVERY_FAILED')
                    revision = reader.assigned_revision(package.id, REVISION)
                    if revision.content_sha256 != CONTENT_HASH:
                        raise ValueError('SKILL_PRODUCTION_HASH_MISMATCH')
                    storage.validate(package, publication)
                available = bool(contexts)
            result = {'skill_key': KEY, 'revision': REVISION, 'published': bool(packages),
                'available_to_all_active_contexts': available, 'registered_internal_prompts': 0,
                'content_sha256': CONTENT_HASH}
            if args.apply:
                (backup / 'result.json').write_text(json.dumps(result) + '\n')
        print('ECOMMERCE_SKILL_RESULT ' + json.dumps(result))
    finally:
        db.close()


if __name__ == '__main__':
    main()
