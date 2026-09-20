"""Transactional Skill authoring; NAS is written before a release becomes visible."""

from contextlib import contextmanager
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from services.skills.authoring_contracts import DraftContent, reviewed_document
from services.skills.contracts import PublishRevision, SkillError, SkillPackage, SkillRevision
from services.skills.repository import SkillRepository
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
            cursor.execute("SELECT set_config('app.skill_action', %s, true)", (action,))
            yield cursor

    def _package(self, cursor, package_id, *, owned=False):
        # Serialize initial draft creation as well as later mutations without
        # granting UPDATE on the immutable package table.
        if owned:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           ('skill-authoring:' + str(package_id),))
        cursor.execute('''SELECT * FROM public.skill_packages WHERE id = %s
            AND (org_id IS NULL OR org_id = %s::uuid)''',
            (package_id, self.repository.scope.org_id))
        row = cursor.fetchone()
        if not row:
            raise SkillError('SKILL_PACKAGE_UNAVAILABLE')
        package = SkillPackage.model_validate(row)
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
                cursor.execute('''INSERT INTO public.skill_packages(skill_key, source, scope_kind, org_id)
                    VALUES (%s, 'admin', %s, %s) RETURNING *''',
                    (data.skill_key, 'org' if org else 'platform', org))
                package = SkillPackage.model_validate(cursor.fetchone())
                self._insert_draft(cursor, package.id, data.content)
                return {'package_id': package.id}
        except UniqueViolation:
            raise SkillError('SKILL_KEY_EXISTS') from None

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
                ar.revision AS available_revision,
                CASE WHEN ar.id IS NOT NULL THEN (SELECT count(*) FROM public.skill_revisions prior
                    WHERE prior.package_id = p.id AND (prior.created_at, prior.id) <= (ar.created_at, ar.id))
                    END AS available_revision_number,
                d.approved_by IS NOT NULL AS approved
                FROM public.skill_packages p
                LEFT JOIN public.skill_drafts d ON d.package_id = p.id
                LEFT JOIN LATERAL (SELECT revision, summary, status, catalog_metadata, created_at FROM public.skill_revisions
                    WHERE package_id = p.id ORDER BY created_at DESC, id DESC LIMIT 1) r ON true
                LEFT JOIN public.skill_assignments a ON a.package_id = p.id AND a.org_id = %s::uuid AND a.enabled
                LEFT JOIN public.skill_revisions ar ON ar.id = a.revision_id AND ar.status = 'published'
                WHERE p.org_id IS NULL OR p.org_id = %s::uuid ORDER BY p.skill_key, p.id''',
                (self.repository.scope.org_id, self.repository.scope.org_id))
            return cursor.fetchall()

    def detail(self, package_id):
        with self._transaction('read') as cursor:
            package = self._package(cursor, package_id)
            owned = (str(package.org_id) if package.org_id else None) == self.repository.scope.org_id
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
            # No internal storage paths/hashes are serialized through admin HTTP.
            if draft:
                draft = {key: draft[key] for key in (
                    'status', 'version', 'revision', 'content', 'approved_by', 'approved_at', 'updated_at')}
            return {'package_id': package.id, 'skill_key': package.skill_key,
                    'scope_kind': package.scope_kind, 'editable': owned,
                    'draft': draft, 'revisions': revisions,
                    'available_revision': available['revision'] if available else None}

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
            return DraftContent(description=validated.summary, body=validated.body,
                                catalog_metadata=validated.catalog_metadata)

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
                        content = DraftContent(description=validated.summary, body=validated.body,
                                               catalog_metadata=validated.catalog_metadata)
                    self._insert_draft(cursor, package_id, content)
            elif action in ('deprecate', 'disable'):
                if draft and (draft['status'] == 'disabled' or
                              (action == 'deprecate' and draft['status'] == 'deprecated')):
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
        publication, raw = reviewed_document(package, draft['revision'], DraftContent.model_validate(draft['content']))
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
            validated = self._storage().publish(package, publication, raw)
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
