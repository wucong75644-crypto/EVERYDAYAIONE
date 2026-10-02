"""Transactional Skill authoring; NAS is written before a release becomes visible."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from services.skills.authoring_contracts import DraftContent, RevisionContent, reviewed_document, new_draft_content
from services.skills.assets import AssetDraft, public_assets
from services.skills.contracts import PublishRevision, SkillError, SkillPackage, SkillRevision
from services.skills.creation_policy import from_features
from services.skills.repository import SkillRepository
from services.skills.reenable import reenable
from services.skills.storage import SkillStorage


class SkillAuthoring:
    def __init__(self, repository: SkillRepository, settings):
        self.repository, self.settings = repository, settings

    def _enabled(self):
        if self.settings.skill_catalog_enabled is not True:
            raise SkillError('SKILL_CATALOG_DISABLED')
        self.repository._require_admin()
        if not self.repository.scope.actor_user_id:
            raise SkillError('SKILL_CONTROL_ACCESS_REQUIRED')

    def _storage(self):
        return SkillStorage(self.settings.skill_storage_root,
                            workspace_root=self.settings.file_workspace_root)

    @contextmanager
    def _transaction(self, action):
        self._enabled()
        with self.repository._cursor() as cursor:
            repository_scope = self.repository.owner_scope
            scoped_action = f'platform_{action}' if repository_scope == 'platform' else action
            cursor.execute("SELECT set_config('app.skill_action', %s, true)", (scoped_action,))
            yield cursor

    def _package(self, cursor, package_id, *, owned=False):
        # Serialize initial draft creation as well as later mutations without
        # granting UPDATE on the immutable package table.
        if owned:
            self.repository.lock_package_write(cursor, package_id)
        cursor.execute('''SELECT * FROM public.skill_packages WHERE id = %s
            AND (scope_kind = 'platform'
                OR (scope_kind = 'org' AND org_id = %s::uuid)
                OR (scope_kind = 'personal' AND owner_user_id = %s::uuid))
            AND NOT EXISTS (SELECT 1 FROM public.skill_drafts d
                WHERE d.package_id = skill_packages.id AND d.deleted_at IS NOT NULL)''',
            (package_id, self.repository.scope.org_id, self.repository.scope.actor_user_id))
        row = cursor.fetchone()
        if not row:
            raise SkillError('SKILL_PACKAGE_UNAVAILABLE')
        package = SkillPackage.model_validate(row)
        repository_scope = self.repository.owner_scope
        if repository_scope == 'personal' and package.scope_kind != 'personal':
            raise SkillError('SKILL_PACKAGE_UNAVAILABLE')
        if repository_scope == 'platform' and package.scope_kind != 'platform':
            raise SkillError('SKILL_PACKAGE_UNAVAILABLE')
        if owned:
            self.repository._require_owner(package)
        return package

    @staticmethod
    def _draft(cursor, package_id):
        cursor.execute('SELECT * FROM public.skill_drafts WHERE package_id = %s FOR UPDATE', (package_id,))
        return cursor.fetchone()

    @staticmethod
    def _check_version(draft, expected):
        if (draft['version'] if draft else 0) != expected:
            raise SkillError('SKILL_VERSION_CONFLICT')

    @staticmethod
    def _insert_draft(cursor, package_id, content):
        cursor.execute('''INSERT INTO public.skill_drafts(package_id, revision, content)
            VALUES (%s, %s, %s) RETURNING *''',
            (package_id, 'v' + uuid4().hex, Jsonb(content.model_dump(mode='json'))))
        return cursor.fetchone()

    def create(self, data):
        try:
            with self._transaction('create') as cursor:
                org = self.repository.scope.org_id
                scope_kind = self.repository.owner_scope or ('org' if org else 'platform')
                if scope_kind == 'personal':
                    org, owner = None, self.repository.scope.actor_user_id
                elif scope_kind == 'platform':
                    org, owner = None, None
                else:
                    owner = None
                cursor.execute('''INSERT INTO public.skill_packages
                    (skill_key, source, scope_kind, org_id, owner_user_id)
                    VALUES (%s, 'admin', %s, %s, %s) RETURNING *''',
                    (data.skill_key, scope_kind, org, owner))
                package = SkillPackage.model_validate(cursor.fetchone())
                self._insert_draft(cursor, package.id, new_draft_content(data.content))
                return {'package_id': package.id}
        except UniqueViolation:
            raise SkillError('SKILL_KEY_EXISTS') from None

    def commit_chat_draft(self, *, change_set_id, package_id, skill_key, content,
                          operation, expected_version, content_sha256):
        """Commit a confirmed chat proposal exactly once with its receipt."""
        import re

        if (not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', skill_key)
                or operation not in ('create', 'update')
                or hashlib.sha256(json.dumps(content.model_dump(mode='json'), ensure_ascii=False,
                    sort_keys=True, separators=(',', ':')).encode()).hexdigest() != content_sha256):
            raise SkillError('SKILL_CANDIDATE_INVALID')
        org_id = self.repository._require_org()
        actor_id = self.repository.scope.actor_user_id
        try:
            with self._transaction('chat_create' if operation == 'create' else 'chat_update') as cursor:
                # Recheck authority while holding the business transaction. The
                # HTTP layer checks before starting; this closes revocation races.
                cursor.execute('''SELECT 1 FROM public.organizations o
                    JOIN public.org_members m ON m.org_id = o.id AND m.user_id = %s::uuid
                    JOIN public.users u ON u.id = m.user_id
                    WHERE o.id = %s::uuid AND o.status = 'active' AND m.status = 'active'
                        AND m.role IN ('owner', 'admin') AND u.status = 'active'
                    FOR SHARE OF o, m, u''', (actor_id, org_id))
                if not cursor.fetchone():
                    raise SkillError('SKILL_ORG_ADMIN_REQUIRED')
                self.repository.lock_package_write(cursor, package_id)
                cursor.execute('''SELECT package_id, operation, content_sha256, draft_revision, draft_version
                    FROM public.skill_authoring_receipts WHERE change_set_id = %s AND org_id = %s::uuid''',
                    (change_set_id, org_id))
                receipt = cursor.fetchone()
                if receipt:
                    if (str(receipt['package_id']) != str(package_id) or receipt['operation'] != operation
                            or receipt['content_sha256'] != content_sha256):
                        raise SkillError('SKILL_RECEIPT_CONFLICT')
                    return {'package_id': str(receipt['package_id']), 'draft_revision': receipt['draft_revision'],
                            'draft_version': receipt['draft_version'], 'content_sha256': receipt['content_sha256'],
                            'replayed': True}

                if operation == 'create':
                    cursor.execute('''INSERT INTO public.skill_packages
                        (id, skill_key, source, scope_kind, org_id)
                        VALUES (%s, %s, 'chat', 'org', %s::uuid) RETURNING *''',
                        (package_id, skill_key, org_id))
                    package = SkillPackage.model_validate(cursor.fetchone())
                    draft = self._insert_draft(cursor, package.id, new_draft_content(content))
                else:
                    package = self._package(cursor, package_id, owned=True)
                    if package.skill_key != skill_key:
                        raise SkillError('SKILL_OWNER_SCOPE_MISMATCH')
                    draft = self._draft(cursor, package_id)
                    self._check_version(draft, expected_version)
                    if not draft or draft['status'] != 'draft':
                        raise SkillError('SKILL_TRANSITION_INVALID')
                    cursor.execute('''UPDATE public.skill_drafts SET content = %s, revision = %s,
                        version = version + 1 WHERE package_id = %s RETURNING *''',
                        (Jsonb(new_draft_content(content).model_dump(mode='json')),
                         'v' + uuid4().hex, package_id))
                    draft = cursor.fetchone()
                cursor.execute('''INSERT INTO public.skill_authoring_receipts
                    (change_set_id, org_id, actor_user_id, package_id, operation, content_sha256,
                     draft_revision, draft_version)
                    VALUES (%s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s)''',
                    (change_set_id, org_id, actor_id, package_id, operation, content_sha256,
                     draft['revision'], draft['version']))
                return {'package_id': str(package_id), 'draft_revision': draft['revision'],
                        'draft_version': draft['version'], 'content_sha256': content_sha256,
                        'replayed': False}
        except UniqueViolation as error:
            raise SkillError('SKILL_KEY_EXISTS') from error

    def commit_chat_proposal(self, *, proposal_id, expected_version, content_sha256, target_scope):
        """Confirm one private chat proposal under the selected, revalidated scope."""
        actor_id = self.repository.scope.actor_user_id
        if target_scope not in ('personal', 'org', 'platform'):
            raise SkillError('SKILL_TARGET_SCOPE_INVALID')
        try:
            with self._transaction('chat_proposal') as cursor:
                cursor.execute("SELECT id FROM public.users WHERE id = %s::uuid AND status = 'active' FOR SHARE",
                               (actor_id,))
                if not cursor.fetchone():
                    raise SkillError('SKILL_ACTOR_UNAVAILABLE')
                cursor.execute('''SELECT * FROM public.skill_chat_proposals
                    WHERE id = %s::uuid AND actor_user_id = %s::uuid FOR UPDATE''',
                    (proposal_id, actor_id))
                proposal = cursor.fetchone()
                if not proposal:
                    raise SkillError('SKILL_PROPOSAL_UNAVAILABLE')
                if proposal['status'] in ('committed', 'awaiting_review'):
                    if proposal['target_scope'] != target_scope:
                        raise SkillError('SKILL_PROPOSAL_SCOPE_CONFLICT')
                    return {'proposal_id': str(proposal_id), 'status': proposal['status'],
                            'target_scope': proposal['target_scope'], 'target_org_id': proposal['target_org_id'],
                            'package_id': str(proposal['package_id']) if proposal['package_id'] else None,
                            'result': proposal['result'], 'replayed': True}
                if (proposal['status'] != 'awaiting_confirmation'
                        or proposal['expires_at'] <= datetime.now(timezone.utc)
                        or int(proposal['version']) != expected_version
                        or proposal['content_sha256'] != content_sha256):
                    raise SkillError('SKILL_PROPOSAL_STALE')
                cursor.execute('''SELECT user_id, org_id, scope_type FROM public.conversations
                    WHERE id = %s::uuid FOR SHARE''', (proposal['conversation_id'],))
                conversation = cursor.fetchone()
                if (not conversation or str(conversation['user_id']) != str(actor_id)
                        or str(conversation['org_id'] or '') != str(proposal['org_id'] or '')
                        or conversation['scope_type'] != 'user'):
                    raise SkillError('SKILL_CONVERSATION_UNAVAILABLE')
                if proposal['org_id']:
                    cursor.execute('''SELECT o.status AS org_status, o.features,
                        m.status AS member_status FROM public.organizations o
                        JOIN public.org_members m ON m.org_id = o.id AND m.user_id = %s::uuid
                        WHERE o.id = %s::uuid FOR SHARE OF o, m''',
                        (actor_id, proposal['org_id']))
                    organization = cursor.fetchone()
                    if (not organization or organization['org_status'] != 'active'
                            or organization['member_status'] != 'active'):
                        raise SkillError('SKILL_ORG_MEMBERSHIP_REQUIRED')
                    policy = from_features(organization['features'])
                else:
                    policy = from_features(None)
                if not policy.chat_creation_enabled:
                    raise SkillError('SKILL_CHAT_CREATION_DISABLED')
                if target_scope == 'org' and not policy.org_submission_enabled:
                    raise SkillError('SKILL_ORG_SUBMISSION_DISABLED')
                if target_scope == 'platform' and not policy.platform_submission_enabled:
                    raise SkillError('SKILL_PLATFORM_SUBMISSION_DISABLED')
                content = DraftContent.model_validate(proposal['content'])
                from services.skills.chat_creation import content_digest
                if content_digest(content) != content_sha256:
                    raise SkillError('SKILL_CANDIDATE_HASH_MISMATCH')
                target_org_id = None
                if target_scope == 'org':
                    target_org_id = proposal['org_id']
                    if not target_org_id:
                        raise SkillError('SKILL_ORG_MEMBERSHIP_REQUIRED')
                    cursor.execute('''SELECT 1 FROM public.organizations o JOIN public.org_members m ON m.org_id = o.id
                        WHERE o.id = %s::uuid AND o.status = 'active' AND m.user_id = %s::uuid AND m.status = 'active'
                        FOR SHARE OF o, m''', (target_org_id, actor_id))
                    if not cursor.fetchone():
                        raise SkillError('SKILL_ORG_MEMBERSHIP_REQUIRED')
                if target_scope == 'platform':
                    result = {'message': '已提交平台 Skill 审核申请，平台管理员审核后会发布。'}
                    cursor.execute('''UPDATE public.skill_chat_proposals SET status = 'awaiting_review',
                        target_scope = 'platform', scope_confirmed_by = %s::uuid, scope_selected_at = now(),
                        updated_at = now(), result = %s
                        WHERE id = %s::uuid''', (actor_id, Jsonb(result), proposal_id))
                    return {'proposal_id': str(proposal_id), 'status': 'awaiting_review',
                            'target_scope': 'platform', 'target_org_id': None,
                            'package_id': None, 'result': result, 'replayed': False}

                package_id = uuid4()
                if target_scope == 'personal':
                    cursor.execute('''INSERT INTO public.skill_packages
                        (id, skill_key, source, scope_kind, org_id, owner_user_id)
                        VALUES (%s, %s, 'chat', 'personal', NULL, %s::uuid) RETURNING *''',
                        (package_id, proposal['skill_key'], actor_id))
                    result_message = '个人 Skill 已发布，仅本人可见和使用。'
                else:
                    cursor.execute('''INSERT INTO public.skill_packages
                        (id, skill_key, source, scope_kind, org_id, owner_user_id)
                        VALUES (%s, %s, 'chat', 'org', %s::uuid, NULL) RETURNING *''',
                        (package_id, proposal['skill_key'], target_org_id))
                    result_message = '已创建组织 Skill 待审核草稿，组织管理员审核发布后可供组织使用。'
                package = SkillPackage.model_validate(cursor.fetchone())
                draft = self._insert_draft(cursor, package.id, new_draft_content(content))
                if target_scope == 'personal':
                    publication, _ = reviewed_document(package, draft['revision'], content)
                    cursor.execute("UPDATE public.skill_drafts SET status = 'in_review', version = version + 1 WHERE package_id = %s",
                                   (package.id,))
                    cursor.execute('''UPDATE public.skill_drafts SET approved_by = %s::uuid,
                        approved_sha256 = %s, approved_at = now(), version = version + 1
                        WHERE package_id = %s RETURNING *''', (actor_id, publication.content_sha256, package.id))
                    draft = cursor.fetchone()
                    self._review_or_publish(cursor, package, draft, 'publish')
                    result_status = 'published'
                else:
                    cursor.execute("UPDATE public.skill_drafts SET status = 'in_review', version = version + 1 WHERE package_id = %s",
                                   (package.id,))
                    result_status = 'in_review'
                result = {'message': result_message, 'package_id': str(package.id), 'status': result_status}
                cursor.execute('''UPDATE public.skill_chat_proposals SET status = 'committed',
                    target_scope = %s, target_org_id = %s::uuid, package_id = %s,
                    scope_confirmed_by = %s::uuid, scope_selected_at = now(), decision_by = %s::uuid,
                    decision_at = now(), updated_at = now(), result = %s
                    WHERE id = %s::uuid''',
                    (target_scope, target_org_id, package.id, actor_id, actor_id, Jsonb(result), proposal_id))
                return {'proposal_id': str(proposal_id), 'status': 'committed',
                        'target_scope': target_scope, 'target_org_id': target_org_id,
                        'package_id': str(package.id), 'result': result, 'replayed': False}
        except UniqueViolation as error:
            raise SkillError('SKILL_KEY_EXISTS') from error

    def list_platform_chat_proposals(self):
        with self._transaction('read') as cursor:
            cursor.execute('''SELECT id, actor_user_id, conversation_id, skill_key, content,
                content_sha256, version, source_message_refs, source_scope, created_at
                FROM public.skill_chat_proposals WHERE target_scope = 'platform' AND status = 'awaiting_review'
                ORDER BY created_at ASC, id''')
            return cursor.fetchall()

    def decide_platform_chat_proposal(self, proposal_id, *, approve: bool, reason: str = ''):
        actor_id = self.repository.scope.actor_user_id
        if self.repository.owner_scope != 'platform':
            raise SkillError('SKILL_PLATFORM_ADMIN_REQUIRED')
        with self._transaction('chat_publish') as cursor:
            cursor.execute("SELECT 1 FROM public.users WHERE id = %s::uuid AND status = 'active' AND role = 'super_admin' FOR SHARE",
                           (actor_id,))
            if not cursor.fetchone():
                raise SkillError('SKILL_PLATFORM_ADMIN_REQUIRED')
            cursor.execute('''SELECT * FROM public.skill_chat_proposals
                WHERE id = %s::uuid AND target_scope = 'platform' AND status = 'awaiting_review' FOR UPDATE''',
                (proposal_id,))
            proposal = cursor.fetchone()
            if not proposal:
                raise SkillError('SKILL_PROPOSAL_UNAVAILABLE')
            if not approve:
                cursor.execute('''UPDATE public.skill_chat_proposals SET status = 'rejected', decision_by = %s::uuid,
                    decision_at = now(), decision_reason = %s, updated_at = now()
                    WHERE id = %s::uuid RETURNING id''', (actor_id, reason[:1000], proposal_id))
                return {'proposal_id': str(proposal_id), 'status': 'rejected'}
            from services.skills.chat_creation import content_digest
            content = DraftContent.model_validate(proposal['content'])
            if content_digest(content) != proposal['content_sha256']:
                raise SkillError('SKILL_CANDIDATE_HASH_MISMATCH')
            package_id = uuid4()
            cursor.execute('''INSERT INTO public.skill_packages
                (id, skill_key, source, scope_kind, org_id, owner_user_id)
                VALUES (%s, %s, 'chat', 'platform', NULL, NULL) RETURNING *''',
                (package_id, proposal['skill_key']))
            package = SkillPackage.model_validate(cursor.fetchone())
            draft = self._insert_draft(cursor, package.id, new_draft_content(content))
            publication, _ = reviewed_document(package, draft['revision'], content)
            cursor.execute("UPDATE public.skill_drafts SET status = 'in_review', version = version + 1 WHERE package_id = %s",
                           (package.id,))
            cursor.execute('''UPDATE public.skill_drafts SET approved_by = %s::uuid,
                approved_sha256 = %s, approved_at = now(), version = version + 1
                WHERE package_id = %s RETURNING *''', (actor_id, publication.content_sha256, package.id))
            self._review_or_publish(cursor, package, cursor.fetchone(), 'publish')
            result = {'message': '平台管理员已审核并发布 Skill。', 'package_id': str(package.id), 'status': 'published'}
            cursor.execute('''UPDATE public.skill_chat_proposals SET status = 'committed', package_id = %s,
                decision_by = %s::uuid, decision_at = now(), decision_reason = %s, updated_at = now(), result = %s
                WHERE id = %s::uuid''',
                (package.id, actor_id, reason[:1000], Jsonb(result), proposal_id))
            return {'proposal_id': str(proposal_id), 'status': 'committed', 'result': result}

    def list(self):
        with self._transaction('list') as cursor:
            cursor.execute('''SELECT p.id AS package_id, p.skill_key, p.scope_kind,
                d.version, coalesce(d.status, CASE WHEN r.status = 'retired' THEN 'disabled'
                    ELSE r.status END, 'draft') AS status,
                r.revision AS published_revision, r.summary AS description,
                CASE WHEN d.package_id IS NOT NULL THEN nullif(d.content->'catalog_metadata'->>'name', '')
                    ELSE r.catalog_metadata->>'name' END AS name,
                coalesce(d.content->>'description', r.summary) AS working_description,
                coalesce(d.updated_at, r.created_at, p.created_at) AS updated_at,
                COALESCE(ar.revision, personal_ar.revision) AS available_revision,
                CASE WHEN COALESCE(ar.id, personal_ar.id) IS NOT NULL THEN (SELECT count(*) FROM public.skill_revisions prior
                    WHERE prior.package_id = p.id AND (prior.created_at, prior.id) <=
                        (COALESCE(ar.created_at, personal_ar.created_at), COALESCE(ar.id, personal_ar.id)))
                    END AS available_revision_number,
                d.approved_by IS NOT NULL AS approved
                FROM public.skill_packages p
                LEFT JOIN public.skill_drafts d ON d.package_id = p.id
                LEFT JOIN LATERAL (SELECT revision, summary, status, catalog_metadata, created_at FROM public.skill_revisions
                    WHERE package_id = p.id ORDER BY created_at DESC, id DESC LIMIT 1) r ON true
                LEFT JOIN public.skill_assignments a ON a.package_id = p.id AND a.org_id = %s::uuid AND a.enabled
                LEFT JOIN public.skill_revisions ar ON ar.id = a.revision_id AND ar.status = 'published'
                LEFT JOIN LATERAL (SELECT id, revision, created_at FROM public.skill_revisions personal_revision
                    WHERE personal_revision.package_id = p.id AND personal_revision.status = 'published'
                        AND p.scope_kind = 'personal'
                    ORDER BY personal_revision.created_at DESC, personal_revision.id DESC LIMIT 1) personal_ar ON true
                WHERE ((p.scope_kind = 'platform'
                    OR (p.scope_kind = 'org' AND p.org_id = %s::uuid)
                    OR (p.scope_kind = 'personal' AND p.owner_user_id = %s::uuid))
                    AND (%s IS NULL OR p.scope_kind = %s)) AND d.deleted_at IS NULL
                ORDER BY p.skill_key, p.id''',
                (self.repository.scope.org_id, self.repository.scope.actor_user_id,
                 self.repository.owner_scope, self.repository.owner_scope))
            return cursor.fetchall()

    def detail(self, package_id):
        with self._transaction('read') as cursor:
            package = self._package(cursor, package_id)
            expected_scope = self.repository.owner_scope or ('org' if self.repository.scope.org_id else 'platform')
            owned = ((expected_scope == 'personal' and package.scope_kind == 'personal'
                      and str(package.owner_user_id) == self.repository.scope.actor_user_id)
                     or (expected_scope == 'org' and package.scope_kind == 'org'
                         and str(package.org_id) == self.repository.scope.org_id)
                     or (expected_scope == 'platform' and package.scope_kind == 'platform'))
            draft = self._draft(cursor, package_id) if owned else None
            cursor.execute('''SELECT revision, summary, status, catalog_metadata, created_at
                FROM public.skill_revisions WHERE package_id = %s ORDER BY created_at DESC, id DESC''',
                (package_id,))
            revisions = cursor.fetchall()
            cursor.execute('''SELECT r.revision FROM public.skill_assignments a
                JOIN public.skill_revisions r ON r.id = a.revision_id AND r.status = 'published'
                WHERE a.package_id = %s AND a.org_id = %s::uuid AND a.enabled''',
                (package_id, self.repository.scope.org_id))
            available = cursor.fetchone()
            if package.scope_kind == 'personal':
                cursor.execute('''SELECT revision FROM public.skill_revisions
                    WHERE package_id = %s AND status = 'published'
                    ORDER BY created_at DESC, id DESC LIMIT 1''', (package_id,))
                personal_revision = cursor.fetchone()
                if personal_revision:
                    available = {'revision': personal_revision['revision']}
            # No internal storage paths/hashes are serialized through admin HTTP.
            if draft:
                draft = {key: draft[key] for key in (
                    'status', 'version', 'revision', 'content', 'approved_by', 'approved_at', 'updated_at')}
            return {'package_id': package.id, 'skill_key': package.skill_key,
                    'scope_kind': package.scope_kind, 'editable': owned,
                    'draft': draft, 'revisions': revisions,
                    'available_revision': available['revision'] if available else None}

    def deletion_check(self, package_id):
        from services.skills.deletion import check_deletion
        with self._transaction('deletion_check') as cursor:
            cursor.execute("SET LOCAL statement_timeout = '3s'")
            package = self._package(cursor, package_id, owned=True)
            draft = self._draft(cursor, package_id)
            return check_deletion(cursor, package, draft)

    def delete(self, package_id, data):
        from services.skills.deletion import check_deletion
        with self._transaction('delete') as cursor:
            cursor.execute("SET LOCAL statement_timeout = '3s'")
            cursor.execute("SET LOCAL lock_timeout = '2s'")
            package = self._package(cursor, package_id, owned=True)
            draft = self._draft(cursor, package_id)
            self._check_version(draft, data.expected_version)
            if not draft or draft['status'] != 'deprecated':
                raise SkillError('SKILL_TRANSITION_INVALID')
            # Same order as Actor checkpoint writers. No task/checkpoint state is
            # modified; bounded locks serialize the final check with their writes.
            cursor.execute('LOCK TABLE public.tasks, public.conversation_turn_checkpoints IN SHARE MODE')
            check = check_deletion(cursor, package, draft)
            if not check['allowed']:
                raise SkillError(check['reason'])
            cursor.execute("""UPDATE public.skill_drafts SET deleted_at = now(),
                deleted_by = %s, version = version + 1 WHERE package_id = %s""",
                (self.repository.scope.actor_user_id, package_id))
        return {'package_id': package_id, 'deleted': True}

    def read_revision(self, package_id, revision):
        with self._transaction('read') as cursor:
            package = self._package(cursor, package_id)
            cursor.execute('''SELECT * FROM public.skill_revisions WHERE package_id = %s AND revision = %s''',
                           (package_id, revision))
            row = cursor.fetchone()
            if not row:
                raise SkillError('SKILL_REVISION_UNAVAILABLE')
            saved = SkillRevision.model_validate(row)
            validated = self._storage().validate(package, PublishRevision(
                revision=saved.revision, content_sha256=saved.content_sha256,
                body_sha256=saved.body_sha256), nas_path=saved.nas_path)
            return RevisionContent(description=validated.summary, body=validated.body,
                                   catalog_metadata=validated.catalog_metadata,
                                   asset_summaries=public_assets(validated.resources),
                                   template_variables=validated.resources.template_variables)

    def save(self, package_id, data):
        with self._transaction('save') as cursor:
            self._package(cursor, package_id, owned=True)
            draft = self._draft(cursor, package_id)
            self._check_version(draft, data.expected_version)
            if not draft or draft['status'] != 'draft':
                raise SkillError('SKILL_TRANSITION_INVALID')
            cursor.execute('''UPDATE public.skill_drafts SET content = %s, revision = %s, version = version + 1
                WHERE package_id = %s''', (Jsonb(data.content.model_dump(mode='json')), 'v' + uuid4().hex, package_id))
        return self.detail(package_id)

    def transition(self, package_id, data):
        action = data.action
        with self._transaction(action) as cursor:
            package = self._package(cursor, package_id, owned=True)
            draft = self._draft(cursor, package_id)
            self._check_version(draft, data.expected_version)
            if action == 'start_draft':
                if draft and draft['status'] != 'published':
                    raise SkillError('SKILL_TRANSITION_INVALID')
                if draft:
                    cursor.execute('''UPDATE public.skill_drafts SET status = 'draft', revision = %s,
                        approved_by = NULL, approved_sha256 = NULL, approved_at = NULL, version = version + 1
                        WHERE package_id = %s''', ('v' + uuid4().hex, package_id))
                else:
                    cursor.execute('''SELECT * FROM public.skill_revisions WHERE package_id = %s
                        AND status = 'published' ORDER BY created_at DESC, id DESC LIMIT 1''', (package_id,))
                    row = cursor.fetchone()
                    content = DraftContent()
                    if row:
                        saved = SkillRevision.model_validate(row)
                        validated = self._storage().validate(package, PublishRevision(
                            revision=saved.revision, content_sha256=saved.content_sha256,
                            body_sha256=saved.body_sha256), nas_path=saved.nas_path)
                        texts = self._storage().read_assets(validated, [a.id for a in validated.resources.assets])
                        content = DraftContent(description=validated.summary, body=validated.body,
                            catalog_metadata=validated.catalog_metadata,
                            template_variables=validated.resources.template_variables,
                            assets=tuple(AssetDraft(**entry.model_dump(exclude={'path', 'sha256', 'bytes', 'source'}),
                                source=self._storage().read_source(validated, entry.id),
                                content=texts[entry.id]) for entry in validated.resources.assets))
                    self._insert_draft(cursor, package_id, content)
            elif action == 'enable':
                reenable(cursor, package, draft, self._storage)
            elif action == 'deprecate' and draft and draft['status'] == 'disabled':
                reenable(cursor, package, draft, self._storage, deprecating=True)
            elif action == 'publish_private':
                if package.scope_kind != 'personal' or not draft or draft['status'] != 'draft':
                    raise SkillError('SKILL_TRANSITION_INVALID')
                content = DraftContent.model_validate(draft['content'])
                publication, _ = reviewed_document(package, draft['revision'], content)
                cursor.execute("""UPDATE public.skill_drafts SET status = 'in_review', version = version + 1
                    WHERE package_id = %s""", (package_id,))
                cursor.execute("""UPDATE public.skill_drafts SET approved_by = %s, approved_sha256 = %s,
                    approved_at = now(), version = version + 1 WHERE package_id = %s""",
                    (self.repository.scope.actor_user_id, publication.content_sha256, package_id))
                draft = {**draft, 'status': 'in_review', 'approved_by': self.repository.scope.actor_user_id,
                         'approved_sha256': publication.content_sha256}
                self._review_or_publish(cursor, package, draft, 'publish')
            elif action in ('deprecate', 'disable'):
                if draft and draft['status'] in ('disabled', 'deprecated'):
                    raise SkillError('SKILL_TRANSITION_INVALID')
                if not draft:
                    draft = self._insert_draft(cursor, package_id, DraftContent())
                status = 'deprecated' if action == 'deprecate' else 'disabled'
                cursor.execute('''UPDATE public.skill_revisions SET status = %s
                    WHERE package_id = %s AND (status = 'published' OR (%s = 'disabled' AND status = 'deprecated'))''',
                    (status, package_id, status))
                cursor.execute('''UPDATE public.skill_drafts SET status = %s, version = version + 1
                    WHERE package_id = %s''', (status, package_id))
            else:
                if not draft:
                    raise SkillError('SKILL_TRANSITION_INVALID')
                self._review_or_publish(cursor, package, draft, action)
        return self.detail(package_id)

    def _review_or_publish(self, cursor, package, draft, action):
        required = 'draft' if action == 'submit' else 'in_review'
        if draft['status'] != required:
            raise SkillError('SKILL_TRANSITION_INVALID')
        if action == 'reject':
            cursor.execute('''UPDATE public.skill_drafts SET status = 'draft', approved_by = NULL,
                approved_sha256 = NULL, approved_at = NULL, version = version + 1 WHERE package_id = %s''',
                (package.id,))
            return
        content = DraftContent.model_validate(draft['content'])
        publication, raw = reviewed_document(package, draft['revision'], content)
        if action == 'submit':
            cursor.execute("UPDATE public.skill_drafts SET status = 'in_review', version = version + 1 WHERE package_id = %s",
                           (package.id,))
        elif action == 'approve':
            if draft['approved_by'] is not None:
                raise SkillError('SKILL_TRANSITION_INVALID')
            cursor.execute('''UPDATE public.skill_drafts SET approved_by = %s, approved_sha256 = %s,
                approved_at = now(), version = version + 1 WHERE package_id = %s''',
                (self.repository.scope.actor_user_id, publication.content_sha256, package.id))
        elif action == 'publish':
            if not draft['approved_by'] or draft['approved_sha256'] != publication.content_sha256:
                raise SkillError('SKILL_APPROVAL_REQUIRED')
            validated = self._storage().publish(package, publication, raw,
                assets={a.id: a.content.encode('utf-8') for a in content.assets},
                sources={a.id: a.source.raw() for a in content.assets if a.source})
            cursor.execute('''INSERT INTO public.skill_revisions
                (package_id, revision, nas_path, content_sha256, body_sha256, summary, catalog_metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id''',
                (package.id, validated.revision, validated.nas_path, validated.content_sha256,
                 validated.body_sha256, validated.summary,
                 Jsonb(validated.catalog_metadata.model_dump(mode='json', exclude_unset=True))))
            revision_id = cursor.fetchone()['id']
            if package.org_id:
                cursor.execute('''INSERT INTO public.skill_assignments(org_id, package_id, revision_id, enabled)
                    VALUES (%s, %s, %s, true) ON CONFLICT (org_id, package_id) DO UPDATE
                    SET revision_id = EXCLUDED.revision_id, enabled = true''',
                    (package.org_id, package.id, revision_id))
            cursor.execute("UPDATE public.skill_drafts SET status = 'published', version = version + 1 WHERE package_id = %s",
                           (package.id,))
