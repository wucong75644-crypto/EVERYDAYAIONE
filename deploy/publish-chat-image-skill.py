"""Publish the release's orchestration method to one existing personal package."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID

APP = Path('/var/www/everydayai')
KEY = 'chat-image-orchestration'
PROTOCOL = 'content-objects-v2'


def publish(connection, actor, package_id, target, raw, lock_token, settings):
    from core.db_scope import DatabaseAccessKind, DatabaseScope, SET_DATABASE_SCOPE_SQL
    from services.skills.authoring import SkillAuthoring
    from services.skills.authoring_contracts import DraftContent, SaveDraft, TransitionDraft
    from services.skills.contracts import PublishRevision
    from services.skills.repository import SkillRepository
    from services.skills.storage import SkillStorage

    if not all((settings.skill_catalog_enabled, settings.skill_runtime_enabled,
                settings.chat_image_async_enabled, settings.agent_task_execution_enabled,
                settings.agent_task_model_directed_enabled)):
        raise ValueError('SKILL_REQUIRED_FEATURE_DISABLED')

    class PinnedPool:
        @contextmanager
        def connection(self):
            yield connection

    pool = PinnedPool()
    scope = DatabaseScope(str(actor), None, DatabaseAccessKind.RUNTIME_ADMIN,
                          'chat-image-skill-release:' + lock_token)
    connection.execute(SET_DATABASE_SCOPE_SQL, scope.settings)
    role = connection.execute('SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
    if role['rolsuper'] or role['rolbypassrls']:
        raise ValueError('SKILL_APPLICATION_ROLE_REQUIRED')
    if not connection.execute("SELECT 1 FROM users WHERE id=%s AND status='active'", (actor,)).fetchone():
        raise ValueError('SKILL_PERSONAL_OWNER_INACTIVE')

    repository = SkillRepository(pool, scope, owner_scope='personal')
    package = repository.get_owned_package(package_id)
    if package.skill_key != KEY:
        raise ValueError('SKILL_RELEASE_TARGET_MISMATCH')
    source_body = raw.split(b'---\n', 2)[2]
    publication = PublishRevision(revision='v3', content_sha256=hashlib.sha256(raw).hexdigest(),
                                  body_sha256=hashlib.sha256(source_body).hexdigest())
    source = SkillStorage.validate_bytes(package, publication, raw)
    if source.catalog_metadata.content_protocol != PROTOCOL or source.resources.assets or source.resources.template_variables:
        raise ValueError('SKILL_RELEASE_SOURCE_INVALID')
    content = DraftContent(description=source.summary, body=source.body, catalog_metadata=source.catalog_metadata)
    authoring = SkillAuthoring(repository, settings)

    with repository._cursor() as cursor:
        repository.lock_package_write(cursor, package_id)
    before = authoring.detail(package_id)
    available = before['available_revision']
    current = repository.assigned_revision(package_id, available)
    same_source = current.body_sha256 == publication.body_sha256 and current.catalog_metadata == source.catalog_metadata
    if not same_source:
        draft = before['draft']
        if (available != target['expected_revision'] or current.body_sha256 != target['expected_body_sha256']
                or not draft or draft['status'] != 'published' or draft['version'] != target['expected_draft_version']):
            raise ValueError('SKILL_RELEASE_BASE_CHANGED')
        started = authoring.transition(package_id, TransitionDraft(action='start_draft', expected_version=draft['version']))
        saved = authoring.save(package_id, SaveDraft(expected_version=started['draft']['version'], content=content))
        before = authoring.transition(package_id, TransitionDraft(action='publish_private',
                                                               expected_version=saved['draft']['version']))

    projection = SkillRepository(pool, DatabaseScope(str(actor), None, DatabaseAccessKind.PROJECTION))
    matches = [item for item in projection.catalog_candidates() if item.skill_key == KEY]
    if len(matches) != 1 or matches[0].package_id != package_id:
        raise ValueError('SKILL_PRODUCTION_DISCOVERY_FAILED')
    published = projection.assigned_revision(package_id, matches[0].revision)
    if (published.revision != before['available_revision'] or published.body_sha256 != publication.body_sha256
            or published.catalog_metadata != source.catalog_metadata):
        raise ValueError('SKILL_PRODUCTION_REVISION_MISMATCH')
    SkillStorage(settings.skill_storage_root, workspace_root=settings.file_workspace_root).validate(
        package, PublishRevision(revision=published.revision, content_sha256=published.content_sha256,
                                 body_sha256=published.body_sha256), nas_path=published.nas_path)
    return {'skill_key': KEY, 'scope': 'personal', 'revision': published.revision,
            'body_sha256': published.body_sha256, 'protocol': PROTOCOL, 'replayed': same_source}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lock-token', required=True)
    args = parser.parse_args()
    if (not re.fullmatch(r'[a-zA-Z0-9-]+', args.lock_token)
            or Path(str(APP) + '.release-lock/owner').read_text().strip() != args.lock_token
            or (APP / '.release-provenance').exists()):
        raise ValueError('SKILL_RELEASE_LOCK_REQUIRED')
    request = json.load(sys.stdin)
    target = request['target']
    actor, package_id = UUID(target['owner_user_id']), UUID(target['package_id'])
    raw = request['source'].encode('utf-8')
    if hashlib.sha256(raw).hexdigest() != target['source_sha256']:
        raise ValueError('SKILL_RELEASE_SOURCE_HASH_MISMATCH')

    os.chdir(APP / 'backend')
    sys.path.insert(0, str(APP / 'backend'))
    from core.config import get_settings
    from core.database import get_db
    db = get_db()
    try:
        with db.pool.connection() as connection, connection.transaction():
            result = publish(connection, actor, package_id, target, raw, args.lock_token, get_settings())
        print('CHAT_IMAGE_SKILL_RESULT ' + json.dumps(result, ensure_ascii=False))
    finally:
        db.close()


if __name__ == '__main__':
    main()
