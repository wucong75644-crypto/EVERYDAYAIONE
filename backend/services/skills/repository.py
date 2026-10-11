"""Transaction-scoped PostgreSQL repository, independent of chat/tool runtimes."""

from contextlib import contextmanager
from uuid import UUID

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from core.db_scope import DatabaseAccessKind, DatabaseScope, SET_DATABASE_SCOPE_SQL
from services.skills.contracts import (
    ActivationAuditCreate, PackageCreate, SkillAssignment, SkillError,
    SkillPackage, SkillRevision, ValidatedSkill,
)
from services.skills.resolver import SkillCandidate


class SkillRepository:
    """Internal service API. Callers supply a trusted, already authorized scope.

    Production uses get_db().pool; no new pool or credentials are created here.
    Explicit predicates also protect callers using a privileged migration role.
    """

    def __init__(self, pool, scope: DatabaseScope, *, owner_scope: str | None = None):
        self._pool = pool
        self.scope = scope
        self.owner_scope = owner_scope

    @contextmanager
    def _cursor(self):
        with self._pool.connection() as connection:
            with connection.transaction():
                with connection.cursor(row_factory=dict_row) as cursor:
                    cursor.execute(SET_DATABASE_SCOPE_SQL, self.scope.settings)
                    yield cursor

    @staticmethod
    def lock_package_write(cursor, package_id):
        # Same order for authoring and internal revision/assignment writers:
        # package advisory lock, then draft, then revision/assignment rows.
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                       ('skill-authoring:' + str(package_id),))

    def _require_admin(self):
        if self.scope.access_kind != DatabaseAccessKind.RUNTIME_ADMIN:
            raise SkillError("SKILL_CONTROL_ACCESS_REQUIRED")

    def _require_org(self):
        if self.scope.org_id is None:
            raise SkillError("SKILL_ORG_REQUIRED")
        return self.scope.org_id

    def _require_owner(self, package: SkillPackage):
        self._require_admin()
        owner_scope = self.owner_scope or ("org" if self.scope.org_id else "platform")
        if owner_scope == "personal" and (
            package.scope_kind != "personal" or str(package.owner_user_id) != self.scope.actor_user_id
        ):
            raise SkillError("SKILL_OWNER_SCOPE_MISMATCH")
        if owner_scope == "org" and (
            package.scope_kind != "org" or str(package.org_id) != self.scope.org_id
        ):
            raise SkillError("SKILL_OWNER_SCOPE_MISMATCH")
        if owner_scope == "platform" and package.scope_kind != "platform":
            raise SkillError("SKILL_OWNER_SCOPE_MISMATCH")

    def create_package(self, package: PackageCreate) -> SkillPackage:
        self._require_admin()
        owner_scope = self.owner_scope or ("org" if self.scope.org_id else "platform")
        if owner_scope == "personal" and (
            package.scope_kind != "personal" or str(package.owner_user_id) != self.scope.actor_user_id
        ):
            raise SkillError("SKILL_OWNER_SCOPE_MISMATCH")
        if owner_scope == "org" and (
            package.scope_kind != "org" or str(package.org_id) != self.scope.org_id
        ):
            raise SkillError("SKILL_OWNER_SCOPE_MISMATCH")
        if owner_scope == "platform" and package.scope_kind != "platform":
            raise SkillError("SKILL_OWNER_SCOPE_MISMATCH")
        with self._cursor() as cursor:
            cursor.execute("""INSERT INTO public.skill_packages(skill_key, source, scope_kind, org_id, owner_user_id)
                VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                (package.skill_key, package.source, package.scope_kind, package.org_id, package.owner_user_id))
            return SkillPackage.model_validate(cursor.fetchone())

    def get_package(self, package_id: UUID) -> SkillPackage:
        with self._cursor() as cursor:
            cursor.execute("""SELECT * FROM public.skill_packages
                WHERE id = %s AND (scope_kind = 'platform'
                    OR (scope_kind = 'org' AND org_id = %s::uuid)
                    OR (scope_kind = 'personal' AND owner_user_id = %s::uuid))""",
                (package_id, self.scope.org_id, self.scope.actor_user_id))
            row = cursor.fetchone()
        if row is None:
            raise SkillError("SKILL_PACKAGE_UNAVAILABLE")
        return SkillPackage.model_validate(row)

    def list_packages(self) -> list[SkillPackage]:
        with self._cursor() as cursor:
            cursor.execute("""SELECT * FROM public.skill_packages
                WHERE scope_kind = 'platform'
                    OR (scope_kind = 'org' AND org_id = %s::uuid)
                    OR (scope_kind = 'personal' AND owner_user_id = %s::uuid)
                ORDER BY skill_key, id""", (self.scope.org_id, self.scope.actor_user_id))
            return [SkillPackage.model_validate(row) for row in cursor.fetchall()]

    def get_owned_package(self, package_id: UUID) -> SkillPackage:
        package = self.get_package(package_id)
        self._require_owner(package)
        return package

    def publish_revision(self, package_id: UUID, validated: ValidatedSkill) -> SkillRevision:
        package = self.get_owned_package(package_id)
        if validated.skill_key != package.skill_key:
            raise SkillError("SKILL_FRONTMATTER_IDENTITY_MISMATCH")
        try:
            with self._cursor() as cursor:
                self.lock_package_write(cursor, package_id)
                cursor.execute("""INSERT INTO public.skill_revisions
                    (package_id, revision, nas_path, content_sha256, body_sha256, summary, catalog_metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                    (package.id, validated.revision, validated.nas_path,
                     validated.content_sha256, validated.body_sha256, validated.summary,
                     Jsonb(validated.catalog_metadata.model_dump(mode="json", exclude_unset=True))))
                return SkillRevision.model_validate(cursor.fetchone())
        except UniqueViolation as error:
            raise SkillError("SKILL_REVISION_ALREADY_PUBLISHED") from error

    def retire_revision(self, package_id: UUID, revision_id: UUID) -> SkillRevision:
        self.get_owned_package(package_id)
        with self._cursor() as cursor:
            self.lock_package_write(cursor, package_id)
            cursor.execute("""UPDATE public.skill_revisions SET status = 'retired'
                WHERE package_id = %s AND id = %s RETURNING *""", (package_id, revision_id))
            row = cursor.fetchone()
        if row is None:
            raise SkillError("SKILL_REVISION_UNAVAILABLE")
        return SkillRevision.model_validate(row)

    def set_assignment(
        self, package_id: UUID, revision_id: UUID, *, enabled: bool = False, priority: int = 0,
    ) -> SkillAssignment:
        self._require_admin()
        org_id = self._require_org()
        package = self.get_package(package_id)
        if type(enabled) is not bool or type(priority) is not int or not -(2**31) <= priority < 2**31:
            raise SkillError("SKILL_ASSIGNMENT_INVALID")
        with self._cursor() as cursor:
            cursor.execute("SELECT set_config('app.skill_action', %s, true)",
                           ('platform_assign' if package.scope_kind == 'platform' else 'assign',))
            self.lock_package_write(cursor, package_id)
            cursor.execute("""INSERT INTO public.skill_assignments
                (org_id, package_id, revision_id, enabled, priority) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (org_id, package_id) DO UPDATE SET
                    revision_id = EXCLUDED.revision_id, enabled = EXCLUDED.enabled,
                    priority = EXCLUDED.priority, updated_at = now()
                RETURNING *""", (org_id, package_id, revision_id, enabled, priority))
            return SkillAssignment.model_validate(cursor.fetchone())

    def enabled_revisions(self, package_id: UUID | None = None) -> list[SkillRevision]:
        org_id = self._require_org()
        with self._cursor() as cursor:
            cursor.execute("""SELECT r.* FROM public.skill_assignments a
                JOIN public.skill_revisions r ON r.id = a.revision_id AND r.package_id = a.package_id
                JOIN public.skill_packages p ON p.id = a.package_id
                WHERE a.org_id = %s AND a.enabled AND r.status = 'published'
                    AND (p.org_id IS NULL OR p.org_id = a.org_id)
                    AND (%s::uuid IS NULL OR p.id = %s::uuid)
                ORDER BY a.priority DESC, p.skill_key, p.id""", (org_id, package_id, package_id))
            return [SkillRevision.model_validate(row) for row in cursor.fetchall()]

    def set_platform_assignment(self, package_id: UUID, revision_id: UUID, *, enabled: bool = False,
                                priority: int = 0) -> SkillAssignment:
        """Explicit platform-wide grant; ordinary organization writers cannot use it."""
        self._require_admin()
        if self.scope.org_id is not None or self.owner_scope != "platform":
            raise SkillError("SKILL_PLATFORM_CONTROL_REQUIRED")
        self.get_owned_package(package_id)
        if type(enabled) is not bool or type(priority) is not int or not -(2**31) <= priority < 2**31:
            raise SkillError("SKILL_ASSIGNMENT_INVALID")
        with self._cursor() as cursor:
            # A human actor must still be an active platform administrator. A
            # trusted server operator uses a null actor and an audited request ID.
            if self.scope.actor_user_id:
                cursor.execute("SELECT 1 FROM public.users WHERE id=%s::uuid AND status='active' AND role='super_admin'",
                               (self.scope.actor_user_id,))
                if not cursor.fetchone():
                    raise SkillError("SKILL_PLATFORM_CONTROL_REQUIRED")
            elif not self.scope.request_id:
                raise SkillError("SKILL_PLATFORM_CONTROL_REQUIRED")
            cursor.execute("SELECT set_config('app.skill_action', 'platform_assign', true)")
            self.lock_package_write(cursor, package_id)
            cursor.execute("""INSERT INTO public.skill_assignments
                (org_id,package_id,revision_id,enabled,priority) VALUES (NULL,%s,%s,%s,%s)
                ON CONFLICT (package_id) WHERE org_id IS NULL DO UPDATE SET
                    revision_id=EXCLUDED.revision_id,enabled=EXCLUDED.enabled,
                    priority=EXCLUDED.priority,updated_at=now() RETURNING *""",
                (package_id, revision_id, enabled, priority))
            return SkillAssignment.model_validate(cursor.fetchone())

    def record_activation(self, audit: ActivationAuditCreate) -> UUID:
        """Reserved explicit writer; never automatically called by this phase."""
        self._require_admin()
        org_id = self._require_org()
        self.get_package(audit.package_id)
        with self._cursor() as cursor:
            cursor.execute("""INSERT INTO public.skill_activation_audits
                (org_id, package_id, revision_id, actor_user_id, conversation_id, turn_id,
                 request_id, outcome, reason_code) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""", (org_id, audit.package_id, audit.revision_id,
                    self.scope.actor_user_id, audit.conversation_id, audit.turn_id,
                    self.scope.request_id, audit.outcome, audit.reason_code))
            return cursor.fetchone()["id"]

    def assigned_revision(self, package_id: UUID, revision: str, *, restoring: bool = False) -> SkillRevision:
        """Read an exact revision of an enabled package, never its latest version.

        Assignment changes may select a newer revision for new turns; existing
        turns may restore a deprecated revision, but still need an enabled grant.
        New activations (even from an older directory) require published status.
        """
        org_id = self.scope.org_id
        with self._cursor() as cursor:
            cursor.execute("""SELECT r.* FROM public.skill_revisions r
                JOIN public.skill_packages p ON p.id = r.package_id
                LEFT JOIN LATERAL (SELECT * FROM public.skill_assignments
                    WHERE package_id=p.id AND (org_id=%s::uuid OR org_id IS NULL)
                    ORDER BY org_id NULLS LAST LIMIT 1) a ON true
                WHERE p.id = %s AND r.revision = %s
                    AND (r.status = 'published' OR (%s AND r.status = 'deprecated'))
                    AND ((p.scope_kind = 'personal' AND p.owner_user_id = %s::uuid)
                         OR (p.scope_kind = 'org' AND p.org_id = %s::uuid AND a.enabled)
                         OR (p.scope_kind = 'platform' AND a.enabled))""",
                (org_id, package_id, revision, restoring, self.scope.actor_user_id, org_id))
            row = cursor.fetchone()
        if row is None:
            raise SkillError("SKILL_PINNED_REVISION_UNAVAILABLE")
        return SkillRevision.model_validate(row)

    def scheduled_candidates(self, skill_key: str, revision: str) -> list[SkillCandidate]:
        """Scheduled configuration requires the existing review attestation."""
        return self.pinned_candidates(skill_key, revision, require_reviewed=True)

    def pinned_candidates(self, skill_key: str, revision: str, *, require_reviewed: bool = False) -> list[SkillCandidate]:
        """Exact published revision under a current grant; no latest substitution."""
        with self._cursor() as cursor:
            org_id = self.scope.org_id
            cursor.execute("""SELECT p.id AS package_id,p.skill_key,p.org_id AS package_org_id,
                    p.owner_user_id AS package_user_id,p.scope_kind,
                    a.org_id AS assignment_org_id,
                    (p.scope_kind='platform' AND a.org_id IS NULL AND a.package_id IS NOT NULL) AS global_assignment,
                    COALESCE(a.priority, 0) AS priority,
                    r.revision,r.summary AS description,r.catalog_metadata
                FROM public.skill_packages p JOIN public.skill_revisions r ON r.package_id=p.id
                LEFT JOIN LATERAL (SELECT * FROM public.skill_assignments
                    WHERE package_id=p.id AND (org_id=%s::uuid OR org_id IS NULL)
                    ORDER BY org_id NULLS LAST LIMIT 1) a ON true
                WHERE p.skill_key=%s AND r.revision=%s AND r.status='published'
                  AND ((p.scope_kind='personal' AND p.owner_user_id=%s::uuid)
                       OR (p.scope_kind IN ('org','platform') AND a.enabled
                           AND (p.scope_kind='platform' OR p.org_id=a.org_id)))
                """ + (' AND r.reviewed' if require_reviewed else ''),
                (org_id, skill_key, revision, self.scope.actor_user_id))
            return [SkillCandidate.model_validate(row) for row in cursor.fetchall()]

    def catalog_candidates(self) -> list[SkillCandidate]:
        """Only enabled, pinned, published revisions; never fetch content or paths."""
        with self._cursor() as cursor:
            cursor.execute("""SELECT p.id AS package_id, p.skill_key,
                    p.org_id AS package_org_id,p.owner_user_id AS package_user_id,p.scope_kind,
                    a.org_id AS assignment_org_id,
                    (p.scope_kind='platform' AND a.org_id IS NULL AND a.package_id IS NOT NULL) AS global_assignment,
                    COALESCE(a.priority,0) AS priority,
                    r.revision, r.summary AS description, r.catalog_metadata
                FROM public.skill_packages p
                JOIN public.skill_revisions r ON r.package_id=p.id AND r.status='published'
                LEFT JOIN LATERAL (SELECT * FROM public.skill_assignments
                    WHERE package_id=p.id AND (org_id=%s::uuid OR org_id IS NULL)
                    ORDER BY org_id NULLS LAST LIMIT 1) a ON true
                WHERE (p.scope_kind='personal' AND p.owner_user_id=%s::uuid
                       AND r.id=(SELECT latest.id FROM public.skill_revisions latest
                           WHERE latest.package_id=p.id AND latest.status='published'
                           ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1))
                   OR (p.scope_kind IN ('org','platform') AND a.enabled AND a.revision_id=r.id
                       AND (p.scope_kind='platform' OR p.org_id=a.org_id))""",
                (self.scope.org_id,self.scope.actor_user_id))
            try:
                return [SkillCandidate.model_validate(row) for row in cursor.fetchall()]
            except ValidationError:
                # ValidationError includes raw field values; keep logs secret-free.
                raise SkillError("SKILL_CATALOG_METADATA_INVALID") from None
